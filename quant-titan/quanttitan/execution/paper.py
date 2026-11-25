"""Paper exchange: live market data, simulated execution.

Implements the same :class:`Exchange` interface as the Binance client, so the order manager,
position manager and reconciler run unchanged. Simulation (none of it is treated as a guaranteed
real fill):
  * market orders walk the latest order-book snapshot (or price ± half spread + modelled slippage
    when no book is available), plus a latency penalty;
  * LIMIT IOC orders fill only the visible quantity inside the limit (partial fills), rest expires;
  * resting LIMIT orders fill (maker fee) when a later price trades through the limit;
  * STOP_MARKET / TAKE_PROFIT_MARKET trigger on mark (or last) price and fill as market orders;
  * taker/maker fees, funding at funding timestamps, isolated-margin liquidation;
  * failure injection (timeouts, rejections, outages) for tests and drills.
"""
from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import CostConfig
from ..core.logging import get_logger
from ..core.types import ContractSpec, now_ms
from .exchange import Balance, Exchange, ExchangeError, ExchangePosition, OrderInfo, OrderRequest

log = get_logger("exchange.paper")


@dataclass
class _Pos:
    qty: float = 0.0  # signed
    entry: float = 0.0
    leverage: float = 1.0
    margin_type: str = "ISOLATED"


@dataclass
class Fill:
    client_id: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    realized: float
    ts: int
    reduce: bool
    order_type: str
    ref_price: float = 0.0


@dataclass
class _Market:
    last: float = 0.0
    mark: float = 0.0
    book: dict | None = None
    ts: int = 0
    funding_rate: float = 0.0
    next_funding_ms: int = 0
    atr: float = 0.0


