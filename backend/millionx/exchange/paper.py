"""Paper broker: real-time public market data, simulated account and fills.

Implements the same Exchange interface as Binance so the trading engine runs
identical code in paper, demo and live mode. Market orders fill at the
current best bid/ask plus configured slippage; conditional orders trigger on
the 1m bar high/low (stop orders checked first, like the backtester).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import FeeModel
from ..models import Bars, OrderBookSnapshot, now_ms
from ..risk import SymbolRules
from .base import Exchange, ExchangeError, OrderResult


@dataclass
class _Pos:
    qty: float = 0.0          # signed
    entry: float = 0.0


@dataclass
class _Cond:
    client_id: str
    symbol: str
    side: str
    type: str
    trigger: float
    qty: Optional[float]
    close_position: bool


class PaperExchange(Exchange):
    name = "paper"
    is_paper = True

    def __init__(self, market: Exchange, start_equity: float, fees: FeeModel):
        self.m = market
        self.fees = fees
        self.wallet = start_equity
        self.pos: dict[str, _Pos] = {}
        self.orders: dict[str, OrderResult] = {}
        self.cond: dict[str, _Cond] = {}
        self.income: list[tuple[int, str, str, float]] = []   # ts, symbol, type, amount
        self.last_px: dict[str, float] = {}
        self.books: dict[str, OrderBookSnapshot] = {}
        self._lock = threading.RLock()

    # market data passthrough
    def symbols(self) -> dict[str, SymbolRules]:
        return self.m.symbols()

    def tickers_24h(self):
        return self.m.tickers_24h()

    def klines(self, symbol, limit=500, end_time=None) -> Bars:
        b = self.m.klines(symbol, limit, end_time)
        if len(b):
            self.last_px[symbol] = float(b.close[-1])
        return b

    def order_book(self, symbol, limit=20) -> OrderBookSnapshot:
        ob = self.m.order_book(symbol, limit)
        self.books[symbol] = ob
        self.last_px[symbol] = ob.mid
        return ob

    def premium_index(self, symbol):
        return self.m.premium_index(symbol)

    def open_interest(self, symbol):
        return self.m.open_interest(symbol)

    # account
    def balance(self) -> dict[str, float]:
        with self._lock:
            unreal = sum(p.qty * (self.last_px.get(s, p.entry) - p.entry) for s, p in self.pos.items())
            used = sum(abs(p.qty) * p.entry for p in self.pos.values())
            return {"equity": self.wallet + unreal, "wallet": self.wallet, "available": self.wallet - used / 10,
                    "unrealized": unreal}

    def position_amount(self, symbol) -> float:
        return self.pos.get(symbol, _Pos()).qty

    def commission(self, symbol):
        return self.fees.maker, self.fees.taker

    # fills
    def _fill(self, symbol: str, side: str, qty: float, price: float, fee_rate: float, ts: int) -> float:
        sgn = 1 if side == "BUY" else -1
        p = self.pos.setdefault(symbol, _Pos())
        fee = abs(qty) * price * fee_rate
        self.wallet -= fee
        self.income.append((ts, symbol, "COMMISSION", -fee))
        new = p.qty + sgn * qty
        if p.qty == 0 or (p.qty > 0) == (sgn > 0):
            p.entry = (p.entry * abs(p.qty) + price * qty) / (abs(p.qty) + qty) if (abs(p.qty) + qty) else 0
        else:
            closed = min(abs(p.qty), qty)
            pnl = closed * (price - p.entry) * (1 if p.qty > 0 else -1)
            self.wallet += pnl
            self.income.append((ts, symbol, "REALIZED_PNL", pnl))
            if abs(new) > 1e-12 and (new > 0) != (p.qty > 0):
                p.entry = price
        p.qty = 0.0 if abs(new) < 1e-12 else new
        return fee

    def _px(self, symbol: str, side: str) -> float:
        ob = self.books.get(symbol)
        ref = (ob.asks[0][0] if side == "BUY" else ob.bids[0][0]) if ob else self.last_px.get(symbol)
        if not ref:
            raise ExchangeError(f"no price for {symbol}")
        slip = self.fees.slippage_bps / 1e4
        return ref * (1 + slip) if side == "BUY" else ref * (1 - slip)

    def market_order(self, symbol, side, qty, client_id, reduce_only=False) -> OrderResult:
        with self._lock:
            if client_id in self.orders:
                return self.orders[client_id]  # idempotent
            cur = self.position_amount(symbol)
            if reduce_only:
                qty = min(qty, abs(cur))
                if qty <= 0 or (cur > 0) == (side == "BUY"):
                    r = OrderResult(client_id, None, symbol, side, "MARKET", "EXPIRED", qty)
                    self.orders[client_id] = r
                    return r
            px = self._px(symbol, side)
            self._fill(symbol, side, qty, px, self.fees.taker, now_ms())
            r = OrderResult(client_id, client_id, symbol, side, "MARKET", "FILLED", qty, qty, px)
            self.orders[client_id] = r
            return r

    def limit_order(self, symbol, side, qty, price, client_id, post_only=True) -> OrderResult:
        with self._lock:
            r = OrderResult(client_id, client_id, symbol, side, "LIMIT", "NEW", qty, 0.0, price,
                            raw={"post_only": post_only})
            self.orders[client_id] = r
            return r

    def query_order(self, symbol, client_id) -> OrderResult:
        if client_id not in self.orders:
            raise ExchangeError("order does not exist", code=-2013)
        return self.orders[client_id]

    def cancel_order(self, symbol, client_id) -> None:
        with self._lock:
            o = self.orders.get(client_id)
            if o and o.status == "NEW":
                o.status = "CANCELED"

    def conditional_order(self, symbol, side, type_, trigger, client_id, qty=None, close_position=False) -> OrderResult:
        with self._lock:
            self.cond[client_id] = _Cond(client_id, symbol, side, type_, trigger, qty, close_position)
            return OrderResult(client_id, client_id, symbol, side, type_, "NEW", qty or 0.0)

    def cancel_conditional(self, symbol, client_id) -> None:
        with self._lock:
            self.cond.pop(client_id, None)

    def cancel_all(self, symbol) -> None:
        with self._lock:
            for k in [k for k, c in self.cond.items() if c.symbol == symbol]:
                del self.cond[k]
            for o in self.orders.values():
                if o.symbol == symbol and o.status == "NEW":
                    o.status = "CANCELED"

    def realized_since(self, symbol, since_ms) -> dict[str, float]:
        out = {"realized": 0.0, "commission": 0.0, "funding": 0.0}
        for ts, s, t, v in self.income:
            if s != symbol or ts < since_ms:
                continue
            if t == "REALIZED_PNL":
                out["realized"] += v
            elif t == "COMMISSION":
                out["commission"] += -v
            elif t == "FUNDING_FEE":
                out["funding"] += -v
        return out

    # simulation hook: called by the engine on every closed 1m bar
    def on_bar(self, symbol: str, o: float, h: float, l: float, c: float, ts: int) -> list[str]:
        fired = []
        with self._lock:
            self.last_px[symbol] = c
            # limit orders (post-only): fill if price traded through
            for oid, od in self.orders.items():
                if od.symbol == symbol and od.status == "NEW" and od.type == "LIMIT":
                    if (od.side == "BUY" and l < od.avg_price) or (od.side == "SELL" and h > od.avg_price):
                        self._fill(symbol, od.side, od.qty, od.avg_price, self.fees.maker, ts)
                        od.status, od.filled_qty = "FILLED", od.qty
                        fired.append(oid)
            conds = [c_ for c_ in self.cond.values() if c_.symbol == symbol]
            conds.sort(key=lambda x: 0 if x.type.startswith("STOP") else 1)  # stops first
            for cd in conds:
                if cd.client_id not in self.cond:
                    continue
                pos = self.position_amount(symbol)
                if pos == 0:
                    self.cond.pop(cd.client_id, None)
                    continue
                hit = (l <= cd.trigger) if cd.side == "SELL" and cd.type.startswith("STOP") else \
                      (h >= cd.trigger) if cd.side == "BUY" and cd.type.startswith("STOP") else \
                      (h >= cd.trigger) if cd.side == "SELL" else (l <= cd.trigger)
                if not hit:
                    continue
                gap = (o < cd.trigger and cd.side == "SELL" and cd.type.startswith("STOP")) or \
                      (o > cd.trigger and cd.side == "BUY" and cd.type.startswith("STOP"))
                ref = o if gap else cd.trigger
                slip = self.fees.slippage_bps / 1e4
                px = ref * (1 - slip) if cd.side == "SELL" else ref * (1 + slip)
                q = abs(pos) if cd.close_position else min(cd.qty or 0, abs(pos))
                self._fill(symbol, cd.side, q, px, self.fees.taker, ts)
                self.cond.pop(cd.client_id, None)
                fired.append(cd.client_id)
        return fired
