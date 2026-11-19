"""BOT 6 — RSI DIVERGENCE: regular bullish/bearish divergence between
confirmed price pivots and RSI, with a market-structure confirmation (close
beyond the pivot bar's extreme). Pivots are only used once confirmed."""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class RSIDivergence(Strategy):
    bot_id = "bot6_rsidiv"
    name = "RSI Divergence"
    description = "Confirmed-pivot RSI divergence with structure-break confirmation."
    default_params = {"timeframe": 3, "rsi_n": 14, "left": 5, "right": 2, "min_gap": 5, "max_gap": 40,
                      "rsi_os": 40.0, "rsi_ob": 60.0, "confirm_bars": 4, "stop_atr": 0.2, "r1": 1.2, "r2": 2.0}
    param_grid = {"left": [3, 5], "rsi_os": [35.0, 40.0], "r2": [1.6, 2.0, 2.5]}

    def compute(self, b):
        p = self.params
        r = ind.rsi(b.close, int(p["rsi_n"]))
        L, R = int(p["left"]), int(p["right"])
        pl = ind.pivots_confirmed(b.low, L, R, "low")
        ph = ind.pivots_confirmed(b.high, L, R, "high")
        n = len(b)
        setup = np.zeros(n)        # +1 bullish / -1 bearish divergence confirmed at this bar
        piv_px = np.full(n, np.nan)
        trig = np.full(n, np.nan)  # structure level to break
        last_low = last_high = None
        for i in range(n):
            if pl[i]:
                c = i - R
                if last_low is not None:
                    pc = last_low
                    gap = c - pc
                    if p["min_gap"] <= gap <= p["max_gap"] and b.low[c] < b.low[pc] and r[c] > r[pc] and r[c] < p["rsi_os"]:
                        setup[i], piv_px[i], trig[i] = 1, b.low[c], b.high[c]
                last_low = c
            if ph[i]:
                c = i - R
                if last_high is not None:
                    pc = last_high
                    gap = c - pc
                    if p["min_gap"] <= gap <= p["max_gap"] and b.high[c] > b.high[pc] and r[c] < r[pc] and r[c] > p["rsi_ob"]:
                        setup[i], piv_px[i], trig[i] = -1, b.high[c], b.low[c]
                last_high = c
        # structure confirmation within confirm_bars after the setup
        sig = np.zeros(n)
        spx = np.full(n, np.nan)
        pend, px, tg, age = 0, np.nan, np.nan, 0
        for i in range(n):
            if setup[i] != 0:
                pend, px, tg, age = setup[i], piv_px[i], trig[i], 0
            if pend == 0:
                continue
            if pend > 0 and b.close[i] > tg and b.low[i] > px:
                sig[i], spx[i], pend = 1, px, 0
            elif pend < 0 and b.close[i] < tg and b.high[i] < px:
                sig[i], spx[i], pend = -1, px, 0
            else:
                age += 1
                if age > p["confirm_bars"]:
                    pend = 0
        self.f.update(rsi=r, sig=sig, spx=spx)

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        s = f["sig"][j]
        if s == 0:
            return None
        a = f["atr"][j]
        if s > 0:
            stop = f["spx"][j] - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, 0.5, "bullish RSI divergence + structure break"
        stop = f["spx"][j] + p["stop_atr"] * a
        tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
        return Side.SHORT, stop, tp1, tp2, 0.5, "bearish RSI divergence + structure break"
