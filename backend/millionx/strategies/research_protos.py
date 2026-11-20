"""MillionX 2.0 research prototypes A-D.

RESEARCH ONLY: they are registered with `register_research`, so they are built only when a
research run asks for them by id. The live/paper engine never trades them. Every
feature is causal: a higher-timeframe value is read from the last CLOSED candle of that
timeframe at the 1m bar that closes the strategy's own bar.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .. import indicators as ind
from ..models import Side
from . import register_research
from .base import Strategy


class _HTF(Strategy):
    """Helper: 1h trend (EMA50 slope + price side) and 15m ADX/ATR% read causally."""

    def _htf(self) -> None:
        b60, ci60 = self.md.tf(60)
        e50 = ind.ema(b60.close, 50) if len(b60) else np.zeros(0)
        slope = np.full(len(b60), np.nan)
        if len(b60) > 6:
            slope[5:] = (e50[5:] - e50[:-5]) / np.where(e50[:-5] > 0, e50[:-5], np.nan)
        t60 = np.zeros(len(b60))
        if len(b60):
            t60[(b60.close > e50) & (slope > 0)] = 1
            t60[(b60.close < e50) & (slope < 0)] = -1
        b15, ci15 = self.md.tf(15)
        adx15 = ind.adx(b15, 14) if len(b15) > 30 else np.full(len(b15), np.nan)
        atr15 = ind.atr(b15, 14) if len(b15) else np.zeros(0)
        n = len(self.b)
        self.f["t60"] = np.zeros(n)
        self.f["adx15"] = np.full(n, np.nan)
        self.f["atrp15"] = np.full(n, np.nan)
        for j in range(n):
            i1 = int(self.close_idx[j])
            k = ind.last_closed_index(ci60, i1)
            if k >= 0:
                self.f["t60"][j] = t60[k]
            k = ind.last_closed_index(ci15, i1)
            if k >= 0:
                self.f["adx15"][j] = adx15[k]
                self.f["atrp15"][j] = atr15[k] / b15.close[k] if b15.close[k] > 0 else np.nan


@register_research
class ProtoTrendPullback(_HTF):
    """A — Trend Pullback Continuation: 1h trend + 15m trend agree, 5m pullback into the
    EMA21 band with an RSI reset, entry when price reclaims EMA9 on volume."""
    bot_id = "protoA_trend_pullback"
    name = "Proto A Trend Pullback"
    description = "1h+15m trend, 5m pullback to EMA21 with RSI reset, EMA9 reclaim entry (research only)."
    default_params = {"timeframe": 5, "band_atr": 0.3, "rsi_reset": 45.0, "vol_min": 1.0, "stop_atr": 0.5,
                      "r1": 1.5, "r2": 3.0}
    param_grid = {"band_atr": [0.2, 0.3, 0.5], "rsi_reset": [40.0, 45.0, 50.0], "r2": [2.0, 3.0]}

    def compute(self, b):
        self.f.update(e9=ind.ema(b.close, 9), e21=ind.ema(b.close, 21), rsi=ind.rsi(b.close, 14))
        self._htf()

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        a = f["atr"][j]
        t60, t15 = f["t60"][j], self.trend15_at(j)
        if t60 == 0 or t15 != t60 or j < 8:
            return None
        w = slice(j - 6, j)
        rmin, rmax = np.nanmin(f["rsi"][w]), np.nanmax(f["rsi"][w])
        vr = self.vol_ratio(j)
        if t60 > 0:
            touched = (b.low[w] <= f["e21"][w] + p["band_atr"] * a).any()
            if touched and rmin <= p["rsi_reset"] and b.close[j] > f["e9"][j] and b.close[j - 1] <= f["e9"][j - 1] \
                    and vr >= p["vol_min"]:
                stop = b.low[slice(j - 6, j + 1)].min() - p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
                return Side.LONG, stop, tp1, tp2, 0.55, "A: 1h/15m up, pullback + EMA9 reclaim"
        else:
            touched = (b.high[w] >= f["e21"][w] - p["band_atr"] * a).any()
            if touched and rmax >= 100 - p["rsi_reset"] and b.close[j] < f["e9"][j] and b.close[j - 1] >= f["e9"][j - 1] \
                    and vr >= p["vol_min"]:
                stop = b.high[slice(j - 6, j + 1)].max() + p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
                return Side.SHORT, stop, tp1, tp2, 0.55, "A: 1h/15m down, pullback + EMA9 loss"
        return None


@register_research
class ProtoSweepReversal(_HTF):
    """B — Liquidity Sweep Reversal: a 5m candle trades beyond a confirmed 15m swing level
    and closes back inside on a volume spike, only when the 15m trend is weak (ADX)."""
    bot_id = "protoB_sweep_reversal"
    name = "Proto B Sweep Reversal"
    description = "Sweep of a confirmed 15m swing with close back inside and volume spike, weak-trend only (research only)."
    default_params = {"timeframe": 5, "pivot": 3, "min_sweep_atr": 0.15, "vol_spike": 1.5, "adx_max": 25.0,
                      "stop_atr": 0.2, "r1": 1.0, "r2": 2.0}
    param_grid = {"min_sweep_atr": [0.1, 0.2, 0.3], "vol_spike": [1.3, 1.8], "adx_max": [20.0, 25.0, 30.0]}

    def compute(self, b):
        self._htf()
        b15, ci15 = self.md.tf(15)
        k = int(self.params["pivot"])
        # last swing level confirmed by 15m bar t (pivot at t-k, known only at t): causal, forward-filled
        ph, pl = np.full(len(b15), np.nan), np.full(len(b15), np.nan)
        if len(b15):
            mh = ind.pivots_confirmed(b15.high, k, k, "high")
            ml = ind.pivots_confirmed(b15.low, k, k, "low")
            h = l = np.nan
            for t in range(len(b15)):
                if mh[t]:
                    h = b15.high[t - k]
                if ml[t]:
                    l = b15.low[t - k]
                ph[t], pl[t] = h, l
        n = len(b)
        self.f["swh"], self.f["swl"] = np.full(n, np.nan), np.full(n, np.nan)
        for j in range(n):
            q = ind.last_closed_index(ci15, int(self.close_idx[j]))
            if q >= 0:
                self.f["swh"][j], self.f["swl"][j] = ph[q], pl[q]

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        a, adx = f["atr"][j], f["adx15"][j]
        if np.isnan(adx) or adx > p["adx_max"] or self.vol_ratio(j) < p["vol_spike"]:
            return None
        lo, hi = f["swl"][j], f["swh"][j]
        if not np.isnan(lo) and b.low[j] < lo - p["min_sweep_atr"] * a and b.close[j] > lo and b.close[j] > b.open[j]:
            stop = b.low[j] - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.5, "B: sell-side sweep reclaimed"
        if not np.isnan(hi) and b.high[j] > hi + p["min_sweep_atr"] * a and b.close[j] < hi and b.close[j] < b.open[j]:
            stop = b.high[j] + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.5, "B: buy-side sweep rejected"
        return None


@register_research
class ProtoBreakoutRetest(_HTF):
    """C — Volatility Breakout Retest: 15m Bollinger width in its lowest percentile, a 15m
    close beyond the 20-bar Donchian channel, then a 5m retest of that level that holds."""
    bot_id = "protoC_breakout_retest"
    name = "Proto C Breakout Retest"
    description = "15m squeeze, Donchian breakout, 5m retest that holds the level (research only)."
    default_params = {"timeframe": 5, "squeeze_pct": 0.2, "retest_bars": 12, "tol_atr": 0.2, "stop_atr": 0.4,
                      "r1": 1.5, "r2": 3.0}
    param_grid = {"squeeze_pct": [0.1, 0.2, 0.3], "retest_bars": [6, 12, 24], "r2": [2.0, 3.0]}

    def compute(self, b):
        self._htf()
        b15, ci15 = self.md.tf(15)
        n15 = len(b15)
        brk_lvl, brk_side = np.full(n15, np.nan), np.zeros(n15)
        if n15 > 120:
            mid, up, lo = ind.bollinger(b15.close, 20, 2.0)
            width = (up - lo) / np.where(mid > 0, mid, np.nan)
            from ..regime2 import rolling_percentile
            wp = rolling_percentile(width, 500)
            dh, dl = ind.donchian(b15, 20)
            for t in range(21, n15):
                win = wp[max(0, t - 8):t]
                squeezed = t > 8 and bool(np.any(win[~np.isnan(win)] <= self.params["squeeze_pct"]))
                # donchian() already excludes bar t from its own channel
                if squeezed and b15.close[t] > dh[t]:
                    brk_lvl[t], brk_side[t] = dh[t], 1
                elif squeezed and b15.close[t] < dl[t]:
                    brk_lvl[t], brk_side[t] = dl[t], -1
        n = len(b)
        self.f["lvl"], self.f["side"], self.f["age"] = np.full(n, np.nan), np.zeros(n), np.full(n, 1e9)
        last_t = -1
        for j in range(n):
            i1 = int(self.close_idx[j])
            q = ind.last_closed_index(ci15, i1)
            # most recent breakout at or before q
            while last_t + 1 <= q:
                last_t += 1
                if brk_side[last_t] != 0:
                    self._cur = (brk_lvl[last_t], brk_side[last_t], int(ci15[last_t]))
            cur = getattr(self, "_cur", None)
            if cur is not None:
                self.f["lvl"][j], self.f["side"][j] = cur[0], cur[1]
                self.f["age"][j] = (i1 - cur[2]) / self.timeframe
        self._cur = None

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        a, lvl, s = f["atr"][j], f["lvl"][j], f["side"][j]
        if s == 0 or np.isnan(lvl) or f["age"][j] > p["retest_bars"] or f["age"][j] < 1:
            return None
        tol = p["tol_atr"] * a
        if s > 0 and b.low[j] <= lvl + tol and b.close[j] > lvl and b.close[j] > b.open[j]:
            stop = min(b.low[j], lvl) - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.55, "C: squeeze breakout retest held"
        if s < 0 and b.high[j] >= lvl - tol and b.close[j] < lvl and b.close[j] < b.open[j]:
            stop = max(b.high[j], lvl) + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.55, "C: squeeze breakdown retest held"
        return None


@register_research
class ProtoCostAwareMomentum(_HTF):
    """D — Cost-Aware Momentum: 15m bars, trades only when the 15m ATR is large relative to
    round-trip costs (so fees stay a small share of risk), in the direction of the 1h trend,
    on a close beyond the high/low of the previous N bars with ATR-normalised momentum."""
    bot_id = "protoD_cost_momentum"
    name = "Proto D Cost-Aware Momentum"
    description = "15m momentum breakout with 1h trend, only when ATR >> costs (research only)."
    default_params = {"timeframe": 15, "lookback": 8, "mom_min": 1.0, "min_atr_cost_mult": 8.0,
                      "round_trip_cost": 0.0014, "stop_atr": 1.2, "r1": 1.5, "r2": 2.5}
    param_grid = {"lookback": [6, 8, 12], "mom_min": [0.8, 1.2], "min_atr_cost_mult": [6.0, 10.0]}

    def compute(self, b):
        self._htf()
        n = int(self.params["lookback"])
        self.f.update(hh=np.r_[np.full(1, np.nan), ind.rolling_max(b.high, n)[:-1]],
                      ll=np.r_[np.full(1, np.nan), ind.rolling_min(b.low, n)[:-1]])
        mom = np.full(len(b), np.nan)
        if len(b) > n:
            mom[n:] = (b.close[n:] - b.close[:-n])
        self.f["mom"] = mom

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        a, c = f["atr"][j], b.close[j]
        if a / c < p["min_atr_cost_mult"] * p["round_trip_cost"] / p["stop_atr"]:
            return None  # stop distance would not be >= min_atr_cost_mult x round-trip cost
        m = f["mom"][j] / a if a > 0 else 0
        t60 = f["t60"][j]
        if t60 > 0 and c > f["hh"][j] and m >= p["mom_min"]:
            stop = c - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, c, stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.55, "D: cost-aware momentum long"
        if t60 < 0 and c < f["ll"][j] and m <= -p["mom_min"]:
            stop = c + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, c, stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.55, "D: cost-aware momentum short"
        return None
