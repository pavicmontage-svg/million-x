"""Market Regime Intelligence 2.0 (MillionX 2.0, phase 2) — causal.

Ten regimes per instrument:
  STRONG_BULL_TREND, WEAK_BULL_TREND, STRONG_BEAR_TREND, WEAK_BEAR_TREND, SIDEWAYS,
  HIGH_VOLATILITY, LOW_VOLATILITY, VOLATILITY_EXPANSION, VOLATILITY_CONTRACTION,
  MARKET_DISLOCATION.

Timeframes: 1h = wider context, 15m = main trend (classification bar), 5m = setup
(VWAP deviation), 1m = shock detection. Every value at a 1-minute index uses ONLY
higher-timeframe candles that had CLOSED at that minute (indicators.resample keeps
complete groups only and the mapping uses the closing 1m index), so no strategy
can read an unfinished higher-timeframe candle.

Inputs: ADX / +DI / -DI, EMA slope (ATR-normalised), ATR percentile, realised
volatility percentile, VWAP z-score, volume expansion, market structure from
confirmed pivots, BTC direction and BTC correlation (when a BTC series is given).
Funding rate and open interest are used only when supplied (live); historical
klines do not contain them, and their absence is never treated as confirmation.
"""
from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from . import indicators as ind
from .models import Bars

REGIMES = ("STRONG_BULL_TREND", "WEAK_BULL_TREND", "STRONG_BEAR_TREND", "WEAK_BEAR_TREND", "SIDEWAYS",
           "HIGH_VOLATILITY", "LOW_VOLATILITY", "VOLATILITY_EXPANSION", "VOLATILITY_CONTRACTION",
           "MARKET_DISLOCATION")
TRENDING = {"STRONG_BULL_TREND", "WEAK_BULL_TREND", "STRONG_BEAR_TREND", "WEAK_BEAR_TREND"}


def rolling_percentile(x: np.ndarray, window: int, min_count: int = 50) -> np.ndarray:
    """Percentile (0..1) of x[i] within x[i-window+1 .. i] — strictly causal."""
    out = np.full(len(x), np.nan)
    win: deque = deque()
    srt: list[float] = []
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        win.append(v)
        bisect.insort(srt, v)
        if len(win) > window:
            old = win.popleft()
            del srt[bisect.bisect_left(srt, old)]
        if len(srt) >= min_count:
            out[i] = bisect.bisect_right(srt, v) / len(srt)
    return out


def _last_closed(close_idx: np.ndarray, i1: np.ndarray) -> np.ndarray:
    """Index of the last higher-TF bar whose closing 1m index is <= i1 (-1 if none)."""
    return np.searchsorted(close_idx, i1, side="right") - 1


def _structure(b: Bars, left: int = 3, right: int = 2) -> np.ndarray:
    """+1 higher highs & higher lows, -1 lower highs & lower lows, 0 mixed (confirmed pivots only)."""
    ph = ind.pivots_confirmed(b.high, left, right, "high")
    pl = ind.pivots_confirmed(b.low, left, right, "low")
    out = np.zeros(len(b))
    hs: list[float] = []
    ls: list[float] = []
    for i in range(len(b)):
        if ph[i]:
            hs = (hs + [float(b.high[i - right])])[-2:]
        if pl[i]:
            ls = (ls + [float(b.low[i - right])])[-2:]
        if len(hs) == 2 and len(ls) == 2:
            if hs[1] > hs[0] and ls[1] > ls[0]:
                out[i] = 1
            elif hs[1] < hs[0] and ls[1] < ls[0]:
                out[i] = -1
    return out


@dataclass
class Regime2:
    label: np.ndarray                    # per 15m bar
    features: dict[str, np.ndarray]      # per 15m bar
    close_idx15: np.ndarray
    extras: dict[str, Any] = field(default_factory=dict)

    def at_1m(self, i: int) -> tuple[str, dict[str, float]]:
        k = int(_last_closed(self.close_idx15, np.array([i]))[0])
        if k < 0:
            return "SIDEWAYS", {}
        return str(self.label[k]), {n: float(v[k]) for n, v in self.features.items()}


