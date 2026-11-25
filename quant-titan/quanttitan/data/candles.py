"""Columnar candle container, resampling, higher-timeframe alignment and integrity checks."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
         "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}
FIELDS = ("ts", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy")


@dataclass
class Candles:
    """Closed candles only. ``ts`` is the bar open time in ms (UTC)."""

    symbol: str
    timeframe: str
    ts: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    open: np.ndarray = field(default_factory=lambda: np.zeros(0))
    high: np.ndarray = field(default_factory=lambda: np.zeros(0))
    low: np.ndarray = field(default_factory=lambda: np.zeros(0))
    close: np.ndarray = field(default_factory=lambda: np.zeros(0))
    volume: np.ndarray = field(default_factory=lambda: np.zeros(0))
    quote_volume: np.ndarray = field(default_factory=lambda: np.zeros(0))
    trades: np.ndarray = field(default_factory=lambda: np.zeros(0))
    taker_buy: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def tf_ms(self) -> int:
        return TF_MS[self.timeframe]

    @property
    def close_ts(self) -> np.ndarray:
        return self.ts + self.tf_ms

    @classmethod
    def from_rows(cls, symbol: str, timeframe: str, rows: list) -> "Candles":
        """Build from Binance kline rows ([openTime, o, h, l, c, v, closeTime, qv, n, takerBase, takerQuote, _])."""
        if not rows:
            return cls(symbol, timeframe)
        a = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                       float(r[7]), float(r[8]), float(r[9])] for r in rows])
        return cls(symbol, timeframe, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5],
                   a[:, 6], a[:, 7], a[:, 8])

    def slice(self, start: int | None = None, end: int | None = None) -> "Candles":
        return Candles(self.symbol, self.timeframe, *(getattr(self, f)[start:end] for f in FIELDS))

    def tail(self, n: int) -> "Candles":
        return self.slice(max(0, len(self) - n), None)

    def append(self, other: "Candles") -> "Candles":
        """Concatenate, de-duplicate by ts (newer wins) and sort."""
        if len(other) == 0:
            return self
        if len(self) == 0:
            return other
        cols = {f: np.concatenate([getattr(self, f), getattr(other, f)]) for f in FIELDS}
        ts = cols["ts"]
        # keep last occurrence of each ts
        _, idx = np.unique(ts[::-1], return_index=True)
        keep = np.sort(len(ts) - 1 - idx)
        order = np.argsort(ts[keep], kind="stable")
        sel = keep[order]
        return Candles(self.symbol, self.timeframe, *(cols[f][sel] for f in FIELDS))

    def to_npz(self, path: str) -> None:
        np.savez_compressed(path, symbol=self.symbol, timeframe=self.timeframe, **{f: getattr(self, f) for f in FIELDS})

    @classmethod
    def from_npz(cls, path: str) -> "Candles":
        d = np.load(path, allow_pickle=False)
        return cls(str(d["symbol"]), str(d["timeframe"]), *(d[f] for f in FIELDS))

    def last_close(self) -> float:
        return float(self.close[-1]) if len(self) else float("nan")


def resample(c: Candles, timeframe: str, *, complete_only: bool = True) -> Candles:
    """Aggregate to a higher timeframe. Incomplete groups (missing bars or still forming) are dropped
    when ``complete_only`` so that no partially-formed higher-timeframe bar can leak future data."""
    tgt = TF_MS[timeframe]
    src = c.tf_ms
    if tgt % src:
        raise ValueError(f"cannot resample {c.timeframe} -> {timeframe}")
    if len(c) == 0:
        return Candles(c.symbol, timeframe)
    grp = c.ts // tgt
    bounds = np.flatnonzero(np.diff(grp)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(c)]])
    counts = ends - starts
    need = tgt // src
    ok = counts == need if complete_only else counts > 0
    starts, ends = starts[ok], ends[ok]
    if len(starts) == 0:
        return Candles(c.symbol, timeframe)
    return Candles(
        c.symbol, timeframe,
        (c.ts[starts] // tgt) * tgt,
        c.open[starts],
        _segment_reduce(np.maximum, c.high, starts, ends),
        _segment_reduce(np.minimum, c.low, starts, ends),
        c.close[ends - 1],
        _segment_reduce(np.add, c.volume, starts, ends),
        _segment_reduce(np.add, c.quote_volume, starts, ends),
        _segment_reduce(np.add, c.trades, starts, ends),
        _segment_reduce(np.add, c.taker_buy, starts, ends),
    )


def _segment_reduce(uf, a: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Reduce contiguous segments [starts[i], ends[i]) with ufunc ``uf``. Segments may be non-adjacent
    (dropped incomplete groups), so reduce over all boundaries and pick the kept segments."""
    bounds = np.unique(np.concatenate([starts, ends[ends < len(a)]]))
    vals = uf.reduceat(a, bounds)
    return vals[np.searchsorted(bounds, starts)]


def align_htf(ltf: Candles, htf: Candles, values: np.ndarray) -> np.ndarray:
    """Map higher-timeframe values onto lower-timeframe bars using only HTF bars that were
    *closed* at the close of each LTF bar (no lookahead)."""
    out = np.full(len(ltf), np.nan)
    if len(htf) == 0:
        return out
    idx = np.searchsorted(htf.close_ts, ltf.close_ts, side="right") - 1
    ok = idx >= 0
    out[ok] = np.asarray(values, dtype=float)[idx[ok]]
    return out


@dataclass
class IntegrityReport:
    ok: bool
    gaps: int
    duplicates: int
    out_of_order: int
    bad_ohlc: int
    zero_volume: int
    issues: list[str]


def check_integrity(c: Candles, max_gap_bars: int = 0) -> IntegrityReport:
    issues: list[str] = []
    if len(c) == 0:
        return IntegrityReport(False, 0, 0, 0, 0, 0, ["empty"])
    d = np.diff(c.ts)
    out_of_order = int((d < 0).sum())
    duplicates = int((d == 0).sum())
    gaps = int((d > c.tf_ms * (1 + max_gap_bars)).sum())
    bad = int(((c.high < np.maximum(c.open, c.close)) | (c.low > np.minimum(c.open, c.close)) | (c.low <= 0)).sum())
    zero_vol = int((c.volume <= 0).sum())
    if out_of_order:
        issues.append(f"{out_of_order} out-of-order bars")
    if duplicates:
        issues.append(f"{duplicates} duplicate bars")
    if gaps:
        issues.append(f"{gaps} gaps")
    if bad:
        issues.append(f"{bad} inconsistent OHLC bars")
    return IntegrityReport(not (out_of_order or duplicates or gaps or bad), gaps, duplicates, out_of_order, bad, zero_vol, issues)
