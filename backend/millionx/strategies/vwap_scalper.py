"""BOT 1 — VWAP SCALPER: mean reversion toward session VWAP after an
over-extension beyond the VWAP deviation band, confirmed by a volume spike and
a rejection close back inside the band. Avoided in strong 15m trends."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class VWAPScalper(Strategy):
    bot_id = "bot1_vwap"
    name = "VWAP Scalper"
    description = "Session VWAP deviation band fade with volume spike and rejection close."
    default_params = {"timeframe": 3, "band_k": 2.0, "vol_mult": 1.5, "max_adx15": 28.0,
                      "stop_atr": 0.25, "min_rr": 1.2}
    param_grid = {"band_k": [1.5, 2.0, 2.5], "vol_mult": [1.3, 1.5, 2.0], "stop_atr": [0.2, 0.35]}

    def compute(self, b):
        vwap, sd = ind.session_vwap(b)
        self.f.update(vwap=vwap, sd=sd)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        if j < 3 or np.isnan(f["vwap"][j]) or f["sd"][j] <= 0:
            return None
        k15 = self.md.idx15_at_1m(int(self.close_idx[j]))
        if k15 >= 0 and self.md.regime.adx[k15] > p["max_adx15"]:
            return None
        vw, sd, a = f["vwap"][j], f["sd"][j], f["atr"][j]
        up, lo = vw + p["band_k"] * sd, vw - p["band_k"] * sd
        spike = max(self.vol_ratio(j), self.vol_ratio(j - 1)) >= p["vol_mult"]
        if not spike:
            return None
        c = b.close
        # long: previous bar closed below lower band, current closes back inside
        if c[j - 1] < f["vwap"][j - 1] - p["band_k"] * f["sd"][j - 1] and lo < c[j] < vw:
            stop = b.low[j - 2:j + 1].min() - p["stop_atr"] * a
            entry = c[j]
            tp2 = vw
            tp1 = entry + (tp2 - entry) * 0.5
            if (tp2 - entry) / (entry - stop) < p["min_rr"]:
                return None
            dev = (vw - c[j - 1]) / sd
            return Side.LONG, stop, tp1, tp2, min(1.0, 0.4 + 0.1 * dev), f"VWAP fade long dev={dev:.1f}sd"
        if c[j - 1] > f["vwap"][j - 1] + p["band_k"] * f["sd"][j - 1] and vw < c[j] < up:
            stop = b.high[j - 2:j + 1].max() + p["stop_atr"] * a
            entry = c[j]
            tp2 = vw
            tp1 = entry - (entry - tp2) * 0.5
            if (entry - tp2) / (stop - entry) < p["min_rr"]:
                return None
            dev = (c[j - 1] - vw) / sd
            return Side.SHORT, stop, tp1, tp2, min(1.0, 0.4 + 0.1 * dev), f"VWAP fade short dev={dev:.1f}sd"
        return None
