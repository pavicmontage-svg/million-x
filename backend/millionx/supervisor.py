"""Central AI Supervisor (deterministic core of Strategy Intelligence).

Collects the signals of all ten bots, scores them, removes redundant and
conflicting ones, discounts correlated strategies, and decides which (if any)
are forwarded to the independent risk manager. A bot can never open a trade
because *another* bot traded: every candidate must be the bot's own signal.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from .competition import CompetitionEngine
from .models import BotStatus, Signal, Trade
from .regime import regime_key


@dataclass
class Verdict:
    signal: Signal
    accepted: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    size_multiplier: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"signal": self.signal.to_dict(), "accepted": self.accepted, "score": self.score,
                "reasons": self.reasons, "size_multiplier": self.size_multiplier}


class Supervisor:
    def __init__(self, competition: CompetitionEngine, min_score: float = 0.3,
                 corr_window: int = 200, coincide_ms: int = 15 * 60_000,
                 tradable_statuses: tuple = (BotStatus.LIVE_ELIGIBLE,)):
        self.comp = competition
        self.min_score = min_score
        self.coincide_ms = coincide_ms
        self.tradable = set(tradable_statuses)
        self.status: dict[str, BotStatus] = {b: BotStatus.RESEARCH for b in competition.books}
        self.status_reason: dict[str, str] = {}
        self._hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=corr_window))
        self._recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
        self.decisions: deque = deque(maxlen=500)

    # ---- bot lifecycle ---------------------------------------------------
    def set_status(self, bot_id: str, st: BotStatus, reason: str = "") -> None:
        self.status[bot_id] = st
        self.status_reason[bot_id] = reason

    def on_trade(self, t: Trade) -> Optional[str]:
        """Health check after every closed (real or virtual) trade."""
        dq = self._recent[t.bot_id]
        dq.append(t)
        st = self.status.get(t.bot_id)
        last = list(dq)[-40:]
        n = len(last)
        if n < 20:
            return None
        gp = sum(x.net_pnl for x in last if x.net_pnl > 0)
        gl = -sum(x.net_pnl for x in last if x.net_pnl <= 0)
        pf = gp / gl if gl > 0 else float("inf")
        exp_r = float(np.mean([x.r_multiple for x in last]))
        streak = 0
        for x in reversed(last):
            if x.net_pnl > 0:
                break
            streak += 1
        if st not in (BotStatus.SUSPENDED, BotStatus.DISABLED):
            if pf < 0.8 or exp_r < -0.15 or streak >= 6:
                self.set_status(t.bot_id, BotStatus.SUSPENDED,
                                f"degraded: PF={pf:.2f} expR={exp_r:.2f} streak={streak} (last {n})")
                return "suspended"
        elif st == BotStatus.SUSPENDED:
            last20 = last[-20:]
            gp2 = sum(x.net_pnl for x in last20 if x.net_pnl > 0)
            gl2 = -sum(x.net_pnl for x in last20 if x.net_pnl <= 0)
            if gl2 > 0 and gp2 / gl2 >= 1.1:
                self.set_status(t.bot_id, BotStatus.PAPER, "recovered in shadow trading; re-validate before live")
                return "recovered_to_paper"
        return None

    # ---- signal correlation ---------------------------------------------
    def _record(self, s: Signal) -> None:
        self._hist[s.bot_id].append((s.bar_time, s.symbol, int(s.side)))

    def signal_correlation(self, a: str, b: str) -> float:
        """Share of bot a's recent signals that coincide (same symbol, side,
        within coincide_ms) with a signal of bot b."""
        ha, hb = self._hist.get(a), self._hist.get(b)
        if not ha or not hb or len(ha) < 10:
            return 0.0
        hits = 0
        for t, sym, sd in ha:
            if any(sym == s2 and sd == d2 and abs(t - t2) <= self.coincide_ms for t2, s2, d2 in hb):
                hits += 1
        return hits / len(ha)

    def correlation_matrix(self) -> dict[str, dict[str, float]]:
        ids = list(self.comp.books)
        return {a: {b: (1.0 if a == b else round(self.signal_correlation(a, b), 3)) for b in ids} for a in ids}

    # ---- main decision ---------------------------------------------------
    def review(self, signals: list[Signal], regime_of: Callable[[str], tuple[str, str]],
               open_symbols: dict[str, int], price_corr: Optional[Callable[[str, str], float]] = None,
               enforce_status: bool = True) -> list[Verdict]:
        verdicts: list[Verdict] = []
        for s in signals:
            self._record(s)
        by_symbol: dict[str, list[Signal]] = defaultdict(list)
        for s in signals:
            by_symbol[s.symbol].append(s)
        for sym, group in by_symbol.items():
            sides = {int(s.side) for s in group}
            if len(sides) > 1:
                for s in group:
                    verdicts.append(Verdict(s, False, 0.0, ["conflicting_directions_same_symbol"]))
                continue
            scored = []
            for s in group:
                reasons = []
                st = self.status.get(s.bot_id, BotStatus.RESEARCH)
                if enforce_status and st not in self.tradable:
                    reasons.append(f"bot_status_{st.value}")
                trend, vol = regime_of(sym)
                rk = regime_key(trend, vol)
                rs = self.comp.regimes.get(rk, s.features.get("source_bot", s.bot_id)) if s.features else \
                    self.comp.regimes.get(rk, s.bot_id)
                fit = 1.0 if rs["n"] < 10 else float(np.clip((rs["pf"] if math.isfinite(rs["pf"]) else 2.0) / 1.2, 0.5, 1.5))
                score = s.confidence * self.comp.weight(s.bot_id) * fit
                if sym in open_symbols:
                    reasons.append("position_already_open_on_symbol")
                if score < self.min_score:
                    reasons.append(f"score {score:.2f} < {self.min_score}")
                scored.append((score, s, reasons))
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best, best_reasons = scored[0]
            # agreement bonus only from weakly-correlated independent bots
            bonus = 0.0
            for sc, s2, r2 in scored[1:]:
                if self.signal_correlation(best.bot_id, s2.bot_id) < 0.5:
                    bonus += 0.1
            best_score *= 1 + min(0.3, bonus)
            mult, notes = 1.0, []
            if price_corr is not None:
                for osym, oside in open_symbols.items():
                    if oside == int(best.side) and price_corr(sym, osym) > 0.85:
                        mult = 0.5
                        notes.append(f"size halved: correlated with open {osym}")
            best.score = best_score
            verdicts.append(Verdict(best, not best_reasons, best_score,
                                    (best_reasons or ["selected"]) + notes, mult))
            for sc, s2, r2 in scored[1:]:
                s2.score = sc
                verdicts.append(Verdict(s2, False, sc, (r2 or []) + [f"redundant_with_{best.bot_id}"]))
        for v in verdicts:
            self.comp.on_signal(v.signal.bot_id, v.accepted)
            self.decisions.append(v)
        verdicts.sort(key=lambda v: v.score, reverse=True)
        return verdicts
