"""Vectorised, strictly causal technical and statistical indicators.

Causality contract: for every function, ``f(x)[t]`` depends only on ``x[0..t]``. This is verified by
``tests/test_indicators.py::test_causality`` which recomputes every indicator on truncated inputs and
compares against the full-length result. The backtester relies on this to avoid lookahead bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import lfilter

Array = np.ndarray


def _f(x) -> Array:
    return np.asarray(x, dtype=float)


def ema(x: Array, n: int) -> Array:
    """Exponential moving average with alpha = 2/(n+1), seeded with the first finite value."""
    return _ewm(_f(x), 2.0 / (n + 1.0))


def rma(x: Array, n: int) -> Array:
    """Wilder's moving average (alpha = 1/n)."""
    return _ewm(_f(x), 1.0 / n)


def _ewm(x: Array, alpha: float) -> Array:
    out = np.full_like(x, np.nan)
    finite = np.isfinite(x)
    if not finite.any():
        return out
    start = int(np.argmax(finite))
    seg = x[start:].copy()
    # forward-fill interior NaNs so the filter stays defined (causal)
    if not np.isfinite(seg).all():
        seg = pd.Series(seg).ffill().to_numpy()
    y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], seg, zi=[(1.0 - alpha) * seg[0]])
    out[start:] = y
    return out


def sma(x: Array, n: int) -> Array:
    return pd.Series(_f(x)).rolling(n, min_periods=n).mean().to_numpy()


def rolling_std(x: Array, n: int, ddof: int = 0) -> Array:
    return pd.Series(_f(x)).rolling(n, min_periods=n).std(ddof=ddof).to_numpy()


def rolling_max(x: Array, n: int) -> Array:
    return pd.Series(_f(x)).rolling(n, min_periods=n).max().to_numpy()


def rolling_min(x: Array, n: int) -> Array:
    return pd.Series(_f(x)).rolling(n, min_periods=n).min().to_numpy()


def rolling_sum(x: Array, n: int) -> Array:
    return pd.Series(_f(x)).rolling(n, min_periods=n).sum().to_numpy()


def shift(x: Array, k: int = 1) -> Array:
    x = _f(x)
    out = np.full_like(x, np.nan)
    if k > 0:
        out[k:] = x[:-k]
    elif k == 0:
        out[:] = x
    return out


