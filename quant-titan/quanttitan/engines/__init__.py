from ..config import AppConfig
from .base import Engine, MarketContext, MicroStats
from .breakout import BreakoutEngine
from .order_flow import OrderFlowEngine
from .regime import RegimeEngine
from .stat_arb import StatArbEngine
from .trend import TrendEngine
from .vwap import VWAPEngine

ENGINE_ORDER = ["vwap", "trend", "breakout", "order_flow", "stat_arb", "regime"]


def build_engines(cfg: AppConfig) -> dict[str, Engine]:
    e = cfg.engines
    return {
        "vwap": VWAPEngine(e.vwap),
        "trend": TrendEngine(e.trend),
        "breakout": BreakoutEngine(e.breakout),
        "order_flow": OrderFlowEngine(e.order_flow),
        "stat_arb": StatArbEngine(e.stat_arb, cfg.costs),
        "regime": RegimeEngine(e.regime),
    }


__all__ = ["Engine", "MarketContext", "MicroStats", "build_engines", "ENGINE_ORDER", "VWAPEngine", "TrendEngine",
           "BreakoutEngine", "OrderFlowEngine", "StatArbEngine", "RegimeEngine"]
