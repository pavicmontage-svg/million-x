"""Market-data packet for the AI Independent Trader (built from the engine's real data).

Per symbol and per timeframe (1m, 5m, 15m): EMA 9/21/50, RSI 14, MACD 12/26/9, ATR 14,
session VWAP, volume, recent support/resistance (confirmed swing points), breakout /
false-breakout flags; plus a 24 h volume profile (POC, value area), order-book
imbalance and spread, funding rate and open interest when the exchange returns them.

The packet contains ONLY market data: no output of the six agents, so the trader's
analysis is independent. It gets a content hash id that every decision must cite.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Optional

import numpy as np

from .. import indicators as ind
from ..models import Bars


def _f(x: Any, d: int = 6) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, d)


def macd(x: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[float, float, float]:
    m = ind.ema(x, fast) - ind.ema(x, slow)
    m = m[~np.isnan(m)]
    if len(m) < sig + 1:
        return float("nan"), float("nan"), float("nan")
    s = ind.ema(m, sig)
    return float(m[-1]), float(s[-1]), float(m[-1] - s[-1])


def volume_profile(b: Bars, bins: int = 30, lookback: int = 1440) -> dict[str, Any]:
    n = min(len(b), lookback)
    if n < 30:
        return {}
    px = (b.high[-n:] + b.low[-n:] + b.close[-n:]) / 3
    vol = b.volume[-n:]
    lo, hi = float(b.low[-n:].min()), float(b.high[-n:].max())
    if hi <= lo:
        return {}
    hist, edges = np.histogram(px, bins=bins, range=(lo, hi), weights=vol)
    poc_i = int(np.argmax(hist))
    order = np.argsort(hist)[::-1]
    tot, acc, inside = hist.sum(), 0.0, []
    for i in order:
        acc += hist[i]
        inside.append(i)
        if acc >= 0.7 * tot:
            break
    return {"poc": _f((edges[poc_i] + edges[poc_i + 1]) / 2), "value_area_low": _f(edges[min(inside)]),
            "value_area_high": _f(edges[max(inside) + 1]), "range_low": _f(lo), "range_high": _f(hi)}


def tf_features(b: Bars, minutes: int) -> dict[str, Any]:
    if minutes > 1:
        b, _ = ind.resample(b, minutes)
    n = len(b)
    if n < 60:
        return {"bars": n, "note": "not enough history"}
    c = b.close
    e9, e21, e50 = ind.ema(c, 9)[-1], ind.ema(c, 21)[-1], ind.ema(c, 50)[-1]
    m, s, h = macd(c)
    atr = ind.atr(b, 14)[-1]
    vwap, _ = ind.session_vwap(b)
    k = min(n, 400)  # swing points from the recent part only (confirmed, no look-ahead)
    hi_w, lo_w = b.high[-k:], b.low[-k:]
    piv_hi = ind.pivots_confirmed(hi_w, 5, 2, "high")
    piv_lo = ind.pivots_confirmed(lo_w, 5, 2, "low")
    highs = [float(hi_w[i - 2]) for i in np.where(piv_hi)[0][-4:]]
    lows = [float(lo_w[i - 2]) for i in np.where(piv_lo)[0][-4:]]
    hh, ll = float(b.high[-41:-1].max()), float(b.low[-41:-1].min())
    last = float(c[-1])
    breakout = "up" if last > hh else ("down" if last < ll else "none")
    false_bo = ("failed_up" if float(b.high[-1]) > hh and last < hh else
                "failed_down" if float(b.low[-1]) < ll and last > ll else "none")
    vol20 = float(np.mean(b.volume[-21:-1])) if n > 21 else float(np.mean(b.volume))
    return {"close": _f(last), "ema9": _f(e9), "ema21": _f(e21), "ema50": _f(e50),
            "rsi14": _f(ind.rsi(c, 14)[-1], 2), "macd": _f(m), "macd_signal": _f(s), "macd_hist": _f(h),
            "atr14": _f(atr), "atr_pct": _f(atr / last * 100, 4), "vwap": _f(vwap[-1]),
            "vs_vwap_pct": _f((last / vwap[-1] - 1) * 100, 4) if vwap[-1] else None,
            "volume_vs_avg20": _f(float(b.volume[-1]) / vol20, 3) if vol20 else None,
            "resistance_swings": [_f(x) for x in highs], "support_swings": [_f(x) for x in lows],
            "range40_high": _f(hh), "range40_low": _f(ll), "breakout": breakout, "false_breakout": false_bo,
            "last_bar_open_time": int(b.open_time[-1])}


def _pivot_safe(fn):
    try:
        return fn()
    except Exception:  # pragma: no cover - defensive: indicators with short series
        return None


def build_packet(engine, symbols: Optional[list[str]] = None, with_derivatives: bool = True) -> dict[str, Any]:
    syms = symbols or list(engine.universe)[:10]
    out: dict[str, Any] = {}
    for sym in syms:
        b = engine.bars.get(sym)
        if b is None or len(b) < 120:
            continue
        row: dict[str, Any] = {"timeframes": {}}
        for tf in (1, 5, 15):
            row["timeframes"][f"{tf}m"] = _pivot_safe(lambda tf=tf: tf_features(b, tf)) or {"note": "unavailable"}
        row["volume_profile_24h"] = volume_profile(b)
        book = engine.books.get(sym)
        if book is not None:
            row["order_book"] = {"spread_bps": _f(book.spread_bps, 3), "imbalance_10": _f(book.imbalance(10), 4),
                                 "depth_10bps": [_f(x, 0) for x in book.depth_within(10)]}
        sc = engine.scanner.get(sym, {})
        row["regime"] = {"trend": sc.get("regime"), "volatility": sc.get("volatility"),
                         "atr_pct_15m": _f(sc.get("atr_pct_15m"), 5)}
        if with_derivatives:
            try:
                pi = engine.ex.premium_index(sym) or {}
                row["funding_rate"] = _f(pi.get("lastFundingRate"), 8)
                row["mark_price"] = _f(pi.get("markPrice"))
            except Exception:
                row["funding_rate"] = None
            try:
                oi = engine.ex.open_interest(sym) or {}
                row["open_interest"] = _f(oi.get("openInterest"), 3)
            except Exception:
                row["open_interest"] = None
        out[sym] = row
    body = json.dumps(out, sort_keys=True, default=str)
    pid = "mkt-" + hashlib.sha256(body.encode()).hexdigest()[:16]
    ts = max((int(engine.last_bar_ms.get(s, 0)) for s in out), default=0)
    return {"market_data_id": pid, "data_time_ms": ts, "symbols": out}
