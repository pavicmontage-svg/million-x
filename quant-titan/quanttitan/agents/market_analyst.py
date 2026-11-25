"""AGENT 2 — Market Analyst: aggregates per-symbol regime states into a market-wide view."""
from __future__ import annotations

from collections import Counter

import numpy as np

from ..core.types import Direction, Regime, RegimeState

UNSAFE = {Regime.UNSAFE_MARKET.value, Regime.MARKET_DISLOCATION.value}


class MarketAnalyst:
    name = "market_analyst"
    title = "Market Analyst"
    responsibilities = ["Analyse market regime", "Evaluate trend and structure", "Interpret volatility",
                        "Analyse liquidity", "Identify conditions unsuitable for trading"]

    def __init__(self, block_fraction: float = 0.5) -> None:
        self.block_fraction = block_fraction
        self.last: dict = {}

    def analyze(self, states: dict[str, RegimeState], avg_correlation: float | None = None,
                anchor: str = "BTCUSDT") -> dict:
        if not states:
            self.last = {"agent": self.name, "summary": "no market data", "block_all": False}
            return self.last
        counts = Counter(s.regime.value for s in states.values())
        n = len(states)
        up = sum(1 for s in states.values() if s.trend_direction is Direction.LONG)
        down = sum(1 for s in states.values() if s.trend_direction is Direction.SHORT)
        unsafe = sorted(s.symbol for s in states.values() if s.regime.value in UNSAFE)
        high_vol = sorted(s.symbol for s in states.values() if "HIGH_VOLATILITY" in s.flags or s.regime is Regime.HIGH_VOLATILITY)
        unsafe_frac = len(unsafe) / n
        block = unsafe_frac >= self.block_fraction
        anchor_state = states.get(anchor)
        breadth = (up - down) / n
        dominant = counts.most_common(1)[0][0]
        parts = [f"{n} instruments; dominant regime {dominant} ({counts[dominant]}/{n})",
                 f"breadth {breadth:+.2f} ({up} up / {down} down)"]
        if anchor_state:
            parts.append(f"{anchor}: {anchor_state.regime.value}")
        if avg_correlation is not None and np.isfinite(avg_correlation):
            parts.append(f"avg correlation {avg_correlation:.2f}" + (" — diversification low" if avg_correlation > 0.8 else ""))
        if unsafe:
            parts.append(f"unsafe: {', '.join(unsafe)}")
        if block:
            parts.append(f"{unsafe_frac:.0%} of the universe unsafe — recommending BLOCK ALL TRADING")
        self.last = {
            "agent": self.name, "summary": "; ".join(parts), "regime_counts": dict(counts), "breadth": round(breadth, 3),
            "unsafe_symbols": unsafe, "high_vol_symbols": high_vol, "avg_correlation": avg_correlation,
            "anchor": anchor_state.to_dict() if anchor_state else None, "block_all": block,
            "states": {k: v.to_dict() for k, v in states.items()},
        }
        return self.last
