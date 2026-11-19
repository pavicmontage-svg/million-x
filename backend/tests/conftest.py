import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from millionx.backtest.data import synthetic_bars  # noqa: E402
from millionx.exchange.base import Exchange  # noqa: E402
from millionx.models import Bars, OrderBookSnapshot, MINUTE_MS  # noqa: E402
from millionx.risk import SymbolRules  # noqa: E402


class Clock:
    def __init__(self, t0: int):
        self.t = t0

    def __call__(self) -> int:
        return self.t


class FakeMarket(Exchange):
    """Deterministic market used by engine tests (no network)."""
    name = "fake"

    def __init__(self, clock: Clock, symbols=("AAAUSDT", "BBBUSDT"), n=4000, seed=5):
        self.clock = clock
        self.data = {s: synthetic_bars(n, seed=seed + i, start_ms=clock.t // MINUTE_MS * MINUTE_MS - (n - 400) * MINUTE_MS,
                                       price=100.0 * (i + 1)) for i, s in enumerate(symbols)}
        self.consecutive_errors = 0
        self.fail = False

    def _avail(self, s):
        b = self.data[s]
        k = int((b.open_time + MINUTE_MS <= self.clock.t).sum())
        return b.slice(0, k)

    def symbols(self):
        out = {}
        for s in self.data:
            r = SymbolRules(s, tick_size=0.001, step_size=0.0001, min_qty=0.0001, min_notional=5.0)
            r.quote, r.base = "USDT", s[:-4]
            out[s] = r
        return out

    def tickers_24h(self):
        return [{"symbol": s, "quoteVolume": str(1e9 - i)} for i, s in enumerate(self.data)]

    def klines(self, symbol, limit=500, end_time=None):
        if self.fail:
            self.consecutive_errors += 1
            from millionx.exchange.base import ExchangeError
            raise ExchangeError("down", retriable=True)
        self.consecutive_errors = 0
        b = self._avail(symbol)
        # include the currently forming bar like Binance does
        full = self.data[symbol]
        k = len(b)
        return full.slice(max(0, k - limit + 1), min(len(full), k + 1))

    def order_book(self, symbol, limit=20):
        b = self._avail(symbol)
        p = float(b.close[-1])
        return OrderBookSnapshot(self.clock.t, [(p * (1 - 1e-5 * (i + 1)), 1e6 / p) for i in range(limit)],
                                 [(p * (1 + 1e-5 * (i + 1)), 1e6 / p) for i in range(limit)])

    def balance(self):
        return {"equity": 0, "wallet": 0, "available": 0, "unrealized": 0}

    def position_amount(self, symbol):
        return 0.0

    def market_order(self, *a, **k):
        raise NotImplementedError

    def limit_order(self, *a, **k):
        raise NotImplementedError

    def query_order(self, *a, **k):
        raise NotImplementedError

    def cancel_order(self, *a, **k):
        pass

    def conditional_order(self, *a, **k):
        raise NotImplementedError

    def cancel_conditional(self, *a, **k):
        pass

    def cancel_all(self, *a, **k):
        pass

    def realized_since(self, *a, **k):
        return {"realized": 0, "commission": 0, "funding": 0}


@pytest.fixture
def clock():
    # engine polls a few seconds after each bar closes, like in production
    return Clock(1_750_000_000_000 // MINUTE_MS * MINUTE_MS + 3_000)
