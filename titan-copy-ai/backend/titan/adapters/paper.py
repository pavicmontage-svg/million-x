"""Internal paper broker - every result it produces is SIMULATED.

* Prices come from a real `PriceFeed` (Binance public market data for crypto; a broker demo feed
  or signal-supplied prices for forex). The simulator never invents prices.
* Models spread (forex quotes are widened if the feed only provides mids), commission,
  adverse slippage, partial fills and stop-loss/take-profit triggering on price updates.
* State is persisted to the `paper_accounts` table after every mutation and restored on start,
  so positions survive process restarts.
"""
from __future__ import annotations

import logging
import random
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..markets import crypto as cm
from ..markets import forex as fx
from ..models import OrderStatus, PaperAccount
from .base import AccountSnapshot, AdapterError, AdapterUnavailable, ExecutionAdapter, OpenRequest, OrderResult, VenuePosition
from .feeds import PriceFeed

log = logging.getLogger("titan.paper")


@dataclass
class SimParams:
    """Simulation assumptions (shown in the UI next to every SIMULATED number)."""
    forex_spread_pips: dict | None = None        # symbol -> typical spread in pips
    forex_commission_per_lot: float = 7.0        # USD round turn per standard lot (raw-spread account)
    crypto_taker_fee: float = 0.0005             # 0.05% futures taker; spot uses crypto_spot_fee
    crypto_spot_fee: float = 0.001
    slippage_bps: float = 1.0                    # adverse slippage applied to market fills
    partial_fill_prob: float = 0.0               # probability a market order fills only partially
    reject_prob: float = 0.0                     # probability of a venue rejection (failure simulation)
    disconnect_prob: float = 0.0                 # probability of a transport failure (UNKNOWN outcome)
    seed: int | None = None

    def spread_pips(self, symbol: str) -> float:
        d = self.forex_spread_pips or {"EURUSD": 0.2, "GBPUSD": 0.5, "USDJPY": 0.4, "AUDUSD": 0.4, "USDCAD": 0.6,
                                       "USDCHF": 0.6, "NZDUSD": 0.7, "EURGBP": 0.6, "EURJPY": 0.8, "GBPJPY": 1.2,
                                       "XAUUSD": 2.0}
        return d.get(symbol, 1.0)


