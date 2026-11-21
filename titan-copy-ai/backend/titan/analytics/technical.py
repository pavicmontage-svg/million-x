"""Technical state from real OHLC candles: trend, ATR volatility ratio, regime."""
from __future__ import annotations

import numpy as np


def atr(high, low, close, n: int = 14) -> np.ndarray:
    h, l, c = map(lambda a: np.asarray(a, dtype=float), (high, low, close))
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    out = np.empty_like(tr)
    out[0] = tr[0]
    for i in range(1, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def ema(x, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    a = 2 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def technical_state(candles: list[dict]) -> dict:
    """candles: [{t, o, h, l, c}] oldest first. Needs >= 120 candles, else UNKNOWN fields."""
    if len(candles) < 120:
        return {"trend": None, "vol_ratio": None, "regime": None, "candles": len(candles)}
    h = [k["h"] for k in candles]
    l = [k["l"] for k in candles]
    c = np.asarray([k["c"] for k in candles], dtype=float)
    a = atr(h, l, c)
    vol_ratio = float(a[-1] / np.mean(a[-90:])) if np.mean(a[-90:]) > 0 else None
    fast, slow = ema(c, 20), ema(c, 100)
    slope = (slow[-1] - slow[-20]) / slow[-20] * 100
    trend = "up" if fast[-1] > slow[-1] and slope > 0 else "down" if fast[-1] < slow[-1] and slope < 0 else "flat"
    # Efficiency ratio (Kaufman): directional move / path length over 50 bars
    seg = c[-50:]
    er = float(abs(seg[-1] - seg[0]) / np.sum(np.abs(np.diff(seg)))) if np.sum(np.abs(np.diff(seg))) > 0 else 0.0
    regime = "high_volatility" if vol_ratio and vol_ratio > 1.6 else "trending" if er > 0.35 else "ranging"
    return {"trend": trend, "vol_ratio": vol_ratio, "efficiency_ratio": er, "regime": regime,
            "atr": float(a[-1]), "last_close": float(c[-1]), "ema20": float(fast[-1]), "ema100": float(slow[-1]),
            "candles": len(candles)}
