"""Engine framework.

Every engine is an independent unit with its own configuration section, its own causal feature
computation, its own signal logic, cooldown, statistics and logger. Engines never read each other's
signals: they only report to the orchestrator.

Feature/evaluate split
----------------------
``features(ctx)`` computes causal numpy arrays aligned to the execution-timeframe bars.
``evaluate(ctx, feats, i)`` turns the features at closed bar ``i`` into an :class:`EngineSignal`.
Live trading calls both on a rolling buffer and evaluates the last bar; the backtester computes the
features once on the full history and evaluates every bar. Because features are causal, both paths
produce identical signals for the same bar (verified in ``tests/test_engines.py``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import indicators as ind
from ..config import dump
from ..core.logging import get_logger
from ..core.types import ContractSpec, Direction, EngineSignal, RegimeState
from ..data.candles import Candles, align_htf, resample


@dataclass
class MicroStats:
    """Live market-microstructure measurements (only available in paper/live, not in bar backtests)."""

    spread_bps: float = float("nan")
    mid: float = float("nan")
    book_imbalance: float = 0.0  # (bid_qty - ask_qty)/(bid_qty + ask_qty) over N levels, -1..1
    depth_usdt: float = 0.0  # bid+ask notional within 0.5% of mid
    depth_change: float = 0.0  # relative change vs previous snapshot
    aggr_buy_usdt: float = 0.0
    aggr_sell_usdt: float = 0.0
    large_trade_share: float = 0.0  # share of notional from trades above the size quantile
    large_trade_bias: float = 0.0  # (large buys - large sells)/large total, -1..1
    trade_count: int = 0
    price_impact: float = 0.0  # regression slope of return on signed notional (per 1M USDT)
    ts: int = 0

    @property
    def aggr_imbalance(self) -> float:
        t = self.aggr_buy_usdt + self.aggr_sell_usdt
        return (self.aggr_buy_usdt - self.aggr_sell_usdt) / t if t > 0 else 0.0


@dataclass
class MarketContext:
    symbol: str
    candles: Candles  # execution timeframe, closed bars only
    spec: ContractSpec = field(default_factory=lambda: ContractSpec("UNKNOWN"))
    micro: MicroStats | None = None
    funding_rate: float = 0.0
    mark_price: float = 0.0
    regime: RegimeState | None = None
    data_age_s: float = 0.0
    _htf: dict[str, Candles] = field(default_factory=dict)

    def htf(self, tf: str) -> Candles:
        if tf not in self._htf:
            self._htf[tf] = resample(self.candles, tf)
        return self._htf[tf]

    def htf_aligned(self, tf: str, values: np.ndarray) -> np.ndarray:
        return align_htf(self.candles, self.htf(tf), values)


def score_linear(x: float, lo: float, hi: float) -> float:
    """Map x in [lo, hi] -> [0, 1] (clipped). Works for lo > hi (decreasing)."""
    if not np.isfinite(x):
        return 0.0
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def combine(components: dict[str, tuple[float, float]]) -> tuple[float, dict[str, float]]:
    """Weighted mean of component scores (each 0..1) -> confidence 0..100 and the per-component map."""
    tw = sum(w for _, w in components.values()) or 1.0
    conf = 100.0 * sum(s * w for s, w in components.values()) / tw
    return round(conf, 1), {k: round(100.0 * s, 1) for k, (s, _) in components.items()}


@dataclass
class EngineStats:
    signals: int = 0
    long_signals: int = 0
    short_signals: int = 0
    evaluations: int = 0
    last_signal: dict | None = None
    state_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    recent: deque = field(default_factory=lambda: deque(maxlen=200))

    def to_dict(self) -> dict:
        return {"signals": self.signals, "long_signals": self.long_signals, "short_signals": self.short_signals,
                "evaluations": self.evaluations, "last_signal": self.last_signal,
                "state_counts": dict(self.state_counts), "recent": list(self.recent)[-50:]}


class Engine(ABC):
    name: str = "base"
    title: str = "Engine"
    description: str = ""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.log = get_logger(f"engine.{self.name}")
        self.stats = EngineStats()
        self._last_signal_ts: dict[str, int] = {}

    # -- to implement ---------------------------------------------------------------------------
    @abstractmethod
    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]: ...

    @abstractmethod
    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal: ...

    # -- shared machinery -----------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "enabled", True))

    def min_bars(self) -> int:
        return 250

    def analyze(self, ctx: MarketContext) -> EngineSignal:
        """Evaluate the most recent closed bar (live path)."""
        if len(ctx.candles) < self.min_bars():
            return self.flat(ctx, len(ctx.candles) - 1, "WARMUP", [f"need {self.min_bars()} bars, have {len(ctx.candles)}"])
        f = self.features(ctx)
        return self.finalize(ctx, self.evaluate(ctx, f, len(ctx.candles) - 1))

    def finalize(self, ctx: MarketContext, sig: EngineSignal) -> EngineSignal:
        """Apply engine-level gates (enabled, min confidence, cooldown) and record statistics."""
        self.stats.evaluations += 1
        self.stats.state_counts[sig.state or "NONE"] += 1
        if sig.direction is not Direction.FLAT:
            if not self.enabled:
                sig = self._demote(sig, "engine disabled")
            elif sig.confidence < self.cfg.min_confidence:
                sig = self._demote(sig, f"confidence {sig.confidence:.0f} < min {self.cfg.min_confidence:.0f}")
            elif self.in_cooldown(ctx.symbol, sig.ts, ctx.candles.tf_ms):
                sig = self._demote(sig, "engine cooldown active for symbol")
        if sig.direction is not Direction.FLAT:
            self._last_signal_ts[ctx.symbol] = sig.ts
            self.stats.signals += 1
            if sig.direction is Direction.LONG:
                self.stats.long_signals += 1
            else:
                self.stats.short_signals += 1
            self.stats.last_signal = {"symbol": sig.symbol, "direction": sig.direction.value,
                                      "confidence": sig.confidence, "setup": sig.setup, "ts": sig.ts}
            self.stats.recent.append(self.stats.last_signal)
            self.log.info("%s %s %s conf=%.0f setup=%s", self.name, sig.symbol, sig.direction.value,
                          sig.confidence, sig.setup)
        return sig

    def _demote(self, sig: EngineSignal, why: str) -> EngineSignal:
        sig.reasons.append(f"suppressed: {why}")
        sig.features["suppressed_direction"] = float(sig.direction.sign)
        sig.direction = Direction.FLAT
        return sig

    def in_cooldown(self, symbol: str, ts: int, tf_ms: int) -> bool:
        last = self._last_signal_ts.get(symbol)
        return last is not None and ts - last < self.cfg.cooldown_bars * tf_ms

    def reset_state(self) -> None:
        self._last_signal_ts.clear()
        self.stats = EngineStats()

    def flat(self, ctx: MarketContext, i: int, state: str, reasons: list[str], **features) -> EngineSignal:
        ts = int(ctx.candles.close_ts[i]) if 0 <= i < len(ctx.candles) else 0
        return EngineSignal(self.name, ctx.symbol, Direction.FLAT, 0.0, state=state, reasons=reasons,
                            features={k: float(v) for k, v in features.items() if v is not None}, ts=ts)

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description,
                "enabled": self.enabled, "config": dump(self.cfg), "stats": self.stats.to_dict()}


def common_features(c: Candles, atr_n: int = 14) -> dict[str, np.ndarray]:
    return {
        "atr": ind.atr(c.high, c.low, c.close, atr_n),
        "relvol": ind.relative_volume(c.volume, 20),
        "taker": ind.taker_buy_ratio(c.volume, c.taker_buy),
    }


def fval(f: dict[str, np.ndarray], key: str, i: int, default: float = float("nan")) -> float:
    a = f.get(key)
    if a is None or i < 0 or i >= len(a):
        return default
    v = float(a[i])
    return v if np.isfinite(v) else default
