"""Causal technical indicators (value at index i uses data up to and including i).

Every function here is written so that it never reads future bars. Tests in
tests/test_no_lookahead.py verify this by comparing results on truncated series.
"""
from __future__ import annotations

import numpy as np

from .models import Bars, MINUTE_MS


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) == 0:
        return out
    a = 2.0 / (n + 1)
    start = min(n - 1, len(x) - 1)
    if len(x) < n:
        return out
    out[start] = np.mean(x[:n])
    for i in range(start + 1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's moving average."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(x.astype(float), 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    w = sliding_window_view(x.astype(float), n)
    out[n - 1:] = w.std(axis=1)
    return out


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def true_range(b: Bars) -> np.ndarray:
    pc = np.concatenate([[b.close[0]], b.close[:-1]])
    return np.maximum(b.high - b.low, np.maximum(np.abs(b.high - pc), np.abs(b.low - pc)))


def atr(b: Bars, n: int = 14) -> np.ndarray:
    return rma(true_range(b), n)


def rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(x, prepend=x[0])
    up = rma(np.where(d > 0, d, 0.0), n)
    dn = rma(np.where(d < 0, -d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / dn
        out = 100 - 100 / (1 + rs)
    out[(dn == 0) & ~np.isnan(up)] = 100.0
    return out


def adx(b: Bars, n: int = 14) -> np.ndarray:
    up = np.diff(b.high, prepend=b.high[0])
    dn = -np.diff(b.low, prepend=b.low[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(b), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(pdm, n) / tr
        mdi = 100 * rma(mdm, n) / tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.nan_to_num(dx, nan=0.0)
    out = rma(dx, n)
    return out


def session_vwap(b: Bars, session_ms: int = 24 * 60 * MINUTE_MS) -> tuple[np.ndarray, np.ndarray]:
    """VWAP reset every UTC session; returns (vwap, rolling volume-weighted std)."""
    tp = (b.high + b.low + b.close) / 3
    sess = b.open_time // session_ms
    vwap = np.full(len(tp), np.nan)
    sd = np.full(len(tp), np.nan)
    cv = cpv = cpv2 = 0.0
    last = None
    for i in range(len(tp)):
        if sess[i] != last:
            cv = cpv = cpv2 = 0.0
            last = sess[i]
        v = b.volume[i]
        cv += v
        cpv += v * tp[i]
        cpv2 += v * tp[i] * tp[i]
        if cv > 0:
            vwap[i] = cpv / cv
            var = max(cpv2 / cv - vwap[i] ** 2, 0.0)
            sd[i] = np.sqrt(var)
    return vwap, sd


def bollinger(x: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = sma(x, n)
    s = rolling_std(x, n)
    return m - k * s, m, m + k * s


def keltner(b: Bars, n: int = 20, k: float = 1.5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = ema(b.close, n)
    a = atr(b, n)
    return m - k * a, m, m + k * a


def donchian(b: Bars, n: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Channel of the *previous* n bars (excludes current bar) so a breakout is
    a close beyond it."""
    hi = rolling_max(b.high, n)
    lo = rolling_min(b.low, n)
    return np.concatenate([[np.nan], hi[:-1]]), np.concatenate([[np.nan], lo[:-1]])


def supertrend(b: Bars, n: int = 10, mult: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """Returns (line, direction) where direction is +1 uptrend / -1 downtrend."""
    a = atr(b, n)
    hl2 = (b.high + b.low) / 2
    ub = hl2 + mult * a
    lb = hl2 - mult * a
    fub = np.full(len(b), np.nan)
    flb = np.full(len(b), np.nan)
    line = np.full(len(b), np.nan)
    d = np.zeros(len(b))
    for i in range(len(b)):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fub[i - 1]):
            fub[i], flb[i], d[i] = ub[i], lb[i], 1
            line[i] = flb[i]
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or b.close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or b.close[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] == 1:
            d[i] = -1 if b.close[i] < flb[i] else 1
        else:
            d[i] = 1 if b.close[i] > fub[i] else -1
        line[i] = flb[i] if d[i] == 1 else fub[i]
    return line, d


def pivots_confirmed(x: np.ndarray, left: int, right: int, kind: str) -> np.ndarray:
    """Boolean array marking at index i (the *confirmation* bar) that a pivot
    occurred at i-right. Using the confirmation index avoids look-ahead."""
    out = np.zeros(len(x), dtype=bool)
    for i in range(left + right, len(x)):
        c = i - right
        win = x[c - left: i + 1]
        if kind == "high" and x[c] == win.max() and np.argmax(win) == left:
            out[i] = True
        if kind == "low" and x[c] == win.min() and np.argmin(win) == left:
            out[i] = True
    return out


def cvd(b: Bars) -> np.ndarray:
    """Cumulative volume delta from taker-buy volume (aggressive buyers - sellers)."""
    delta = 2 * b.taker_buy_volume - b.volume
    return np.cumsum(delta)


def zscore(x: np.ndarray, n: int) -> np.ndarray:
    m = sma(x, n)
    s = rolling_std(x, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (x - m) / s


# ---- multi-timeframe helpers ----------------------------------------------

def resample(b: Bars, minutes: int) -> tuple[Bars, np.ndarray]:
    """Aggregate 1m bars into `minutes` bars. Only *complete* groups are kept.
    Returns (bars, close_idx) where close_idx[j] is the 1m index whose close
    completes higher-timeframe bar j."""
    if minutes == 1:
        return b, np.arange(len(b))
    g = b.open_time // (minutes * MINUTE_MS)
    idx_change = np.flatnonzero(np.diff(g)) + 1
    starts = np.concatenate([[0], idx_change])
    ends = np.concatenate([idx_change, [len(b)]])
    keep = ((ends - starts) == minutes) & (b.open_time[starts] % (minutes * MINUTE_MS) == 0)
    if not keep.any():
        empty = np.zeros(0)
        return Bars(empty.astype(np.int64), empty, empty, empty, empty, empty, empty, empty, minutes), empty.astype(int)
    s_arr, e_arr = starts[keep], ends[keep]
    out = Bars(
        open_time=b.open_time[s_arr],
        open=b.open[s_arr],
        high=np.maximum.reduceat(b.high, starts)[keep],
        low=np.minimum.reduceat(b.low, starts)[keep],
        close=b.close[e_arr - 1],
        volume=np.add.reduceat(b.volume, starts)[keep],
        taker_buy_volume=np.add.reduceat(b.taker_buy_volume, starts)[keep],
        quote_volume=np.add.reduceat(b.quote_volume, starts)[keep],
        interval_min=minutes,
    )
    return out, e_arr - 1


def last_closed_index(close_idx: np.ndarray, i_1m: int) -> int:
    """Index of the last higher-TF bar completed at or before 1m index i."""
    j = int(np.searchsorted(close_idx, i_1m, side="right")) - 1
    return j
