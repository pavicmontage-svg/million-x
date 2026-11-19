"""BOT 2 — EMA MOMENTUM: EMA 9/21/50 stacked trend, 15m confirmation,
pullback into the EMA21 zone, then a momentum resumption close."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class EMAMomentum(Strategy):
    bot_id = "bot2_ema"
    name = "EMA Momentum"
    description = "EMA 9/21/50 trend stack, 15m confirmation, pullback-to-EMA21 continuation."
    default_params = {"timeframe": 5, "pullback_lb": 3, "zone_atr": 0.15, "stop_atr": 0.3,
                      "rsi_lo": 45.0, "rsi_hi": 70.0, "r1": 1.0, "r2": 2.0}
    param_grid = {"pullback_lb": [2, 3, 5], "zone_atr": [0.1, 0.2], "r2": [1.6, 2.0, 2.5]}

    def compute(self, b):
        self.f.update(e9=ind.ema(b.close, 9), e21=ind.ema(b.close, 21), e50=ind.ema(b.close, 50),
                      rsi=ind.rsi(b.close, 14))

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        e9, e21, e50, a = f["e9"][j], f["e21"][j], f["e50"][j], f["atr"][j]
        if np.isnan(e50):
            return None
        lb = int(p["pullback_lb"])
        t15 = self.trend15_at(j)
        r = f["rsi"][j]
        lo_w, hi_w = slice(j - lb, j), slice(j - 5, j + 1)
        if e9 > e21 > e50 and t15 > 0:
            touched = (b.low[lo_w] <= f["e21"][lo_w] + p["zone_atr"] * a).any()
            if touched and b.close[j] > e9 and b.close[j - 1] <= f["e9"][j - 1] and b.close[j] > b.open[j] and p["rsi_lo"] <= r <= p["rsi_hi"]:
                stop = b.low[hi_w].min() - p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
                conf = 0.5 + 0.25 * min(1.0, (e9 - e50) / (2 * a))
                return Side.LONG, stop, tp1, tp2, conf, "EMA stack pullback long"
        if e9 < e21 < e50 and t15 < 0:
            touched = (b.high[lo_w] >= f["e21"][lo_w] - p["zone_atr"] * a).any()
            if touched and b.close[j] < e9 and b.close[j - 1] >= f["e9"][j - 1] and b.close[j] < b.open[j] and 100 - p["rsi_hi"] <= r <= 100 - p["rsi_lo"]:
                stop = b.high[hi_w].max() + p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
                conf = 0.5 + 0.25 * min(1.0, (e50 - e9) / (2 * a))
                return Side.SHORT, stop, tp1, tp2, conf, "EMA stack pullback short"
        return None
