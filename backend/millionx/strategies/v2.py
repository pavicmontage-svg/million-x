"""MillionX 2.0 — versioned v2 strategies (bot1..bot9).

Each v2 keeps the v1 entry logic UNCHANGED (v1 modules are not edited) and adds the gates
that the phase-1 forensic report (docs/STRATEGY_FORENSIC_REPORT.md) justified on mechanical
grounds, not by mining the sample:
  * execution on 15m / 60m (the plan tests both), because cost in R falls with timeframe;
  * cost gate: the strategy's own stop distance must be >= k x round-trip cost (k in a grid);
  * trend-following bots trade only with the closed 1h trend;
  * mean-reversion bots trade only when the 15m ADX is below a threshold and never against
    the closed 1h trend.
No one-sided (long/short) bias and no per-symbol exclusions are added.
They are registered as research strategies: the live/paper engine never trades them until the
Research Engine 2.0 verdict and the activation criteria are met and the owner activates them.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..models import Side
from . import register_research
from .bollinger_squeeze import BollingerSqueeze
from .breakout_hunter import BreakoutHunter
from .ema_momentum import EMAMomentum
from .liquidity_hunter import LiquidityHunter
from .order_flow import OrderFlowIntelligence
from .range_expansion import RangeExpansionBreakout
from .research_protos import _HTF
from .rsi_divergence import RSIDivergence
from .supertrend_momentum import SupertrendMomentum
from .vwap_scalper import VWAPScalper

ROUND_TRIP_COST = 2 * 0.0005 + 2 * 0.0002  # taker both sides + 2 bps slippage both sides (BASE scenario)
GATE_KEYS = ("min_cost_mult", "adx_max")


class _V2(_HTF):
    base_cls: type = None
    kind = "trend"  # or "mean_reversion"
    version = "2.0.0"

    def compute(self, b):
        inner_params = {k: v for k, v in self.params.items() if k not in GATE_KEYS}
        self.inner = self.base_cls(inner_params)
        self.inner.prepare(self.md)
        self.f["inner_ok"] = np.ones(1)
        self._htf()

    def evaluate(self, j):
        inner = getattr(self, "inner", None)
        if inner is None or len(inner.f) <= 1 or j < inner.warmup_bars:
            return None
        if np.isnan(inner.f["atr"][j]) or inner.f["atr"][j] <= 0:
            return None
        if self.base_cls is OrderFlowIntelligence and self.md.live and self.md.book is None:
            return None  # 2.0: a missing live order book is never treated as confirmation
        out = inner.evaluate(j)
        if out is None:
            return None
        side, stop, tp1, tp2, conf, reason = out
        entry = float(self.b.close[j])
        if abs(entry - stop) / entry < self.params["min_cost_mult"] * ROUND_TRIP_COST:
            return None
        t60 = self.f["t60"][j]
        if self.kind == "trend":
            if t60 != int(side):
                return None
        else:
            adx = self.f["adx15"][j]
            if np.isnan(adx) or adx >= self.params["adx_max"] or t60 == -int(side):
                return None
        return side, stop, tp1, tp2, conf, f"v2 {reason}"


def _make(base: type, kind: str, label: str = "") -> type:
    grid: dict[str, list[Any]] = {"min_cost_mult": [4.0, 6.0, 8.0]}
    defaults = dict(base.default_params, timeframe=15, min_cost_mult=6.0)
    if kind == "mean_reversion":
        grid["adx_max"] = [20.0, 25.0]
        defaults["adx_max"] = 25.0
    attrs = {"bot_id": base.bot_id + "_v2", "name": f"{base.name} v2{label}",
             "description": f"{base.description} | v2 gates: 15m/60m, cost >= k x round-trip, "
                            + ("with 1h trend" if kind == "trend" else "15m ADX below threshold, not against 1h trend"),
             "base_cls": base, "kind": kind, "default_params": defaults, "param_grid": grid,
             "warmup_bars": max(60, getattr(base, "warmup_bars", 60))}
    return register_research(type(base.__name__ + "V2", (_V2,), attrs))


VWAPScalperV2 = _make(VWAPScalper, "mean_reversion")
EMAMomentumV2 = _make(EMAMomentum, "trend")
OrderFlowV2 = _make(OrderFlowIntelligence, "trend", " (CVD-only in backtest: no historical order book)")
BreakoutHunterV2 = _make(BreakoutHunter, "trend")
BollingerSqueezeV2 = _make(BollingerSqueeze, "trend")
RSIDivergenceV2 = _make(RSIDivergence, "mean_reversion")
LiquidityHunterV2 = _make(LiquidityHunter, "mean_reversion")
SupertrendMomentumV2 = _make(SupertrendMomentum, "trend")
RangeExpansionV2 = _make(RangeExpansionBreakout, "trend")
