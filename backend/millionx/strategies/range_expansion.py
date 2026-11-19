"""BOT 9 — RANGE EXPANSION BREAKOUT (replacement slot).

IMPORTANT: this is NOT "Moneyline Breakout Entry v3.5". No public definition
of that indicator could be found, so by the owner's decision slot 9 runs a
separate, fully documented breakout method until the original source is
provided:

  1. Compression: an NR7 bar (narrowest range of the last 7) while
     ATR(5)/ATR(50) < compression_ratio.
  2. Trigger: within `valid_bars` after the NR7 bar, a close beyond the NR7
     high/low by `trigger_atr`*ATR, with range expansion (bar range >
     expansion_mult*ATR) and volume > vol_mult * average.
  3. Filter: not against the 15m trend.
  4. Stop: opposite side of the NR7 bar; targets in R multiples.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class RangeExpansionBreakout(Strategy):
    bot_id = "bot9_rangeexp"
    name = "Range Expansion Breakout (slot 9 — not Moneyline)"
    description = "NR7 + ATR compression, expansion breakout with volume; replaces Moneyline v3.5 until its source is supplied."
    is_replacement = True
    default_params = {"timeframe": 5, "nr_n": 7, "compression_ratio": 0.75, "valid_bars": 3,
                      "trigger_atr": 0.1, "expansion_mult": 1.2, "vol_mult": 1.5, "r1": 1.0, "r2": 2.0}
    param_grid = {"compression_ratio": [0.65, 0.75, 0.85], "valid_bars": [2, 3, 5], "r2": [1.6, 2.0, 2.6]}

    def compute(self, b):
        p = self.params
        rng = b.high - b.low
        nrmin = ind.rolling_min(rng, int(p["nr_n"]))
        a5 = ind.atr(b, 5)
        a50 = ind.atr(b, 50)
        with np.errstate(invalid="ignore", divide="ignore"):
            comp = (rng <= nrmin) & (a5 / a50 < p["compression_ratio"])
        n = len(b)
        sig = np.zeros(n)
        stop = np.full(n, np.nan)
        vavg = ind.sma(b.volume, 20)
        a = self.f["atr"]
        nh = nl = np.nan
        age = 10 ** 9
        for i in range(n):
            if age < p["valid_bars"] and not np.isnan(a[i]) and not np.isnan(vavg[i - 1] if i else np.nan):
                expand = rng[i] > p["expansion_mult"] * a[i]
                vol_ok = b.volume[i] > p["vol_mult"] * vavg[i - 1]
                if expand and vol_ok and b.close[i] > nh + p["trigger_atr"] * a[i]:
                    sig[i], stop[i], age = 1, nl, 10 ** 9
                    continue
                if expand and vol_ok and b.close[i] < nl - p["trigger_atr"] * a[i]:
                    sig[i], stop[i], age = -1, nh, 10 ** 9
                    continue
            if comp[i]:
                nh, nl, age = b.high[i], b.low[i], 0
            else:
                age += 1
        self.f.update(sig=sig, stp=stop)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        s = f["sig"][j]
        if s == 0:
            return None
        t = self.trend15_at(j)
        if s > 0 and t >= 0:
            tp1, tp2 = self.levels(Side.LONG, b.close[j], f["stp"][j], p["r1"], p["r2"])
            return Side.LONG, f["stp"][j], tp1, tp2, 0.55, "NR7 compression expansion up"
        if s < 0 and t <= 0:
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], f["stp"][j], p["r1"], p["r2"])
            return Side.SHORT, f["stp"][j], tp1, tp2, 0.55, "NR7 compression expansion down"
        return None
