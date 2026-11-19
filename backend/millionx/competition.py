"""Strategy Competition Engine.

Every bot trades *virtually* (shadow positions) on every one of its signals,
whether or not the supervisor sends a real order. This gives each bot an
unbiased track record per regime. Once per day the engine:

  1. compares results and signal quality,
  2. measures risk (drawdown of the virtual book),
  3. proposes new weights (risk-adjusted score, shrunk toward equal weight
     when the sample is small),
  4. validates the proposal in simulation by replaying the last window of
     shadow trades with old vs new weights,
  5. applies it only if it is not worse; otherwise keeps the old weights.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .backtest import metrics
from .models import Trade


class RegimeStats:
    """Per (regime, bot) running statistics from CLOSED trades only."""

    def __init__(self):
        self._d: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"n": 0, "gp": 0.0, "gl": 0.0, "sum_r": 0.0, "wins": 0})

    def record(self, regime_key: str, bot_id: str, net_pnl: float, r: float) -> None:
        s = self._d[(regime_key, bot_id)]
        s["n"] += 1
        s["sum_r"] += r
        if net_pnl > 0:
            s["gp"] += net_pnl
            s["wins"] += 1
        else:
            s["gl"] += -net_pnl

    def get(self, regime_key: str, bot_id: str) -> dict[str, float]:
        s = self._d.get((regime_key, bot_id), {"n": 0, "gp": 0.0, "gl": 0.0, "sum_r": 0.0, "wins": 0})
        pf = s["gp"] / s["gl"] if s["gl"] > 0 else (float("inf") if s["gp"] > 0 else 0.0)
        return {"n": int(s["n"]), "pf": pf, "exp_r": s["sum_r"] / s["n"] if s["n"] else 0.0,
                "win_rate": s["wins"] / s["n"] if s["n"] else 0.0}

    def best(self, regime_key: str, candidates: list[str], min_trades: int, min_pf: float,
             min_exp_r: float) -> Optional[str]:
        best_id, best_score = None, -1e9
        for b in candidates:
            st = self.get(regime_key, b)
            if st["n"] < min_trades or st["pf"] < min_pf or st["exp_r"] < min_exp_r:
                continue
            score = st["exp_r"] * math.sqrt(st["n"])
            if score > best_score:
                best_id, best_score = b, score
        return best_id

    def table(self) -> list[dict[str, Any]]:
        rows = []
        for (rk, bot), _ in sorted(self._d.items()):
            st = self.get(rk, bot)
            rows.append({"regime": rk, "bot_id": bot, **st})
        return rows


@dataclass
class BotBook:
    bot_id: str
    virtual_equity: float = 100.0
    trades: list[Trade] = field(default_factory=list)
    weight: float = 1.0
    signals: int = 0
    approved: int = 0

    def record(self, t: Trade) -> None:
        self.trades.append(t)
        # virtual book compounds the R result at a fixed 0.5 % risk so bots are comparable
        self.virtual_equity *= (1 + 0.005 * t.r_multiple)


class CompetitionEngine:
    def __init__(self, bot_ids: list[str], min_weight: float = 0.25, max_weight: float = 2.0,
                 prior_trades: int = 30, window_days: int = 14):
        self.books = {b: BotBook(b) for b in bot_ids}
        self.regimes = RegimeStats()
        self.min_w, self.max_w = min_weight, max_weight
        self.prior = prior_trades
        self.window_ms = window_days * 86_400_000
        self.history: list[dict[str, Any]] = []

    def on_signal(self, bot_id: str, approved: bool) -> None:
        bk = self.books.get(bot_id)
        if bk:
            bk.signals += 1
            bk.approved += int(approved)

    def on_virtual_trade(self, t: Trade) -> None:
        bk = self.books.get(t.bot_id)
        if bk:
            bk.record(t)
        self.regimes.record(t.regime, t.bot_id, t.net_pnl, t.r_multiple)

    def weight(self, bot_id: str) -> float:
        bk = self.books.get(bot_id)
        return bk.weight if bk else 1.0

    # ---- daily cycle -----------------------------------------------------
    def _score(self, trades: list[Trade]) -> float:
        if not trades:
            return 0.0
        r = np.array([t.r_multiple for t in trades])
        eq = np.cumprod(1 + 0.005 * r)
        dd = metrics.max_drawdown(np.concatenate([[1.0], eq]))
        return float(r.mean() * math.sqrt(len(r)) - 2.0 * dd)

    def propose(self, now_ms: int) -> dict[str, float]:
        raw = {}
        for b, bk in self.books.items():
            recent = [t for t in bk.trades if t.closed_at >= now_ms - self.window_ms]
            n = len(recent)
            s = self._score(recent)
            shrink = n / (n + self.prior)          # small samples stay near 1.0
            raw[b] = 1.0 + shrink * math.tanh(s / 3)
        return {b: float(min(self.max_w, max(self.min_w, w))) for b, w in raw.items()}

    def _replay(self, weights: dict[str, float], now_ms: int) -> float:
        """Weighted R of the recent window, net of a drawdown penalty."""
        pts = []
        for b, bk in self.books.items():
            for t in bk.trades:
                if t.closed_at >= now_ms - self.window_ms:
                    pts.append((t.closed_at, weights.get(b, 1.0) * t.r_multiple))
        if not pts:
            return 0.0
        pts.sort()
        r = np.array([p[1] for p in pts])
        eq = np.cumprod(1 + 0.005 * r / max(1.0, len(self.books) / 3))
        return float(eq[-1] - 1 - 0.5 * metrics.max_drawdown(np.concatenate([[1.0], eq])))

    def daily_cycle(self, now_ms: int) -> dict[str, Any]:
        cur = {b: bk.weight for b, bk in self.books.items()}
        prop = self.propose(now_ms)
        old_v, new_v = self._replay(cur, now_ms), self._replay(prop, now_ms)
        applied = new_v >= old_v
        if applied:
            for b, w in prop.items():
                self.books[b].weight = w
        rec = {"ts": now_ms, "current": cur, "proposed": prop, "sim_old": old_v, "sim_new": new_v,
               "applied": applied, "leaderboard": self.leaderboard(now_ms)}
        self.history.append(rec)
        return rec

    def leaderboard(self, now_ms: Optional[int] = None) -> list[dict[str, Any]]:
        rows = []
        for b, bk in self.books.items():
            m = metrics.compute(bk.trades, 100.0)
            rows.append({"bot_id": b, "weight": bk.weight, "virtual_equity": bk.virtual_equity,
                         "signals": bk.signals, "approved": bk.approved,
                         "trades": m["trades"], "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
                         "expectancy_r": m["expectancy_r"], "max_drawdown": m["max_drawdown"]})
        rows.sort(key=lambda r: (r["expectancy_r"] or -9) * math.sqrt(max(1, r["trades"])), reverse=True)
        return rows

    def best_by_regime(self) -> dict[str, Optional[str]]:
        keys = sorted({rk for rk, _ in self.regimes._d})
        return {k: self.regimes.best(k, list(self.books), 10, 1.0, 0.0) for k in keys}
