"""BOT 7 — LIQUIDITY HUNTER: detects sweeps of resting liquidity (recent swing
highs/lows and the previous UTC day's high/low) where price pierces the level
and closes back inside on elevated volume (stop-hunt reversal)."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side, MINUTE_MS
from .base import Strategy

DAY_MS = 24 * 60 * MINUTE_MS


class LiquidityHunter(Strategy):
    bot_id = "bot7_liquidity"
    name = "Liquidity Hunter"
    description = "Swing / prior-day level sweep with close back inside and volume spike."
    default_params = {"timeframe": 3, "swing_n": 80, "exclude_recent": 3, "min_pierce_atr": 0.1,
                      "vol_mult": 1.6, "stop_atr": 0.15, "r1": 1.0, "r2": 2.5}
    param_grid = {"swing_n": [40, 80, 160], "vol_mult": [1.4, 1.6, 2.0], "r2": [2.0, 2.5, 3.0]}

    def compute(self, b):
        p = self.params
        n, ex = len(b), int(p["exclude_recent"])
        hi = ind.rolling_max(b.high, int(p["swing_n"]))
        lo = ind.rolling_min(b.low, int(p["swing_n"]))
        sh = np.concatenate([np.full(ex + 1, np.nan), hi[:-(ex + 1)]])
        sl = np.concatenate([np.full(ex + 1, np.nan), lo[:-(ex + 1)]])
        day = b.open_time // DAY_MS
        pdh = np.full(n, np.nan)
        pdl = np.full(n, np.nan)
        cur, h, l, ph, pl = None, -np.inf, np.inf, np.nan, np.nan
        for i in range(n):
            if day[i] != cur:
                if cur is not None:
                    ph, pl = h, l
                cur, h, l = day[i], -np.inf, np.inf
            pdh[i], pdl[i] = ph, pl
            h, l = max(h, b.high[i]), min(l, b.low[i])
        self.f.update(sh=sh, sl=sl, pdh=pdh, pdl=pdl)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        a = f["atr"][j]
        if self.vol_ratio(j) < p["vol_mult"]:
            return None
        lows = [x for x in (f["sl"][j], f["pdl"][j]) if not np.isnan(x)]
        highs = [x for x in (f["sh"][j], f["pdh"][j]) if not np.isnan(x)]
        for lvl in lows:
            if b.low[j] < lvl - p["min_pierce_atr"] * a and b.close[j] > lvl:
                stop = b.low[j] - p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
                opp = [x for x in highs if x > tp1]
                if opp:
                    tp2 = max(tp1, min(min(opp), tp2))
                wick = (min(b.open[j], b.close[j]) - b.low[j]) / max(b.high[j] - b.low[j], 1e-12)
                return Side.LONG, stop, tp1, tp2, 0.4 + 0.4 * wick, f"sell-side sweep of {lvl:.4f}"
        for lvl in highs:
            if b.high[j] > lvl + p["min_pierce_atr"] * a and b.close[j] < lvl:
                stop = b.high[j] + p["stop_atr"] * a
                tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
                opp = [x for x in lows if x < tp1]
                if opp:
                    tp2 = min(tp1, max(max(opp), tp2))
                wick = (b.high[j] - max(b.open[j], b.close[j])) / max(b.high[j] - b.low[j], 1e-12)
                return Side.SHORT, stop, tp1, tp2, 0.4 + 0.4 * wick, f"buy-side sweep of {lvl:.4f}"
        return None