class PaperExchange(Exchange):
    name = "paper"
    env = "PAPER"

    def __init__(self, starting_balance: float, costs: CostConfig, specs: dict[str, ContractSpec] | None = None,
                 *, simulate_latency: bool = False) -> None:
        self.wallet = float(starting_balance)
        self.costs = costs
        self.specs = specs or {}
        self.simulate_latency = simulate_latency
        self.pos: dict[str, _Pos] = defaultdict(_Pos)
        self.orders: dict[str, OrderInfo] = {}
        self.req: dict[str, OrderRequest] = {}
        self.mkt: dict[str, _Market] = defaultdict(_Market)
        self.fills: list[Fill] = []
        self.funding_paid: dict[str, float] = defaultdict(float)
        self._ids = itertools.count(1)
        self._fail_queue: list[str] = []
        self.outage = False

    # ------------------------------------------------------------------ test hooks
    def inject_failures(self, *modes: str) -> None:
        """Queue failures for the next calls: 'timeout_lost' (request lost), 'timeout_applied'
        (applied but response lost), 'reject', 'server_error'."""
        self._fail_queue.extend(modes)

    def _maybe_fail(self, applied_cb=None):
        if self.outage:
            raise ExchangeError("paper exchange outage (simulated)", retriable=True, unknown_state=True)
        if not self._fail_queue:
            return None
        mode = self._fail_queue.pop(0)
        if mode == "timeout_lost":
            raise ExchangeError("simulated timeout (request lost)", retriable=True, unknown_state=True)
        if mode == "timeout_applied":
            if applied_cb:
                applied_cb()
            raise ExchangeError("simulated timeout (request applied, response lost)", retriable=True, unknown_state=True)
        if mode == "reject":
            raise ExchangeError("simulated rejection", code=-2010)
        if mode == "server_error":
            raise ExchangeError("simulated HTTP 503", retriable=True, unknown_state=True)
        return None

    # ------------------------------------------------------------------ market data input
    def update_market(self, symbol: str, *, last: float, mark: float | None = None, book: dict | None = None,
                      ts: int | None = None, funding_rate: float | None = None, next_funding_ms: int | None = None,
                      atr: float | None = None, high: float | None = None, low: float | None = None) -> list[Fill]:
        """Feed a price update. ``high``/``low`` let bar-replay trigger stops touched inside the bar."""
        m = self.mkt[symbol]
        m.last, m.mark = last, mark or last
        m.ts = ts or now_ms()
        if book is not None:
            m.book = book
        if funding_rate is not None:
            m.funding_rate = funding_rate
        if atr is not None:
            m.atr = atr
        if next_funding_ms is not None:
            if m.next_funding_ms and m.ts >= m.next_funding_ms:
                self._apply_funding(symbol)
            m.next_funding_ms = next_funding_ms
        before = len(self.fills)
        self._check_liquidation(symbol)
        self._trigger(symbol, high if high is not None else m.mark, low if low is not None else m.mark)
        self._fill_resting_limits(symbol, high if high is not None else last, low if low is not None else last)
        return self.fills[before:]

    def _apply_funding(self, symbol: str) -> None:
        p, m = self.pos[symbol], self.mkt[symbol]
        if p.qty == 0:
            return
        pay = p.qty * m.mark * m.funding_rate  # longs pay positive funding
        self.wallet -= pay
        self.funding_paid[symbol] += pay

    def _check_liquidation(self, symbol: str) -> None:
        p, m = self.pos[symbol], self.mkt[symbol]
        if p.qty == 0 or p.margin_type != "ISOLATED":
            return
        mmr = self.specs.get(symbol, ContractSpec(symbol)).maint_margin_rate
        if p.qty > 0:
            liq = p.entry * (1 - 1 / p.leverage + mmr)
            hit = m.mark <= liq
        else:
            liq = p.entry * (1 + 1 / p.leverage - mmr)
            hit = m.mark >= liq
        if hit:
            log.warning("PAPER LIQUIDATION %s at %.6g", symbol, liq)
            self._execute(symbol, "SELL" if p.qty > 0 else "BUY", abs(p.qty), liq, taker=True, cid=f"liq{next(self._ids)}",
                          reduce=True, otype="LIQUIDATION")

    # ------------------------------------------------------------------ helpers
    def _spec(self, symbol: str) -> ContractSpec:
        return self.specs.get(symbol, ContractSpec(symbol))

    def _market_fill_price(self, symbol: str, side: str, qty: float, limit: float | None = None) -> tuple[float, float]:
        """Return (avg price, fillable qty)."""
        m = self.mkt[symbol]
        if m.last <= 0:
            raise ExchangeError(f"no market data for {symbol}")
        lat_bps = self.costs.base_slippage_bps * (self.costs.latency_ms / 1000) if self.simulate_latency else 0.0
        sgn = 1 if side == "BUY" else -1
        if m.book and m.book.get("bids") and m.book.get("asks"):
            levels = m.book["asks"] if side == "BUY" else m.book["bids"]
            left, cost, filled = qty, 0.0, 0.0
            for p, q in levels:
                p, q = float(p), float(q)
                if limit is not None and (p - limit) * sgn > 0:
                    break
                take = min(left, q)
                cost += take * p; filled += take; left -= take
                if left <= 1e-12:
                    break
            if left > 1e-12 and limit is None and levels:
                # beyond visible depth: remaining qty at the worst level plus impact (market orders fill fully)
                worst = float(levels[-1][0]) * (1 + sgn * self.costs.impact_coef * 1e-3)
                cost += left * worst; filled += left
            if filled <= 0:
                return 0.0, 0.0
            return cost / filled * (1 + sgn * lat_bps / 1e4), filled
        spread_bps = 1.0
        slip = self.costs.base_slippage_bps + spread_bps / 2 + lat_bps
        if m.atr and m.last:
            slip += self.costs.vol_slippage_coef * m.atr / m.last * 1e4
        px = m.last * (1 + sgn * slip / 1e4)
        if limit is not None and (px - limit) * sgn > 0:
            return 0.0, 0.0
        return px, qty

    def _execute(self, symbol: str, side: str, qty: float, price: float, *, taker: bool, cid: str, reduce: bool,
                 otype: str, ref: float = 0.0) -> Fill:
        p = self.pos[symbol]
        sgn = 1 if side == "BUY" else -1
        fee = qty * price * (self.costs.taker_fee if taker else self.costs.maker_fee)
        realized = 0.0
        new_qty = p.qty + sgn * qty
        if p.qty == 0 or (p.qty > 0) == (sgn > 0):
            p.entry = (abs(p.qty) * p.entry + qty * price) / (abs(p.qty) + qty) if (abs(p.qty) + qty) > 0 else price
        else:
            closed = min(qty, abs(p.qty))
            realized = (price - p.entry) * closed * (1 if p.qty > 0 else -1)
            if abs(new_qty) > 1e-12 and (new_qty > 0) != (p.qty > 0):
                p.entry = price  # flipped
        p.qty = 0.0 if abs(new_qty) < 1e-12 else new_qty
        if p.qty == 0:
            p.entry = 0.0
        self.wallet += realized - fee
        f = Fill(cid, symbol, side, qty, price, fee, realized, self.mkt[symbol].ts or now_ms(), reduce, otype, ref)
        self.fills.append(f)
        return f

    def _reduce_qty(self, symbol: str, side: str, qty: float) -> float:
        """Max qty a reduce-only order may execute (cannot increase or flip a position)."""
        p = self.pos[symbol]
        if p.qty == 0 or (p.qty > 0) == (side == "BUY"):
            return 0.0
        return min(qty, abs(p.qty))

    # ------------------------------------------------------------------ Exchange API
    async def server_time(self) -> int:
        self._maybe_fail()
        return now_ms()

    async def balance(self) -> Balance:
        self._maybe_fail()
        unreal = sum((self.mkt[s].mark - p.entry) * p.qty for s, p in self.pos.items() if p.qty)
        used = sum(abs(p.qty) * self.mkt[s].mark / max(p.leverage, 1) for s, p in self.pos.items() if p.qty)
        return Balance(self.wallet, self.wallet - used + min(unreal, 0.0), unreal)

    async def positions(self) -> list[ExchangePosition]:
        self._maybe_fail()
        out = []
        for s, p in self.pos.items():
            if p.qty:
                m = self.mkt[s]
                out.append(ExchangePosition(s, p.qty, p.entry, m.mark, p.leverage, 0.0, (m.mark - p.entry) * p.qty, p.margin_type))
        return out

    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        self._maybe_fail()
        return [o for o in self.orders.values() if not o.is_terminal and (symbol is None or o.symbol == symbol)]

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self._maybe_fail()
        self.pos[symbol].leverage = float(leverage)

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        self._maybe_fail()
        self.pos[symbol].margin_type = margin_type

    async def get_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo | None:
        self._maybe_fail()
        return self.orders.get(client_id)

    async def cancel_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo:
        self._maybe_fail()
        o = self.orders.get(client_id)
        if o is None:
            raise ExchangeError("Unknown order sent.", code=-2011)
        if not o.is_terminal:
            o.status = "CANCELED"
            o.update_ms = now_ms()
        return o

    async def place_order(self, req: OrderRequest) -> OrderInfo:
        if self.simulate_latency and self.costs.latency_ms:
            await asyncio.sleep(self.costs.latency_ms / 1000)
        applied: dict = {}
        self._maybe_fail(lambda: applied.setdefault("o", self._place(req)))
        if "o" in applied:
            return applied["o"]
        return self._place(req)

    def _place(self, req: OrderRequest) -> OrderInfo:
        if req.client_id in self.orders:
            raise ExchangeError("Duplicate client order id", code=-4116)
        spec = self._spec(req.symbol)
        if not req.close_position and req.qty < spec.min_qty - 1e-12:
            raise ExchangeError(f"qty {req.qty} below minimum {spec.min_qty}", code=-4003)
        o = OrderInfo(req.client_id, req.symbol, req.side, req.type, "NEW", req.qty, 0.0, 0.0, req.price, req.stop_price,
                      req.reduce_only, req.close_position, f"P{next(self._ids)}", 0.0, now_ms())
        self.orders[req.client_id] = o
        self.req[req.client_id] = req
        if req.type == "MARKET" or (req.type == "LIMIT" and req.tif == "IOC"):
            self._fill_now(o, req)
        elif req.type == "LIMIT":
            m = self.mkt[req.symbol]
            if m.last and ((req.side == "BUY" and m.last <= req.price) or (req.side == "SELL" and m.last >= req.price)):
                self._fill_now(o, req)  # marketable limit behaves as taker
        return o

    def _fill_now(self, o: OrderInfo, req: OrderRequest, trigger_ref: float = 0.0) -> None:
        qty = req.qty
        if req.close_position:
            qty = self._reduce_qty(req.symbol, req.side, abs(self.pos[req.symbol].qty))
        elif req.reduce_only:
            qty = self._reduce_qty(req.symbol, req.side, qty)
        if qty <= 0:
            o.status = "EXPIRED"
            return
        limit = req.price if req.type == "LIMIT" else None
        px, fillable = self._market_fill_price(req.symbol, req.side, qty, limit)
        if fillable <= 0:
            o.status = "EXPIRED" if req.tif == "IOC" or req.type != "LIMIT" else "NEW"
            return
        f = self._execute(req.symbol, req.side, fillable, px, taker=True, cid=o.client_id,
                          reduce=req.reduce_only or req.close_position, otype=req.type, ref=trigger_ref or self.mkt[req.symbol].last)
        o.filled_qty += fillable
        o.avg_price = px
        o.fee += f.fee
        o.status = "FILLED" if o.filled_qty >= qty - 1e-12 else ("EXPIRED" if req.tif == "IOC" or req.type == "MARKET" else "PARTIALLY_FILLED")
        o.update_ms = now_ms()

    def _trigger(self, symbol: str, hi: float, lo: float) -> None:
        for cid, o in list(self.orders.items()):
            if o.symbol != symbol or o.is_terminal or o.type not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
                continue
            req = self.req[cid]
            sp = req.stop_price
            if o.type == "STOP_MARKET":
                hit = (req.side == "SELL" and lo <= sp) or (req.side == "BUY" and hi >= sp)
            else:
                hit = (req.side == "SELL" and hi >= sp) or (req.side == "BUY" and lo <= sp)
            if not hit:
                continue
            m = self.mkt[symbol]
            saved = m.last
            # gap-through: if the market is already beyond the trigger, fill at the market, else at the trigger
            if o.type == "STOP_MARKET":
                m.last = min(sp, saved) if req.side == "SELL" else max(sp, saved)
            else:
                m.last = sp
            self._fill_now(o, req, trigger_ref=sp)
            m.last = saved

    def _fill_resting_limits(self, symbol: str, hi: float, lo: float) -> None:
        for cid, o in list(self.orders.items()):
            if o.symbol != symbol or o.is_terminal or o.type != "LIMIT":
                continue
            req = self.req[cid]
            through = (req.side == "BUY" and lo < req.price) or (req.side == "SELL" and hi > req.price)
            if not through:
                continue
            qty = req.qty - o.filled_qty
            if req.reduce_only:
                qty = self._reduce_qty(symbol, req.side, qty)
            if qty <= 0:
                o.status = "EXPIRED"
                continue
            f = self._execute(symbol, req.side, qty, req.price, taker=False, cid=cid, reduce=req.reduce_only, otype="LIMIT", ref=req.price)
            o.filled_qty += qty; o.avg_price = req.price; o.fee += f.fee; o.status = "FILLED"; o.update_ms = now_ms()
