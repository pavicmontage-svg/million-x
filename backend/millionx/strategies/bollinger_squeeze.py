"""BOT 5 — BOLLINGER SQUEEZE: volatility compression (Bollinger Bands inside
Keltner Channels for N bars), entry on the first confirmed expansion close
outside the band in the direction of momentum."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class BollingerSqueeze(Strategy):
    bot_id = "bot5_squeeze"
    name = "Bollinger Squeeze"
    description = "BB-inside-Keltner compression, momentum-confirmed expansion breakout."
    default_params = {"timeframe": 5, "bb_n": 20, "bb_k": 2.0, "kc_k": 1.5, "min_squeeze": 6,
                      "stop_atr": 1.5, "r1": 1.0, "r2": 2.0, "vol_mult": 1.2}
    param_grid = {"min_squeeze": [4, 6, 10], "kc_k": [1.3, 1.5, 2.0], "r2": [1.6, 2.0, 2.6]}

    def compute(self, b):
        p = self.params
        bl, bm, bu = ind.bollinger(b.close, int(p["bb_n"]), p["bb_k"])
        kl, km, ku = ind.keltner(b, int(p["bb_n"]), p["kc_k"])
        sq = (bu < ku) & (bl > kl)
        run = np.zeros(len(b))
        for i in range(1, len(b)):
            run[i] = run[i - 1] + 1 if sq[i] else 0
        mom = b.close - bm
        self.f.update(bl=bl, bm=bm, bu=bu, sq=sq.astype(float), run=run, mom=mom)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        if j < 2 or np.isnan(f["bu"][j]):
            return None
        released = f["sq"][j] == 0 and f["run"][j - 1] >= p["min_squeeze"]
        if not released or self.vol_ratio(j) < p["vol_mult"]:
            return None
        a = f["atr"][j]
        rising = f["mom"][j] > f["mom"][j - 1] > 0
        falling = f["mom"][j] < f["mom"][j - 1] < 0
        if b.close[j] > f["bu"][j] and rising and self.trend15_at(j) >= 0:
            stop = max(f["bm"][j], b.close[j] - p["stop_atr"] * a)
            if b.close[j] - stop < 0.3 * a:
                stop = b.close[j] - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.55 + min(0.3, f["run"][j - 1] / 60), "squeeze release long"
        if b.close[j] < f["bl"][j] and falling and self.trend15_at(j) <= 0:
            stop = min(f["bm"][j], b.close[j] + p["stop_atr"] * a)
            if stop - b.close[j] < 0.3 * a:
                stop = b.close[j] + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.55 + min(0.3, f["run"][j - 1] / 60), "squeeze release short"
        return None
