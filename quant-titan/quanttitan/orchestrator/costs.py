"""Transaction-cost model shared by the pre-trade edge check, the paper exchange and the backtester.

All costs are expressed per unit of position and converted into R (multiples of the planned risk
per unit, |entry − stop|), so they can be compared directly with a setup's expected R.

  fee       2 × taker fee × price                              (entry + exit, conservative: taker)
  spread    spread_bps × price                                  (half on entry, half on exit)
  slippage  2 × (base_bps + vol_coef × ATR% + impact_bps) × price
            impact_bps = impact_coef × sqrt(order notional / bar quote volume) × 1e4 × ATR%
  funding   |rate| × hold_hours / 8 × price, only when the position's side pays funding
"""
from __future__ import annotations

import math

from ..config import CostConfig
from ..core.types import CostEstimate, Direction


def slippage_bps(costs: CostConfig, price: float, atr: float, notional: float = 0.0, bar_quote_volume: float = 0.0) -> float:
    atr_pct = atr / price if price > 0 and atr > 0 else 0.0
    impact = 0.0
    if notional > 0 and bar_quote_volume > 0:
        impact = costs.impact_coef * math.sqrt(notional / bar_quote_volume) * atr_pct * 1e4
    return costs.base_slippage_bps + costs.vol_slippage_coef * atr_pct * 1e4 + impact


def estimate_costs(costs: CostConfig, *, direction: Direction, entry: float, stop: float, atr: float,
                   spread_bps: float | None = None, funding_rate: float = 0.0, hold_hours: float = 4.0,
                   notional: float = 0.0, bar_quote_volume: float = 0.0) -> CostEstimate:
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return CostEstimate(math.inf, math.inf, math.inf, math.inf)
    p = entry
    sp = spread_bps if spread_bps is not None and math.isfinite(spread_bps) else 1.0
    fee_r = 2 * costs.taker_fee * p / risk
    spread_r = sp / 1e4 * p / risk
    slip_r = 2 * slippage_bps(costs, p, atr, notional, bar_quote_volume) / 1e4 * p / risk
    pays = (direction is Direction.LONG and funding_rate > 0) or (direction is Direction.SHORT and funding_rate < 0)
    funding_r = abs(funding_rate) * hold_hours / 8.0 * p / risk if pays else 0.0
    return CostEstimate(fee_r, slip_r, spread_r, funding_r)
