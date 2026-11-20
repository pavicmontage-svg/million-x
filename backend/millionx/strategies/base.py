"""Strategy framework.

A strategy works on its own timeframe (built from closed 1m bars), computes
causal features once in `prepare`, and is queried with `signal_at(i)` where i
is a 1m bar index. A signal can only appear at the 1m bar that *closes* a bar
of the strategy's timeframe, so backtest and live use the exact same code.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .. import indicators as ind
from ..models import Bars, OrderBookSnapshot, Side, Signal, MINUTE_MS
from ..regime import RegimeSeries, detect

# Minimum stop distance (fraction of price). A stop tighter than this makes
# exchange fees a large share of the risk (on 1-minute scalps often 40-50 %),
# so the stop is widened and both targets are scaled by the same factor: the
# reward/risk structure of the strategy is kept, only the size of the move grows.
MIN_STOP_FRAC = 0.0042


def set_min_stop_from_fees(fees, target_fee_share: float) -> float:
    """min stop = round-trip cost rate * (1/share - 1). Returns the value set."""
    global MIN_STOP_FRAC
    if target_fee_share <= 0:
        MIN_STOP_FRAC = 0.0
    else:
        cost = 2 * fees.taker + 2 * fees.slippage_bps / 1e4
        MIN_STOP_FRAC = cost * (1 / min(0.95, target_fee_share) - 1)
    return MIN_STOP_FRAC


class MarketData:
    """All market information for one symbol up to "now" (no future bars)."""

    def __init__(self, symbol: str, bars_1m: Bars, book: Optional[OrderBookSnapshot] = None):
        self.symbol = symbol
        self.b1 = bars_1m
        self.book = book
        self._tf: dict[int, tuple[Bars, np.ndarray]] = {}
        self._regime: Optional[RegimeSeries] = None
        self._trend15: Optional[np.ndarray] = None

    def tf(self, minutes: int) -> tuple[Bars, np.ndarray]:
        if minutes not in self._tf:
            self._tf[minutes] = ind.resample(self.b1, minutes)
        return self._tf[minutes]

    @property
    def regime(self) -> RegimeSeries:
        if self._regime is None:
            self._regime = detect(self.tf(15)[0])
        return self._regime

    @property
    def trend15(self) -> np.ndarray:
        """+1 / -1 / 0 per 15m bar: EMA21 vs EMA50 with price confirmation."""
        if self._trend15 is None:
            b15 = self.tf(15)[0]
            e21, e50 = ind.ema(b15.close, 21), ind.ema(b15.close, 50)
            t = np.zeros(len(b15))
            t[(e21 > e50) & (b15.close > e21)] = 1
            t[(e21 < e50) & (b15.close < e21)] = -1
            self._trend15 = t
        return self._trend15

    def idx15_at_1m(self, i: int) -> int:
        return ind.last_closed_index(self.tf(15)[1], i)

    def regime_at_1m(self, i: int) -> tuple[str, str]:
        return self.regime.at(self.idx15_at_1m(i))

    def atr_pct15_at_1m(self, i: int) -> float:
        j = self.idx15_at_1m(i)
        if j < 0:
            return float("nan")
        return float(self.regime.atr_pct[j])


@dataclass
class StrategyInfo:
    bot_id: str
    name: str
    description: str
    timeframe: int
    is_replacement: bool = False


class Strategy:
    bot_id = "base"
    name = "Base"
    description = ""
    default_params: dict[str, Any] = {}
    param_grid: dict[str, list[Any]] = {}
    warmup_bars = 60

    def __init__(self, params: Optional[dict[str, Any]] = None):
        self.params = dict(self.default_params)
        if params:
            self.params.update(params)
        self.md: Optional[MarketData] = None
        self.b: Optional[Bars] = None
        self.close_idx: np.ndarray = np.zeros(0, dtype=int)
        self.j_of_i: np.ndarray = np.zeros(0, dtype=int)
        self.f: dict[str, np.ndarray] = {}

    # ---- framework -------------------------------------------------------
    @property
    def timeframe(self) -> int:
        return int(self.params.get("timeframe", 5))

    def info(self) -> StrategyInfo:
        return StrategyInfo(self.bot_id, self.name, self.description, self.timeframe,
                            getattr(self, "is_replacement", False))

    def clone(self, params: Optional[dict[str, Any]] = None) -> "Strategy":
        s = copy.copy(self)
        s.params = dict(self.params)
        if params:
            s.params.update(params)
        s.md, s.b, s.f = None, None, {}
        return s

    def prepare(self, md: MarketData) -> None:
        self.md = md
        self.b, self.close_idx = md.tf(self.timeframe)
        self.j_of_i = np.full(len(md.b1), -1, dtype=int)
        if len(self.close_idx):
            self.j_of_i[self.close_idx] = np.arange(len(self.close_idx))
        self.f = {"atr": ind.atr(self.b, 14)} if len(self.b) else {"atr": np.zeros(0)}
        if len(self.b) > self.warmup_bars:
            self.compute(self.b)

    def compute(self, b: Bars) -> None:  # override
        raise NotImplementedError

    def evaluate(self, j: int) -> Optional[tuple[Side, float, float, float, float, str]]:  # override
        """Return (side, stop, tp1, tp2, confidence, reason) or None."""
        raise NotImplementedError

    def trend15_at(self, j: int) -> int:
        i1 = int(self.close_idx[j])
        k = self.md.idx15_at_1m(i1)
        return int(self.md.trend15[k]) if k >= 0 else 0

    def signal_at(self, i: int) -> Optional[Signal]:
        if self.md is None or i < 0 or i >= len(self.j_of_i):
            return None
        j = int(self.j_of_i[i])
        if j < self.warmup_bars or not self.f or "atr" not in self.f or len(self.f) <= 1:
            return None
        if np.isnan(self.f["atr"][j]) or self.f["atr"][j] <= 0:
            return None
        out = self.evaluate(j)
        if out is None:
            return None
        side, stop, tp1, tp2, conf, reason = out
        entry = float(self.b.close[j])
        if side == Side.LONG and not (stop < entry < tp1 <= tp2):
            return None
        if side == Side.SHORT and not (stop > entry > tp1 >= tp2):
            return None
        feats = {"atr": float(self.f["atr"][j])}
        d = abs(entry - stop)
        if MIN_STOP_FRAC > 0 and d < entry * MIN_STOP_FRAC:
            k = entry * MIN_STOP_FRAC / d
            sgn = int(side)
            stop = entry - sgn * d * k
            tp1 = entry + (tp1 - entry) * k
            tp2 = entry + (tp2 - entry) * k
            feats["stop_widened_from_pct"] = d / entry * 100
            reason = f"{reason} [stop widened {d / entry * 100:.2f}%→{MIN_STOP_FRAC * 100:.2f}% for fees]"
        return Signal(bot_id=self.bot_id, symbol=self.md.symbol, side=side, entry=entry, stop=float(stop),
                      tp1=float(tp1), tp2=float(tp2), confidence=float(np.clip(conf, 0, 1)),
                      bar_time=int(self.b.open_time[j] + self.timeframe * MINUTE_MS), timeframe=self.timeframe,
                      reason=reason, features=feats)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def levels(side: Side, entry: float, stop: float, r1: float, r2: float) -> tuple[float, float]:
        r = abs(entry - stop)
        s = int(side)
        return entry + s * r1 * r, entry + s * r2 * r

    def vol_ratio(self, j: int, n: int = 20) -> float:
        v = self.b.volume
        if j < n:
            return 0.0
        avg = v[j - n:j].mean()
        return float(v[j] / avg) if avg > 0 else 0.0
