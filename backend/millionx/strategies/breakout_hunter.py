"""BOT 4 — BREAKOUT HUNTER: UTC-session opening range (first 30 min) and
Donchian(20) breakouts with volume confirmation; entry on a successful retest
of the broken level (not on the first impulse)."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side, MINUTE_MS
from .base import Strategy

DAY_MS = 24 * 60 * MINUTE_MS


class BreakoutHunter(Strategy):
    bot_id = "bot4_breakout"
    name = "Breakout Hunter"
    description = "Opening-range + Donchian breakout, volume-confirmed, retest entry."
    default_params = {"timeframe": 5, "or_minutes": 30, "donchian_n": 20, "vol_mult": 1.8,
                      "retest_bars": 6, "retest_atr": 0.15, "stop_atr": 0.5, "r1": 1.0, "r2": 2.5}
    param_grid = {"donchian_n": [20, 40], "vol_mult": [1.5, 1.8, 2.2], "r2": [2.0, 2.5, 3.0]}

    def compute(self, b):
        p = self.params
        n = len(b)
        dhi, dlo = ind.donchian(b, int(p["donchian_n"]))
        # opening range of the current UTC day, known only after or_minutes
        day = b.open_time // DAY_MS
        mins = (b.open_time % DAY_MS) // MINUTE_MS + self.timeframe
        or_hi = np.full(n, np.nan)
        or_lo = np.full(n, np.nan)
        cur_d, h, l = None, -np.inf, np.inf
        for i in range(n):
            if day[i] != cur_d:
                cur_d, h, l = day[i], -np.inf, np.inf
            if mins[i] <= p["or_minutes"]:
                h, l = max(h, b.high[i]), min(l, b.low[i])
            elif np.isfinite(h):
                or_hi[i], or_lo[i] = h, l
        # breakout -> retest state machine (causal)
        vavg = ind.sma(b.volume, 20)
        sig = np.zeros(n)
        lvl = np.full(n, np.nan)
        state, level, since = 0, np.nan, 0
        a = self.f["atr"]
        for i in range(1, n):
            if np.isnan(dhi[i]) or np.isnan(a[i]) or np.isnan(vavg[i - 1]):
                continue
            up_lvl = np.nanmax([dhi[i], or_hi[i]]) if not np.isnan(or_hi[i]) else dhi[i]
            dn_lvl = np.nanmin([dlo[i], or_lo[i]]) if not np.isnan(or_lo[i]) else dlo[i]
            vol_ok = b.volume[i] > p["vol_mult"] * vavg[i - 1]
            if state == 0:
                if b.close[i] > up_lvl and vol_ok:
                    state, level, since = 1, up_lvl, 0
                elif b.close[i] < dn_lvl and vol_ok:
                    state, level, since = -1, dn_lvl, 0
                continue
            since += 1
            if since > p["retest_bars"]:
                state = 0
                continue
            tol = p["retest_atr"] * a[i]
            if state == 1:
                if b.close[i] < level - tol:
                    state = 0
                elif b.low[i] <= level + tol and b.close[i] > level:
                    sig[i], lvl[i], state = 1, level, 0
            elif state == -1:
                if b.close[i] > level + tol:
                    state = 0
                elif b.high[i] >= level - tol and b.close[i] < level:
                    sig[i], lvl[i], state = -1, level, 0
        self.f.update(sig=sig, lvl=lvl)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        s = f["sig"][j]
        if s == 0:
            return None
        a, level = f["atr"][j], f["lvl"][j]
        if s > 0 and self.trend15_at(j) >= 0:
            stop = min(level, b.low[j]) - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.6, f"breakout retest long @ {level:.4f}"
        if s < 0 and self.trend15_at(j) <= 0:
            stop = max(level, b.high[j]) + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, 0.6, f"breakdown retest short @ {level:.4f}"
        return None
