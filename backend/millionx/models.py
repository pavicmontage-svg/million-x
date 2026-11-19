"""Core data structures shared by backtest, paper and live trading."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

import numpy as np

MINUTE_MS = 60_000


class Side(int, Enum):
    LONG = 1
    SHORT = -1


class BotStatus(str, Enum):
    RESEARCH = "RESEARCH"            # no validated results yet
    BACKTEST_PASSED = "BACKTEST_PASSED"
    PAPER = "PAPER"                  # trading virtually in real time
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"  # passed backtest + paper criteria
    SUSPENDED = "SUSPENDED"          # auto-disabled by supervisor (shadow trading continues)
    DISABLED = "DISABLED"            # disabled by the user


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


class VolRegime(str, Enum):
    HIGH = "HIGH_VOL"
    NORMAL = "NORMAL_VOL"
    LOW = "LOW_VOL"


@dataclass
class Bars:
    """Column-oriented OHLCV bars (oldest first). open_time in ms UTC."""
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    taker_buy_volume: np.ndarray
    quote_volume: np.ndarray
    interval_min: int = 1

    def __len__(self) -> int:
        return len(self.close)

    def slice(self, start: int, end: int) -> "Bars":
        return Bars(*(getattr(self, k)[start:end] for k in
                      ("open_time", "open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume")),
                    interval_min=self.interval_min)

    @property
    def close_time(self) -> np.ndarray:
        return self.open_time + self.interval_min * MINUTE_MS

    @staticmethod
    def from_rows(rows: list[list[Any]], interval_min: int = 1) -> "Bars":
        """Rows in Binance kline format."""
        a = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                       float(r[9]) if len(r) > 9 else float(r[5]) / 2, float(r[7]) if len(r) > 7 else 0.0]
                      for r in rows], dtype=float) if rows else np.zeros((0, 8))
        return Bars(a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], a[:, 7],
                    interval_min=interval_min)


@dataclass
class OrderBookSnapshot:
    ts: int
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bps(self) -> float:
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * 1e4

    def depth_within(self, bps: float) -> tuple[float, float]:
        m = self.mid
        lo, hi = m * (1 - bps / 1e4), m * (1 + bps / 1e4)
        bid = sum(p * q for p, q in self.bids if p >= lo)
        ask = sum(p * q for p, q in self.asks if p <= hi)
        return bid, ask

    def imbalance(self, levels: int = 10) -> float:
        b = sum(q for _, q in self.bids[:levels])
        a = sum(q for _, q in self.asks[:levels])
        return (b - a) / (b + a) if (a + b) > 0 else 0.0


@dataclass
class Signal:
    bot_id: str
    symbol: str
    side: Side
    entry: float              # reference price at signal (bar close)
    stop: float
    tp1: float                # partial take profit
    tp2: float                # final target
    confidence: float         # 0..1, strategy's own estimate (not a probability of profit)
    bar_time: int             # close time of the signal bar (ms)
    timeframe: int
    reason: str = ""
    entry_type: str = "MARKET"  # MARKET | LIMIT
    limit_price: Optional[float] = None
    features: dict[str, float] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    score: float = 0.0        # filled by the supervisor

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = "LONG" if self.side == Side.LONG else "SHORT"
        return d


@dataclass
class Position:
    id: str
    bot_id: str
    symbol: str
    side: Side
    qty: float
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    initial_stop: float
    opened_at: int
    leverage: int = 1
    risk_amount: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    realized_pnl: float = 0.0     # from partial exits, before fees
    qty_initial: float = 0.0
    tp1_done: bool = False
    trail_active: bool = False
    best_price: float = 0.0
    atr_at_entry: float = 0.0
    signal_id: str = ""
    regime: str = ""
    virtual: bool = False         # shadow (competition) position
    exchange_ids: dict[str, Any] = field(default_factory=dict)

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * int(self.side)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = "LONG" if self.side == Side.LONG else "SHORT"
        return d


@dataclass
class Trade:
    id: str
    bot_id: str
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    opened_at: int
    closed_at: int
    gross_pnl: float
    fees: float
    funding: float
    net_pnl: float
    r_multiple: float
    risk_amount: float
    exit_reason: str
    regime: str = ""
    virtual: bool = False
    mode: str = "backtest"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_ms() -> int:
    return int(time.time() * 1000)
