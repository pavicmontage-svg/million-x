"""Synthetic market data for tests and offline demos.

The generator produces regime-switching OHLCV with known structure (trend, range/mean-reversion,
compression followed by expansion, high-volatility shocks) so that engines and the regime classifier
can be tested against ground truth. Synthetic data is *never* used to report strategy performance.
"""
from __future__ import annotations

import numpy as np

from .candles import TF_MS, Candles

REGIMES = ("trend_up", "trend_down", "range", "compression", "shock")


def regime_path(n: int, seed: int = 0, segment: tuple[int, int] = (150, 400)) -> list[str]:
    rng = np.random.default_rng(seed)
    out: list[str] = []
    while len(out) < n:
        r = REGIMES[rng.integers(0, len(REGIMES))]
        if r == "shock":
            length = int(rng.integers(10, 30))
        else:
            length = int(rng.integers(*segment))
        out.extend([r] * length)
    return out[:n]


def generate(n: int = 5000, *, symbol: str = "SYNTHUSDT", timeframe: str = "5m", start_price: float = 100.0,
             seed: int = 0, regimes: list[str] | None = None, start_ts: int = 1_767_225_600_000,
             base_vol: float = 0.0015) -> tuple[Candles, list[str]]:
    """Return (candles, per-bar regime label)."""
    rng = np.random.default_rng(seed)
    regimes = regimes or regime_path(n, seed)
    ts = start_ts + np.arange(n, dtype=np.int64) * TF_MS[timeframe]
    o = np.empty(n); h = np.empty(n); l = np.empty(n); c = np.empty(n)
    v = np.empty(n); tb = np.empty(n)
    price = start_price
    anchor = start_price
    prev_reg = None
    since_compression = 10**9
    for i, reg in enumerate(regimes):
        if reg != prev_reg:
            anchor = price
            since_compression = 0 if prev_reg == "compression" else 10**9
            prev_reg = reg
        since_compression += 1
        if reg == "trend_up":
            mu, sig = 0.0009, base_vol
        elif reg == "trend_down":
            mu, sig = -0.0009, base_vol
        elif reg == "range":
            mu, sig = -0.08 * np.log(price / anchor), base_vol
        elif reg == "compression":
            mu, sig = -0.05 * np.log(price / anchor), base_vol * 0.3
        else:  # shock
            mu, sig = 0.0, base_vol * 5
        ret = mu + sig * rng.standard_normal()
        vol_mult = {"shock": 4.0, "compression": 0.5}.get(reg, 1.0)
        if since_compression <= 6 and reg in ("trend_up", "trend_down"):
            vol_mult, ret = 3.0, ret + (0.002 if reg == "trend_up" else -0.002)  # expansion out of compression
        op = price
        cl = price * np.exp(ret)
        wick = abs(sig) * price * np.abs(rng.standard_normal(2)) * 0.8
        hi = max(op, cl) + wick[0]
        lo = min(op, cl) - wick[1]
        vol = max(1.0, rng.lognormal(np.log(1000 * vol_mult), 0.35) * (1 + 40 * abs(ret)))
        # aggressive-flow proxy correlated with the bar's return
        frac = np.clip(0.5 + 30 * ret + 0.05 * rng.standard_normal(), 0.02, 0.98)
        o[i], h[i], l[i], c[i], v[i], tb[i] = op, hi, lo, cl, vol, vol * frac
        price = cl
    qv = v * c
    trades = np.maximum(1, (v / 2).astype(int)).astype(float)
    return Candles(symbol, timeframe, ts, o, h, l, c, v, qv, trades, tb), regimes


def cointegrated_pair(n: int = 4000, *, beta: float = 1.5, theta: float = 0.05, seed: int = 1,
                      timeframe: str = "15m") -> tuple[Candles, Candles]:
    """Y = beta * X + OU noise (in log prices) — a genuinely cointegrated pair."""
    rng = np.random.default_rng(seed)
    lx = np.log(100.0) + np.cumsum(0.004 * rng.standard_normal(n))
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = s[i - 1] - theta * s[i - 1] + 0.002 * rng.standard_normal()
    ly = beta * lx + s - beta * lx[0] + np.log(50.0)
    # returned as (Y, X): Y is the dependent leg
    return _from_close("YUSDT", np.exp(ly), timeframe, rng), _from_close("XUSDT", np.exp(lx), timeframe, rng)


def random_walk_pair(n: int = 4000, seed: int = 2, timeframe: str = "15m") -> tuple[Candles, Candles]:
    rng = np.random.default_rng(seed)
    a = 100 * np.exp(np.cumsum(0.002 * rng.standard_normal(n)))
    b = 50 * np.exp(np.cumsum(0.002 * rng.standard_normal(n)))
    return _from_close("AUSDT", a, timeframe, rng), _from_close("BUSDT", b, timeframe, rng)


def _from_close(symbol: str, close: np.ndarray, timeframe: str, rng) -> Candles:
    n = len(close)
    o = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.standard_normal(n)) * 0.001 * close
    h = np.maximum(o, close) + spread
    l = np.minimum(o, close) - spread
    v = rng.lognormal(7, 0.3, n)
    ts = 1_767_225_600_000 + np.arange(n, dtype=np.int64) * TF_MS[timeframe]
    return Candles(symbol, timeframe, ts, o, h, l, close, v, v * close, np.maximum(1, v // 2), v * 0.5)


def synthetic_book(mid: float, *, levels: int = 20, tick: float = 0.01, depth: float = 50.0,
                   imbalance: float = 0.0, seed: int = 0) -> dict:
    """Order book snapshot with a controllable bid/ask imbalance in [-1, 1]."""
    rng = np.random.default_rng(seed)
    bid_mult, ask_mult = 1 + imbalance, 1 - imbalance
    bids = [[mid - tick * (i + 1), depth * bid_mult * (0.5 + rng.random())] for i in range(levels)]
    asks = [[mid + tick * (i + 1), depth * ask_mult * (0.5 + rng.random())] for i in range(levels)]
    return {"bids": bids, "asks": asks, "ts": 0}
