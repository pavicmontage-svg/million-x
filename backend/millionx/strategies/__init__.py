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


# 2.0 research strategies (versioned v2 bots and prototypes A-D). They are built only when a
# research run asks for them by id; the live/paper engine never includes them.
RESEARCH_CLASSES: list[type] = []


def register_research(cls: type) -> type:
    if cls not in RESEARCH_CLASSES:
        RESEARCH_CLASSES.append(cls)
    return cls


def build_bots(stats=None, params: Optional[dict[str, dict[str, Any]]] = None,
               include: Optional[list[str]] = None) -> list[Strategy]:
    params = params or {}
    base = [c(params.get(c.bot_id)) for c in BASE_CLASSES]
    adaptive = AdaptiveAIScalper(params.get(AdaptiveAIScalper.bot_id), members=base, stats=stats)
    extra = [c(params.get(c.bot_id)) for c in RESEARCH_CLASSES if include and c.bot_id in include]
    return base + [adaptive] + extra


__all__ = ["build_bots", "BASE_CLASSES", "ALL_IDS", "MarketData", "Strategy"]

from . import research_protos  # noqa: E402,F401  (registers research-only prototypes)