class PaperBroker(ExecutionAdapter):
    is_simulated = True
    mode = "paper"

    def __init__(self, venue: str, market: str, account_currency: str, initial_balance: float,
                 feed: PriceFeed, params: SimParams | None = None, product: str = cm.USDM,
                 filters: dict[str, cm.SymbolFilters] | None = None, session_factory=None):
        self.venue, self.market, self.account_currency = venue, market, account_currency
        self.feed, self.params, self.product = feed, params or SimParams(), product
        self.filters = filters or {}
        self.rng = random.Random(self.params.seed)
        self.lock = threading.RLock()
        self.session_factory = session_factory
        self.state = {"balance": initial_balance, "positions": {}, "orders": {}, "realized": 0.0,
                      "fees": 0.0, "created": datetime.now(timezone.utc).isoformat()}
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self):
        if not self.session_factory:
            return
        with self.session_factory() as s:
            row = s.query(PaperAccount).filter_by(venue=self.venue).one_or_none()
            if row and row.state:
                self.state = row.state
            else:
                s.add(PaperAccount(venue=self.venue, state=self.state))

    def _save(self):
        if not self.session_factory:
            return
        with self.session_factory() as s:
            row = s.query(PaperAccount).filter_by(venue=self.venue).one_or_none()
            if row is None:
                s.add(PaperAccount(venue=self.venue, state=dict(self.state)))
            else:
                row.state = dict(self.state)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(row, "state")

    # ------------------------------------------------------------------ pricing
    def quote(self, symbol: str) -> tuple[float, float]:
        bid, ask = self.feed.quote(symbol)
        if self.market == "forex" and bid == ask:
            sp = fx.spec(symbol)
            half = self.params.spread_pips(symbol) * sp.pip_size / 2
            bid, ask = bid - half, ask + half
        return bid, ask

    def mids(self) -> dict[str, float]:
        return self.feed.mids()

    def _value_per_unit(self, symbol: str, qty: float) -> float:
        if self.market == "forex":
            return fx.value_per_price_unit(fx.spec(symbol), qty, self.account_currency, self.mids())
        f = self.filters.get(symbol)
        if self.product == cm.COINM and f:
            return 0.0  # inverse: handled in _pnl
        return qty

    def _pnl(self, p: dict, price: float, qty: float | None = None) -> float:
        qty = p["quantity"] if qty is None else qty
        if self.market == "forex":
            return fx.pnl(fx.spec(p["symbol"]), p["side"], qty, p["entry_price"], price, self.account_currency, self.mids())
        cs = float(self.filters[p["symbol"]].contract_size) if p["symbol"] in self.filters else 1.0
        return cm.pnl_usd(self.product, p["side"], qty, p["entry_price"], price, cs)

    def _fee(self, symbol: str, qty: float, price: float) -> float:
        if self.market == "forex":
            usd = self.params.forex_commission_per_lot / 2 * qty
            return fx.convert(usd, "USD", self.account_currency, self.mids())
        rate = self.params.crypto_spot_fee if self.product == cm.SPOT else self.params.crypto_taker_fee
        if self.product == cm.COINM:
            cs = float(self.filters[symbol].contract_size) if symbol in self.filters else 100.0
            return qty * cs * rate
        return qty * price * rate

    def _fill_price(self, symbol: str, side: str) -> float:
        bid, ask = self.quote(symbol)
        px = ask if side == "buy" else bid
        slip = px * self.params.slippage_bps / 10_000 * self.rng.random()
        return px + slip if side == "buy" else px - slip

    def _failures(self):
        if self.rng.random() < self.params.disconnect_prob:
            raise AdapterUnavailable("SIMULATED network failure: order outcome unknown")
        if self.rng.random() < self.params.reject_prob:
            raise AdapterError("SIMULATED venue rejection")

    # ------------------------------------------------------------------ trading
    def account(self) -> AccountSnapshot:
        with self.lock:
            pos = self.positions()
            upnl = sum(p.unrealized for p in pos)
            margin = sum(self._margin(p.symbol, p.quantity, p.entry_price) for p in pos)
            bal = self.state["balance"]
            return AccountSnapshot(self.account_currency, bal, bal + upnl, margin, bal + upnl - margin, pos,
                                   raw={"simulated": True})

    def _margin(self, symbol: str, qty: float, price: float) -> float:
        if self.market == "forex":
            return fx.margin_required(fx.spec(symbol), qty, price, self.account_currency, self.mids())
        if self.product == cm.SPOT:
            return qty * price
        if self.product == cm.COINM:
            cs = float(self.filters[symbol].contract_size) if symbol in self.filters else 100.0
            return qty * cs / 3.0
        return qty * price / 3.0   # paper futures run at max 3x leverage

    def positions(self) -> list[VenuePosition]:
        out = []
        for ref, p in self.state["positions"].items():
            try:
                bid, ask = self.quote(p["symbol"])
                mark = bid if p["side"] == "buy" else ask
                upnl = self._pnl(p, mark)
            except Exception:
                upnl = 0.0
            out.append(VenuePosition(ref, p["symbol"], p["side"], p["quantity"], p["entry_price"], p.get("sl"),
                                     p.get("tp"), upnl, client_id=p.get("client_id")))
        return out

    def open_position(self, req: OpenRequest) -> OrderResult:
        with self.lock:
            if req.client_id in self.state["orders"]:
                return _res(self.state["orders"][req.client_id])
            self._failures()
            if req.sl is None:
                raise AdapterError("stop-loss required")
            qty = req.quantity
            if self.params.partial_fill_prob and self.rng.random() < self.params.partial_fill_prob:
                step = 0.01 if self.market == "forex" else float(self.filters[req.symbol].step_size) \
                    if req.symbol in self.filters else 0.001
                qty = max(step, round(qty * self.rng.uniform(0.4, 0.9) / step) * step)
            price = self._fill_price(req.symbol, req.side)
            fee = self._fee(req.symbol, qty, price)
            ref = "SIM-" + uuid.uuid4().hex[:10]
            self.state["positions"][ref] = {"symbol": req.symbol, "side": req.side, "quantity": qty,
                                            "entry_price": price, "sl": req.sl, "tp": req.tp,
                                            "client_id": req.client_id,
                                            "opened": datetime.now(timezone.utc).isoformat()}
            self.state["balance"] -= fee
            self.state["fees"] += fee
            status = OrderStatus.PARTIALLY_FILLED if qty < req.quantity else OrderStatus.FILLED
            r = OrderResult(req.client_id, status, "SIMO-" + uuid.uuid4().hex[:8], ref, qty, price, fee,
                            self.account_currency, raw={"simulated": True})
            self.state["orders"][req.client_id] = _ser(r)
            self._save()
            return r

    def modify_protection(self, pos, sl, tp, client_id):
        with self.lock:
            p = self.state["positions"].get(pos.ref)
            if not p:
                raise AdapterError(f"position {pos.ref} not found")
            if sl is not None:
                p["sl"] = sl
            if tp is not None:
                p["tp"] = tp
            r = OrderResult(client_id, OrderStatus.ACKED, position_ref=pos.ref, raw={"simulated": True})
            self.state["orders"][client_id] = _ser(r)
            self._save()
            return r

    def close_position(self, pos, quantity, client_id, price: float | None = None, reason: str = "close"):
        with self.lock:
            if client_id in self.state["orders"]:
                return _res(self.state["orders"][client_id])
            p = self.state["positions"].get(pos.ref)
            if not p:
                raise AdapterError(f"position {pos.ref} not found")
            self._failures()
            qty = min(quantity, p["quantity"])
            close_side = "sell" if p["side"] == "buy" else "buy"
            px = price if price is not None else self._fill_price(p["symbol"], close_side)
            pnl = self._pnl(p, px, qty)
            fee = self._fee(p["symbol"], qty, px)
            self.state["balance"] += pnl - fee
            self.state["realized"] += pnl
            self.state["fees"] += fee
            p["quantity"] = round(p["quantity"] - qty, 10)
            if p["quantity"] <= 1e-12:
                del self.state["positions"][pos.ref]
            r = OrderResult(client_id, OrderStatus.FILLED, "SIMO-" + uuid.uuid4().hex[:8], pos.ref, qty, px, fee,
                            self.account_currency, raw={"simulated": True, "pnl": pnl, "reason": reason})
            self.state["orders"][client_id] = _ser(r)
            self._save()
            return r

    def get_order(self, client_id, symbol):
        o = self.state["orders"].get(client_id)
        return _res(o) if o else None

    def check_triggers(self) -> list[OrderResult]:
        """Apply stop-loss / take-profit on the latest prices (called by the engine loop)."""
        out = []
        with self.lock:
            for ref, p in list(self.state["positions"].items()):
                try:
                    bid, ask = self.quote(p["symbol"])
                except Exception:
                    continue
                mark = bid if p["side"] == "buy" else ask
                hit = None
                if p.get("sl") is not None and ((p["side"] == "buy" and mark <= p["sl"]) or
                                                (p["side"] == "sell" and mark >= p["sl"])):
                    hit = ("stop_loss", mark)   # stop fills at market (gaps fill worse than SL)
                elif p.get("tp") is not None and ((p["side"] == "buy" and mark >= p["tp"]) or
                                                  (p["side"] == "sell" and mark <= p["tp"])):
                    hit = ("take_profit", p["tp"])
                if hit:
                    vp = VenuePosition(ref, p["symbol"], p["side"], p["quantity"], p["entry_price"])
                    r = self.close_position(vp, p["quantity"], f"{hit[0][:2]}-{ref}", price=hit[1], reason=hit[0])
                    out.append(r)
        return out

    def size(self, symbol, side, entry, sl, risk_amount):
        if self.market == "forex":
            sp = fx.spec(symbol)
            mids = self.mids()
            r = fx.size_by_risk(sp, entry, sl, risk_amount, self.account_currency, mids)
            if r.reason:
                return 0, 0, 0, 0, {}, r.reason
            notional = fx.convert(sp.contract_size * r.lots * entry, sp.quote, self.account_currency, mids)
            margin = fx.margin_required(sp, r.lots, entry, self.account_currency, mids)
            return r.lots, r.risk_amount, notional, margin, fx.currency_legs(symbol, side, r.risk_amount), None
        f = self.filters.get(symbol)
        if f is None:
            return 0, 0, 0, 0, {}, f"no exchange filters loaded for {symbol}"
        r = cm.size_by_risk(f, side, entry, sl, risk_amount)
        if r.reason:
            return 0, 0, 0, 0, {}, r.reason
        q = float(r.quantity)
        return q, r.risk_usd, r.notional_usd, self._margin(symbol, q, entry), {}, None


def _ser(r: OrderResult) -> dict:
    d = asdict(r)
    d["status"] = r.status.value
    return d


def _res(d: dict) -> OrderResult:
    d = dict(d)
    d["status"] = OrderStatus(d["status"])
    return OrderResult(**d)
