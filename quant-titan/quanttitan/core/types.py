"""Core domain types shared by every layer."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1 if self is Direction.SHORT else 0

    def opposite(self) -> "Direction":
        return {Direction.LONG: Direction.SHORT, Direction.SHORT: Direction.LONG}.get(self, Direction.FLAT)


class Regime(str, Enum):
    STRONG_TREND = "STRONG_TREND"
    WEAK_TREND = "WEAK_TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
    MARKET_DISLOCATION = "MARKET_DISLOCATION"
    UNSAFE_MARKET = "UNSAFE_MARKET"
    UNKNOWN = "UNKNOWN"


class Outcome(str, Enum):
    APPROVE_LONG = "APPROVE_LONG"
    APPROVE_SHORT = "APPROVE_SHORT"
    WAIT = "WAIT"
    REJECT = "REJECT"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    CLOSE = "CLOSE"
    BLOCK_ALL_TRADING = "BLOCK_ALL_TRADING"


@dataclass
class ContractSpec:
    symbol: str
    tick_size: float = 0.01
    step_size: float = 0.001
    min_qty: float = 0.001
    max_qty: float = 1e9
    min_notional: float = 5.0
    maint_margin_rate: float = 0.004  # first bracket; used for liquidation estimates
    max_leverage: float = 50.0
    onboard_ms: int = 0
    status: str = "TRADING"

    def round_qty(self, qty: float) -> float:
        if self.step_size <= 0:
            return qty
        steps = int(qty / self.step_size + 1e-9)
        return round(steps * self.step_size, 12)

    def round_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        return round(round(price / self.tick_size) * self.tick_size, 12)


@dataclass
class TargetLevel:
    price: float
    fraction: float  # fraction of the original quantity to close


@dataclass
class EngineSignal:
    engine: str
    symbol: str
    direction: Direction
    confidence: float  # 0..100, transparent component score
    setup: str = ""
    state: str = ""  # engine-specific market state label (e.g. VWAP_RECLAIM)
    entry: float = 0.0
    stop: float = 0.0
    targets: list[TargetLevel] = field(default_factory=list)
    trail_atr: float = 0.0
    atr: float = 0.0
    time_stop_bars: int = 0
    role: str = "primary"  # primary | confirmation | filter
    components: dict[str, float] = field(default_factory=dict)
    features: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    ts: int = 0
    pair: dict[str, Any] | None = None  # stat-arb legs

    @property
    def is_actionable(self) -> bool:
        return self.direction in (Direction.LONG, Direction.SHORT) and self.stop > 0 and self.entry > 0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def reward_r(self, idx: int = 0) -> float:
        if not self.targets or self.risk_per_unit <= 0:
            return 0.0
        t = self.targets[min(idx, len(self.targets) - 1)].price
        return abs(t - self.entry) / self.risk_per_unit

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        return d


@dataclass
class RegimeState:
    symbol: str
    regime: Regime
    flags: list[str] = field(default_factory=list)
    trend_direction: Direction = Direction.FLAT
    permitted_engines: list[str] = field(default_factory=list)
    risk_multiplier: float = 1.0
    metrics: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    ts: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["regime"] = self.regime.value
        d["trend_direction"] = self.trend_direction.value
        return d


@dataclass
class CostEstimate:
    fee_r: float
    slippage_r: float
    spread_r: float
    funding_r: float

    @property
    def total_r(self) -> float:
        return self.fee_r + self.slippage_r + self.spread_r + self.funding_r

    def to_dict(self) -> dict:
        return {**asdict(self), "total_r": self.total_r}


@dataclass
class Decision:
    id: str
    ts: int
    symbol: str
    outcome: Outcome
    regime: str = ""
    engine: str = ""
    setup: str = ""
    direction: Direction = Direction.FLAT
    confidence: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    targets: list[TargetLevel] = field(default_factory=list)
    qty: float = 0.0
    risk_usdt: float = 0.0
    expected_r: float = 0.0
    net_edge_r: float = 0.0
    edge_samples: int = 0
    exploration: bool = False
    costs: dict[str, float] = field(default_factory=dict)
    engine_views: dict[str, str] = field(default_factory=dict)
    risk_verdict: str = ""
    risk_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    explanation: str = ""
    signal: EngineSignal | None = None
    approval: dict | None = None
    status: str = "new"  # new | pending_user | executed | expired | rejected_by_user | failed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["direction"] = self.direction.value
        if self.signal is not None:
            d["signal"] = self.signal.to_dict()
        return d


@dataclass
class Position:
    id: str
    symbol: str
    direction: Direction
    engine: str
    setup: str
    entry_price: float
    qty: float  # remaining quantity (base asset)
    initial_qty: float
    stop: float
    initial_stop: float
    targets: list[TargetLevel]
    opened_ts: int
    decision_id: str = ""
    trail_atr: float = 0.0
    atr: float = 0.0
    time_stop_bars: int = 0
    bars_held: int = 0
    realized_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    slippage_cost: float = 0.0
    tp_hits: int = 0
    best_price: float = 0.0
    regime: str = ""
    leverage: float = 1.0
    group_id: str = ""  # stat-arb pair id
    status: str = "open"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_usdt(self) -> float:
        """Current risk to stop (can be <= 0 once the stop is at/through break-even)."""
        return max(0.0, (self.entry_price - self.stop) * self.direction.sign * self.qty)

    @property
    def initial_risk_usdt(self) -> float:
        return abs(self.entry_price - self.initial_stop) * self.initial_qty

    def notional(self, price: float) -> float:
        return abs(self.qty * price)

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.direction.sign * self.qty

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        return d


@dataclass
class TradeRecord:
    id: str
    symbol: str
    direction: str
    engine: str
    setup: str
    regime: str
    entry_price: float
    exit_price: float
    qty: float
    opened_ts: int
    closed_ts: int
    gross_pnl: float
    fees: float
    funding: float
    slippage_cost: float
    net_pnl: float
    r_multiple: float
    initial_risk: float
    exit_reason: str
    decision_id: str = ""
    mode: str = "paper"
    group_id: str = ""
    confidence: float = 0.0
    expected_r: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
