"""Market regime detection on 15m bars (causal).

Trend regime: BULL / BEAR / SIDEWAYS from EMA50 slope (ATR-normalised), ADX and
price location. Volatility regime: HIGH / NORMAL / LOW from a z-score of ATR%
against its own trailing distribution.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import indicators as ind
from .models import Bars, Regime, VolRegime


@dataclass
class RegimeSeries:
    trend: np.ndarray        # array of Regime values (object)
    vol: np.ndarray          # array of VolRegime values (object)
    atr_pct: np.ndarray      # ATR / close on 15m
    adx: np.ndarray

    def at(self, j: int) -> tuple[str, str]:
        if j < 0 or j >= len(self.trend):
            return Regime.SIDEWAYS.value, VolRegime.NORMAL.value
        return self.trend[j], self.vol[j]


def detect(b15: Bars, ema_n: int = 50, slope_lb: int = 8, adx_n: int = 14,
           slope_thr: float = 0.6, adx_thr: float = 20.0, vol_lb: int = 960) -> RegimeSeries:
    n = len(b15)
    trend = np.full(n, Regime.SIDEWAYS.value, dtype=object)
    vol = np.full(n, VolRegime.NORMAL.value, dtype=object)
    if n == 0:
        return RegimeSeries(trend, vol, np.zeros(0), np.zeros(0))
    e = ind.ema(b15.close, ema_n)
    a = ind.atr(b15, 14)
    ax = ind.adx(b15, adx_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (e - np.concatenate([np.full(slope_lb, np.nan), e[:-slope_lb]])) / a
        atr_pct = a / b15.close
    bull = (slope > slope_thr) & (ax > adx_thr) & (b15.close > e)
    bear = (slope < -slope_thr) & (ax > adx_thr) & (b15.close < e)
    trend[bull] = Regime.BULL.value
    trend[bear] = Regime.BEAR.value
    z = ind.zscore(np.nan_to_num(atr_pct, nan=0.0), min(vol_lb, max(20, n // 2)))
    vol[z > 1.0] = VolRegime.HIGH.value
    vol[z < -0.8] = VolRegime.LOW.value
    return RegimeSeries(trend, vol, atr_pct, ax)


def regime_key(trend: str, vol: str) -> str:
    return f"{trend}|{vol}"
