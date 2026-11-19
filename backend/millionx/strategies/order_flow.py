"""BOT 3 — ORDER FLOW INTELLIGENCE.

Historical data: aggressive buyer/seller imbalance from taker-buy volume
(Binance klines field), i.e. a bar-level cumulative volume delta.
Live/paper: additionally requires the current order-book imbalance (top 10
levels) to agree and the spread to be tight. Historical order books are not in
Binance's public kline archive, so the book filter is live-only; backtest
results for this bot therefore cover the flow part only (documented).
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..models import Side
from .base import Strategy


class OrderFlowIntelligence(Strategy):
    bot_id = "bot3_orderflow"
    name = "Order Flow Intelligence"
    description = "Taker-flow imbalance (CVD) with initiative break; live order-book imbalance filter."
    default_params = {"timeframe": 1, "flow_n": 5, "flow_thr": 0.22, "break_n": 15, "book_thr": 0.15,
                      "max_spread_bps": 2.5, "stop_atr": 1.2, "r1": 1.0, "r2": 1.8}
    param_grid = {"flow_thr": [0.15, 0.22, 0.3], "break_n": [10, 15, 25], "stop_atr": [1.0, 1.5]}

    def compute(self, b):
        n = int(self.params["flow_n"])
        delta = 2 * b.taker_buy_volume - b.volume
        dsum = ind.sma(delta, n) * n
        vsum = ind.sma(b.volume, n) * n
        with np.errstate(divide="ignore", invalid="ignore"):
            flow = np.where(vsum > 0, dsum / vsum, 0.0)
        hi, lo = ind.donchian(b, int(self.params["break_n"]))
        self.f.update(flow=flow, dhi=hi, dlo=lo)

    def _book_ok(self, side: Side) -> bool:
        book = self.md.book
        if book is None:
            return True  # historical mode: flow-only
        if book.spread_bps > self.params["max_spread_bps"]:
            return False
        imb = book.imbalance(10)
        return imb >= self.params["book_thr"] if side == Side.LONG else imb <= -self.params["book_thr"]

    def evaluate(self, j):
        p, f, b = self.params, self.f, self.b
        fl, a = f["flow"][j], f["atr"][j]
        if np.isnan(fl) or np.isnan(f["dhi"][j]):
            return None
        if fl >= p["flow_thr"] and b.close[j] > f["dhi"][j] and self._book_ok(Side.LONG):
            stop = b.close[j] - p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.LONG, b.close[j], stop, p["r1"], p["r2"])
            return Side.LONG, stop, tp1, tp2, min(1.0, 0.3 + fl), f"aggressive buyers flow={fl:.2f}"
        if fl <= -p["flow_thr"] and b.close[j] < f["dlo"][j] and self._book_ok(Side.SHORT):
            stop = b.close[j] + p["stop_atr"] * a
            tp1, tp2 = self.levels(Side.SHORT, b.close[j], stop, p["r1"], p["r2"])
            return Side.SHORT, stop, tp1, tp2, min(1.0, 0.3 - fl), f"aggressive sellers flow={fl:.2f}"
        return None
