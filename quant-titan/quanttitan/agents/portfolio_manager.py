"""AGENT 4 — Portfolio Manager: correlation, concentration and allocation across simultaneous signals."""
from __future__ import annotations

import itertools

import numpy as np

from ..config import AppConfig
from ..core.types import Decision, Outcome
from ..data.candles import TF_MS, Candles, resample
from ..risk.portfolio import PortfolioState


def correlation_matrix(candles: dict[str, Candles], tf: str = "1h", window: int = 168) -> dict[tuple[str, str], float]:
    rets: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for s, c in candles.items():
        h = c if TF_MS[c.timeframe] >= TF_MS[tf] else resample(c, tf)  # already at/above the correlation timeframe
        if len(h) < 30:
            continue
        h = h.tail(window + 1)
        rets[s] = (h.ts[1:], np.diff(np.log(h.close)))
    out: dict[tuple[str, str], float] = {}
    for a, b in itertools.combinations(sorted(rets), 2):
        ta, ra = rets[a]; tb, rb = rets[b]
        common, ia, ib = np.intersect1d(ta, tb, return_indices=True)
        if len(common) < 20:
            continue
        x, y = ra[ia], rb[ib]
        if np.std(x) == 0 or np.std(y) == 0:
            continue
        out[(a, b)] = float(np.corrcoef(x, y)[0, 1])
    return out


class PortfolioManager:
    name = "portfolio_manager"
    title = "Portfolio Manager"
    responsibilities = ["Portfolio allocation", "Correlation evaluation", "Aggregate exposure control",
                        "Avoid concentration", "Rank simultaneous signals", "Allocate risk within approved limits"]

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.last: dict = {}

    def rank(self, proposals: list[Decision], pf: PortfolioState) -> list[Decision]:
        """Order approved proposals by net edge then confidence and drop those that duplicate
        exposure already proposed in this batch (same direction, correlation ≥ threshold)."""
        thr = self.cfg.risk.correlation_threshold
        approved = [d for d in proposals if d.outcome in (Outcome.APPROVE_LONG, Outcome.APPROVE_SHORT)]
        approved.sort(key=lambda d: (d.net_edge_r, d.confidence), reverse=True)
        kept: list[Decision] = []
        for d in approved:
            dup = next((k for k in kept if pf.corr(k.symbol, d.symbol) * (1 if k.direction is d.direction else -1) >= thr), None)
            if dup is not None:
                d.outcome = Outcome.REJECT
                d.reasons.append(f"portfolio: duplicates {dup.direction.value} {dup.symbol} exposure "
                                 f"(corr {pf.corr(dup.symbol, d.symbol):.2f}) — kept the higher-ranked signal")
                continue
            kept.append(d)
        self.last = {"agent": self.name, "proposals": len(proposals), "approved_in": len(approved), "kept": len(kept),
                     "ranking": [{"symbol": d.symbol, "engine": d.engine, "dir": d.direction.value,
                                  "net_edge": round(d.net_edge_r, 4), "conf": d.confidence} for d in kept]}
        return kept

    def report(self, pf: PortfolioState) -> dict:
        eq = pf.equity or 1.0
        by_symbol: dict[str, float] = {}
        long_n = short_n = 0.0
        for p in pf.positions:
            n = p.notional(pf.prices.get(p.symbol, p.entry_price))
            by_symbol[p.symbol] = by_symbol.get(p.symbol, 0.0) + n
            if p.direction.sign > 0:
                long_n += n
            else:
                short_n += n
        top = sorted(pf.correlations.items(), key=lambda kv: -abs(kv[1]))[:10]
        return {"agent": self.name, "exposure_pct": {k: round(v / eq * 100, 2) for k, v in by_symbol.items()},
                "net_exposure_pct": round((long_n - short_n) / eq * 100, 2),
                "gross_exposure_pct": round((long_n + short_n) / eq * 100, 2),
                "top_correlations": [{"pair": f"{a}/{b}", "corr": round(c, 3)} for (a, b), c in top],
                "last_allocation": self.last}