def classify(b1: Bars, btc1: Optional[Bars] = None, funding: Optional[float] = None,
             oi_change: Optional[float] = None, pct_window: int = 2880) -> Regime2:
    b15, c15 = ind.resample(b1, 15)
    b60, c60 = ind.resample(b1, 60)
    b5, c5 = ind.resample(b1, 5)
    n = len(b15)
    lab = np.full(n, "SIDEWAYS", dtype=object)
    if n < 60:
        return Regime2(lab, {}, c15)
    # --- 15m main trend
    adx = ind.adx(b15, 14)
    up = np.diff(b15.high, prepend=b15.high[0])
    dn = -np.diff(b15.low, prepend=b15.low[0])
    tr = ind.rma(ind.true_range(b15), 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * ind.rma(np.where((up > dn) & (up > 0), up, 0.0), 14) / tr
        mdi = 100 * ind.rma(np.where((dn > up) & (dn > 0), dn, 0.0), 14) / tr
    atr15 = ind.atr(b15, 14)
    e50 = ind.ema(b15.close, 50)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = (e50 - np.concatenate([np.full(8, np.nan), e50[:-8]])) / atr15
        atr_pct = atr15 / b15.close
        atr_ratio = atr15 / np.concatenate([np.full(20, np.nan), atr15[:-20]])
    atr_pctile = rolling_percentile(atr_pct, pct_window)
    struct = _structure(b15)
    vol_exp = b15.volume / np.maximum(ind.sma(b15.volume, 20), 1e-12)
    # --- 1m realised volatility (last 60 min) mapped to 15m closes
    lr = np.diff(np.log(b1.close), prepend=np.log(b1.close[0]))
    rv1 = ind.rolling_std(lr, 60)
    rv15 = rv1[c15]
    rv_pctile = rolling_percentile(rv15, pct_window)
    # shock: largest 5-minute move inside the 15m bar vs ATR15
    mv5 = np.abs(b1.close - np.concatenate([np.full(5, np.nan), b1.close[:-5]]))
    shock = np.array([np.nanmax(mv5[max(0, c - 14):c + 1]) if c >= 5 else np.nan for c in c15]) / atr15
    # --- 5m VWAP deviation z mapped to 15m closes
    vw, vsd = ind.session_vwap(b5)
    with np.errstate(divide="ignore", invalid="ignore"):
        vz5 = (b5.close - vw) / vsd
    k5 = _last_closed(c5, c15)
    vwap_z = np.where(k5 >= 0, vz5[np.clip(k5, 0, len(vz5) - 1)], np.nan) if len(vz5) else np.full(n, np.nan)
    # --- 1h context (last CLOSED hour at each 15m close)
    e50h = ind.ema(b60.close, 50) if len(b60) else np.zeros(0)
    atr60 = ind.atr(b60, 14) if len(b60) else np.zeros(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope_h = (e50h - np.concatenate([np.full(4, np.nan), e50h[:-4]])) / atr60 if len(b60) else np.zeros(0)
    kh = _last_closed(c60, c15)
    ctx_h = np.where(kh >= 0, slope_h[np.clip(kh, 0, max(0, len(slope_h) - 1))], np.nan) if len(slope_h) else np.full(n, np.nan)
    # --- BTC direction / correlation
    btc_dir = np.full(n, np.nan)
    btc_corr = np.full(n, np.nan)
    if btc1 is not None and len(btc1) > 0:
        bb15, bc15 = ind.resample(btc1, 15)
        be50 = ind.ema(bb15.close, 50)
        bslope = np.sign(be50 - np.concatenate([np.full(8, np.nan), be50[:-8]]))
        # align by open_time
        pos = np.searchsorted(bb15.open_time, b15.open_time)
        ok = (pos < len(bb15)) & (bb15.open_time[np.clip(pos, 0, len(bb15) - 1)] == b15.open_time)
        btc_dir[ok] = bslope[pos[ok]]
        r_a = np.diff(np.log(b15.close), prepend=np.nan)
        r_b = np.full(n, np.nan)
        r_bb = np.diff(np.log(bb15.close), prepend=np.nan)
        r_b[ok] = r_bb[pos[ok]]
        for i in range(96, n):
            x, y = r_a[i - 96:i + 1], r_b[i - 96:i + 1]
            m = ~(np.isnan(x) | np.isnan(y))
            if m.sum() > 50 and x[m].std() > 0 and y[m].std() > 0:
                btc_corr[i] = np.corrcoef(x[m], y[m])[0, 1]
    # --- classification (priority order)
    for i in range(n):
        if np.isnan(atr15[i]) or np.isnan(adx[i]):
            continue
        if (not np.isnan(shock[i]) and shock[i] > 3.0) or (not np.isnan(rv_pctile[i]) and rv_pctile[i] > 0.995):
            lab[i] = "MARKET_DISLOCATION"
            continue
        if not np.isnan(atr_ratio[i]) and atr_ratio[i] > 1.5 and (atr_pctile[i] if not np.isnan(atr_pctile[i]) else 0) > 0.6:
            lab[i] = "VOLATILITY_EXPANSION"
            continue
        if not np.isnan(atr_ratio[i]) and atr_ratio[i] < 0.7 and (atr_pctile[i] if not np.isnan(atr_pctile[i]) else 1) < 0.4:
            lab[i] = "VOLATILITY_CONTRACTION"
            continue
        bull_di, bear_di = pdi[i] > mdi[i], mdi[i] > pdi[i]
        s, st, h = slope[i], struct[i], ctx_h[i]
        if adx[i] >= 25 and bull_di and s > 0.5 and st >= 0 and (np.isnan(h) or h > 0):
            lab[i] = "STRONG_BULL_TREND"
        elif adx[i] >= 25 and bear_di and s < -0.5 and st <= 0 and (np.isnan(h) or h < 0):
            lab[i] = "STRONG_BEAR_TREND"
        elif adx[i] >= 18 and bull_di and s > 0.2:
            lab[i] = "WEAK_BULL_TREND"
        elif adx[i] >= 18 and bear_di and s < -0.2:
            lab[i] = "WEAK_BEAR_TREND"
        elif not np.isnan(atr_pctile[i]) and atr_pctile[i] >= 0.8:
            lab[i] = "HIGH_VOLATILITY"
        elif not np.isnan(atr_pctile[i]) and atr_pctile[i] <= 0.2:
            lab[i] = "LOW_VOLATILITY"
        else:
            lab[i] = "SIDEWAYS"
    feats = {"adx": adx, "plus_di": pdi, "minus_di": mdi, "ema_slope": slope, "atr_pct": atr_pct,
             "atr_pctile": atr_pctile, "atr_ratio": atr_ratio, "rv_pctile": rv_pctile, "vwap_z": vwap_z,
             "vol_expansion": vol_exp, "structure": struct, "ctx_1h_slope": ctx_h, "shock_atr": shock,
             "btc_dir": btc_dir, "btc_corr": btc_corr}
    extras = {"funding": funding, "oi_change": oi_change,
              "derivatives_note": "funding/OI used only when supplied (live); absent in historical klines"}
    return Regime2(lab, feats, c15, extras)
