"""Registry of the ten bots. Each lives in its own module with its own logic."""
from __future__ import annotations

from typing import Any, Optional

from .adaptive import AdaptiveAIScalper
from .base import MarketData, Strategy
from .bollinger_squeeze import BollingerSqueeze
from .breakout_hunter import BreakoutHunter
from .ema_momentum import EMAMomentum
from .liquidity_hunter import LiquidityHunter
from .order_flow import OrderFlowIntelligence
from .range_expansion import RangeExpansionBreakout
from .rsi_divergence import RSIDivergence
from .supertrend_momentum import SupertrendMomentum
from .vwap_scalper import VWAPScalper

BASE_CLASSES = [VWAPScalper, EMAMomentum, OrderFlowIntelligence, BreakoutHunter, BollingerSqueeze,
                RSIDivergence, LiquidityHunter, SupertrendMomentum, RangeExpansionBreakout]
ALL_IDS = [c.bot_id for c in BASE_CLASSES] + [AdaptiveAIScalper.bot_id]


def build_bots(stats=None, params: Optional[dict[str, dict[str, Any]]] = None) -> list[Strategy]:
    params = params or {}
    base = [c(params.get(c.bot_id)) for c in BASE_CLASSES]
    adaptive = AdaptiveAIScalper(params.get(AdaptiveAIScalper.bot_id), members=base, stats=stats)
    return base + [adaptive]


__all__ = ["build_bots", "BASE_CLASSES", "ALL_IDS", "MarketData", "Strategy"]
