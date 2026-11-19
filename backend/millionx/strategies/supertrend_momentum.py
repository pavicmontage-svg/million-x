"""BOT 8 — SUPERTREND MOMENTUM: Supertrend flip on the trading timeframe,
aligned with the 15m Supertrend, price on the right side of EMA50 and ADX
showing a trending market."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class SupertrendMomentum(Strategy):
    bot_id = "bot8_supertrend"
    name = "Supertrend Momentum"
    description = "Supertrend flip + 15m Supertrend alignment + EMA50 + ADX filter."
    default_params = {"timeframe": 5, "st_n": 10, "st_mult": 3.0, "ema_n": 50, "min_adx": 18.0,
                      "max_stop_atr": 3.0, "r1": 1.0, "r2": 2.0}
    param_grid = {"st_mult": [2.0, 2.5, 3.0], "min_adx": [15.0, 20.0, 25.0], "r2": [1.6, 2.0, 3.0]}

    def compute(self, b):
        p = self.params
        line, d = ind.supertrend(b, int(p["st_n"]), p["st_mult"])
        b15 = self.md.tf(15)[0]
        _, d15 = ind.supertrend(b15, int(p["st_n"]), p["st_mult"]) if len(b15) > 20 else (None, np.zeros(len(b15)))
        self.f.update(st=line, dir=d, ema=ind.ema(b.close, int(p["ema_n"])), adx=ind.adx(b, 14))
        self._d15 = d15

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        if j < 1 or np.isnan(f["ema"][j]) or f["adx"][j] < p["min_adx"]:
            return None
        k = self.md.idx15_at_1m(int(self.close_idx[j]))
        d15 = self._d15[k] if k >= 0 and k < len(self._d15) else 0
        a = f["atr"][j]
        flip_up = f["dir"][j] == 1 and f["dir"][j - 1] == -1
        flip_dn = f["dir"][j] == -1 and f["dir"][j - 1] == 1
        if flip_up and d15 == 1 and b.close[j] > f["ema"][j]:
            stop = f["st"][j]
            if b.close[j] - stop > p["max_stop_atr"] * a:
                return None
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.45 + min(0.4, f["adx"][j] / 100), "supertrend flip up"
        if flip_dn and d15 == -1 and b.close[j] < f["ema"][j]:
            stop = f["st"][j]
            if stop - b.close[j] > p["max_stop_atr"] * a:
                return None
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.45 + min(0.4, f["adx"][j] / 100), "supertrend flip down"
        return None