def true_range(h: Array, l: Array, c: Array) -> Array:
    h, l, c = _f(h), _f(l), _f(c)
    pc = shift(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def atr(h: Array, l: Array, c: Array, n: int = 14) -> Array:
    return rma(true_range(h, l, c), n)


def adx(h: Array, l: Array, c: Array, n: int = 14) -> tuple[Array, Array, Array]:
    """Return (adx, +DI, -DI) using Wilder's definitions."""
    h, l, c = _f(h), _f(l), _f(c)
    up = h - shift(h, 1)
    dn = shift(l, 1) - l
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm[0] = minus_dm[0] = 0.0
    tr_s = rma(true_range(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rma(plus_dm, n) / tr_s
        mdi = 100.0 * rma(minus_dm, n) / tr_s
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.where(np.isfinite(dx), dx, 0.0)
    a = rma(dx, n)
    a[: 2 * n - 1] = np.nan  # warm-up
    return a, pdi, mdi


def rsi(c: Array, n: int = 14) -> Array:
    c = _f(c)
    d = np.diff(c, prepend=c[0])
    g = rma(np.maximum(d, 0.0), n)
    lo = rma(np.maximum(-d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = g / lo
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where(lo == 0, 100.0, out)
    out[:n] = np.nan
    return out


def bollinger(c: Array, n: int = 20, k: float = 2.0) -> tuple[Array, Array, Array, Array]:
    """Return (mid, upper, lower, width) where width = (upper-lower)/mid."""
    mid = sma(c, n)
    sd = rolling_std(c, n, ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (up - lo) / mid
    return mid, up, lo, width


def rolling_percentile_rank(x: Array, n: int) -> Array:
    """Percentile (0..100) of x[t] within the window x[t-n+1..t]."""
    s = pd.Series(_f(x))
    return (s.rolling(n, min_periods=max(10, n // 5)).rank(pct=True) * 100.0).to_numpy()


def efficiency_ratio(c: Array, n: int = 20) -> Array:
    """Kaufman efficiency ratio: net move / path length over n bars (0 = noise, 1 = straight line)."""
    c = _f(c)
    net = np.abs(c - shift(c, n))
    path = rolling_sum(np.abs(np.diff(c, prepend=c[0])), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        er = net / path
    return np.where(np.isfinite(er), er, 0.0)


def log_returns(c: Array) -> Array:
    c = _f(c)
    r = np.full_like(c, np.nan)
    r[1:] = np.log(c[1:] / c[:-1])
    return r


def realized_vol(c: Array, n: int) -> Array:
    return rolling_std(log_returns(c), n, ddof=1)


def zscore(x: Array, n: int) -> Array:
    m = sma(x, n)
    s = rolling_std(x, n, ddof=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (_f(x) - m) / s
    return np.where(np.isfinite(z), z, np.nan)


def relative_volume(v: Array, n: int = 20) -> Array:
    """v[t] / mean(v[t-n..t-1]) — the current bar is excluded from its own baseline."""
    base = shift(sma(v, n), 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rv = _f(v) / base
    return np.where(np.isfinite(rv), rv, np.nan)


def session_vwap(ts_ms: Array, h: Array, l: Array, c: Array, v: Array, *, anchor: str = "utc_day",
                 rolling_bars: int = 288) -> tuple[Array, Array, Array]:
    """Session VWAP and volume-weighted standard deviation.

    Typical price p_i = (H_i + L_i + C_i)/3. Within a session (UTC day, or a rolling window):

        VWAP_t   = sum(v_i * p_i) / sum(v_i)
        sigma_t  = sqrt( sum(v_i * p_i^2) / sum(v_i) - VWAP_t^2 )      (volume-weighted variance)
        band_k   = VWAP_t +/- k * sigma_t

    Returns (vwap, sigma, bars_in_session). Bars with zero cumulative volume yield NaN.
    """
    ts = np.asarray(ts_ms, dtype=np.int64)
    p = (_f(h) + _f(l) + _f(c)) / 3.0
    v = _f(v)
    if anchor == "utc_day":
        sess = ts // 86_400_000
        df = pd.DataFrame({"s": sess, "v": v, "vp": v * p, "vp2": v * p * p})
        g = df.groupby("s", sort=False)
        cv = g["v"].cumsum().to_numpy()
        cvp = g["vp"].cumsum().to_numpy()
        cvp2 = g["vp2"].cumsum().to_numpy()
        n_in = g.cumcount().to_numpy() + 1
    else:
        cv = rolling_sum(v, rolling_bars)
        cvp = rolling_sum(v * p, rolling_bars)
        cvp2 = rolling_sum(v * p * p, rolling_bars)
        n_in = np.minimum(np.arange(len(v)) + 1, rolling_bars)
    with np.errstate(divide="ignore", invalid="ignore"):
        vw = cvp / cv
        var = cvp2 / cv - vw * vw
    # relative floor guards against negative variance from floating point cancellation
    var = np.where(var < 0, 0.0, var)
    sigma = np.sqrt(var)
    vw = np.where(cv > 0, vw, np.nan)
    sigma = np.where(cv > 0, sigma, np.nan)
    return vw, sigma, n_in.astype(float)


def taker_buy_ratio(volume: Array, taker_buy: Array) -> Array:
    v = _f(volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = _f(taker_buy) / v
    return np.where(v > 0, r, 0.5)


def signed_volume(volume: Array, taker_buy: Array) -> Array:
    """Aggressive buy volume minus aggressive sell volume per bar."""
    return 2.0 * _f(taker_buy) - _f(volume)


def rolling_corr(a: Array, b: Array, n: int) -> Array:
    return pd.Series(_f(a)).rolling(n, min_periods=n).corr(pd.Series(_f(b))).to_numpy()


def ols_beta(y: Array, x: Array) -> tuple[float, float]:
    """OLS y = alpha + beta x. Returns (alpha, beta)."""
    y, x = _f(y), _f(x)
    m = np.isfinite(y) & np.isfinite(x)
    y, x = y[m], x[m]
    if len(y) < 3 or np.var(x) == 0:
        return 0.0, 0.0
    beta = np.cov(x, y, ddof=0)[0, 1] / np.var(x)
    alpha = y.mean() - beta * x.mean()
    return float(alpha), float(beta)


def half_life(spread: Array) -> float:
    """Ornstein-Uhlenbeck half-life (in bars) from AR(1) fit of the spread: ds = a + b*s_{t-1}."""
    s = _f(spread)
    s = s[np.isfinite(s)]
    if len(s) < 10:
        return float("inf")
    ds = np.diff(s)
    lag = s[:-1]
    _, b = ols_beta(ds, lag)
    if b >= 0:
        return float("inf")
    return float(-np.log(2.0) / np.log(1.0 + b)) if b > -1 else 0.5
