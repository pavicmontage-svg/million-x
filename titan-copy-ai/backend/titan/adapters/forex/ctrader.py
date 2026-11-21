"""cTrader Open API adapter (JSON over WebSocket, port 5036).

Verified against spotware/openapi-proto-messages (2026-11-21) and help.ctrader.com/open-api:
  hosts      wss://demo.ctraderapi.com:5036 | wss://live.ctraderapi.com:5036 (JSON)
  auth       ProtoOAApplicationAuthReq(2100) -> ProtoOAAccountAuthReq(2102) with an OAuth access token
             (scope "trading"), obtained via https://id.ctrader.com/my/settings/openapi/grantingaccess/
             and https://openapi.ctrader.com/apps/token (refresh with grant_type=refresh_token)
  orders     ProtoOANewOrderReq(2106) MARKET with relativeStopLoss/relativeTakeProfit
             (absolute SL/TP are not supported for MARKET orders), clientOrderId (max 50)
  amend      ProtoOAAmendPositionSLTPReq(2110)   close ProtoOAClosePositionReq(2111)
  state      ProtoOAReconcileReq(2124), ProtoOATraderReq(2121), ProtoOASymbolByIdReq(2116)
  prices     ProtoOASubscribeSpotsReq(2127) -> ProtoOASpotEvent(2131) (bid/ask in 1/100000)
  events     ProtoOAExecutionEvent(2126), ProtoOAOrderErrorEvent(2132), ProtoOAErrorRes(2142)
  units      volume in 0.01 of a unit (lots * lotSize); money fields scaled by 10^moneyDigits
  limits     50 req/s (non-historical) per connection; heartbeat every 10 s keeps the socket alive

A copy-trading strategy listing API does not exist in Open API: this adapter executes on, and
listens to, accounts the owner has authorised.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ...markets import forex as fx
from ...models import OrderStatus
from ..base import AccountSnapshot, AdapterError, AdapterUnavailable, ExecutionAdapter, OpenRequest, OrderResult, VenuePosition
from ..feeds import PushFeed

log = logging.getLogger("titan.ctrader")

HOSTS = {"demo": "wss://demo.ctraderapi.com:5036", "live": "wss://live.ctraderapi.com:5036"}
PT = dict(HEARTBEAT=51, ERROR_RES=50, APP_AUTH_REQ=2100, APP_AUTH_RES=2101, ACC_AUTH_REQ=2102, ACC_AUTH_RES=2103,
          NEW_ORDER_REQ=2106, AMEND_SLTP_REQ=2110, CLOSE_POSITION_REQ=2111, SYMBOLS_LIST_REQ=2114,
          SYMBOLS_LIST_RES=2115, SYMBOL_BY_ID_REQ=2116, SYMBOL_BY_ID_RES=2117, TRADER_REQ=2121, TRADER_RES=2122,
          RECONCILE_REQ=2124, RECONCILE_RES=2125, EXECUTION_EVENT=2126, SUBSCRIBE_SPOTS_REQ=2127,
          SUBSCRIBE_SPOTS_RES=2128, SPOT_EVENT=2131, ORDER_ERROR_EVENT=2132, OA_ERROR_RES=2142,
          TOKEN_INVALIDATED=2147, CLIENT_DISCONNECT=2148, ACCOUNTS_BY_TOKEN_REQ=2149, ACCOUNTS_BY_TOKEN_RES=2150,
          REFRESH_TOKEN_REQ=2173, REFRESH_TOKEN_RES=2174)
BUY, SELL = 1, 2
EXEC = {2: "ORDER_ACCEPTED", 3: "ORDER_FILLED", 4: "ORDER_REPLACED", 5: "ORDER_CANCELLED", 6: "ORDER_EXPIRED",
        7: "ORDER_REJECTED", 8: "ORDER_CANCEL_REJECTED", 11: "ORDER_PARTIAL_FILL"}
PRICE_SCALE = 100_000


@dataclass
class CTraderSymbol:
    symbol_id: int
    name: str
    digits: int
    pip_position: int
    lot_size: int          # cents of units per lot (e.g. 10_000_000 == 100,000 units)
    min_volume: int
    step_volume: int
    max_volume: int


class CTraderConnection:
    """Thread-hosted asyncio WebSocket connection with request/response correlation."""

    def __init__(self, env: str, client_id: str, client_secret: str, access_token: str, account_id: int,
                 on_event: Callable[[int, dict], None] | None = None):
        self.url = HOSTS[env]
        self.client_id, self.client_secret, self.token, self.account_id = client_id, client_secret, access_token, account_id
        self.on_event = on_event
        self.loop = asyncio.new_event_loop()
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.connected = threading.Event()
        self.stop = False
        self.last_error: str | None = None
        self.thread = threading.Thread(target=self._run, name="ctrader-ws", daemon=True)

    def start(self, timeout: float = 20) -> None:
        self.thread.start()
        if not self.connected.wait(timeout):
            raise AdapterUnavailable(f"cTrader connection failed: {self.last_error}")

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        import websockets

        backoff = 1
        while not self.stop:
            try:
                async with websockets.connect(self.url, ping_interval=None, max_size=2 ** 23) as ws:
                    self.ws = ws
                    reader = asyncio.create_task(self._reader())
                    await self._request(PT["APP_AUTH_REQ"], {"clientId": self.client_id, "clientSecret": self.client_secret},
                                        PT["APP_AUTH_RES"])
                    await self._request(PT["ACC_AUTH_REQ"], {"ctidTraderAccountId": self.account_id,
                                                             "accessToken": self.token}, PT["ACC_AUTH_RES"])
                    self.connected.set()
                    backoff = 1
                    while not reader.done():
                        await asyncio.sleep(10)
                        await ws.send(json.dumps({"payloadType": PT["HEARTBEAT"], "payload": {}}))
            except Exception as e:  # reconnect with backoff
                self.last_error = str(e)
                self.connected.clear()
                for f in self.pending.values():
                    if not f.done():
                        f.set_exception(AdapterUnavailable(f"cTrader disconnected: {e}"))
                self.pending.clear()
                if self.on_event:
                    self.on_event(-1, {"disconnected": str(e)})
                await asyncio.sleep(min(backoff, 60))
                backoff *= 2

    async def _reader(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            pt, payload, mid = msg.get("payloadType"), msg.get("payload", {}), msg.get("clientMsgId")
            if pt == PT["HEARTBEAT"]:
                continue
            if mid and mid in self.pending and not self.pending[mid].done():
                if pt in (PT["OA_ERROR_RES"], PT["ERROR_RES"]):
                    self.pending[mid].set_exception(AdapterError(f"cTrader {payload.get('errorCode')}: "
                                                                 f"{payload.get('description')}"))
                else:
                    self.pending[mid].set_result((pt, payload))
            if self.on_event and pt in (PT["EXECUTION_EVENT"], PT["SPOT_EVENT"], PT["ORDER_ERROR_EVENT"],
                                        PT["TOKEN_INVALIDATED"], PT["CLIENT_DISCONNECT"]):
                try:
                    self.on_event(pt, payload)
                except Exception:
                    log.exception("cTrader event handler failed")

    async def _request(self, pt: int, payload: dict, expect: int | None = None, timeout: float = 15):
        mid = f"t{next(self.ids)}"
        fut = self.loop.create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({"clientMsgId": mid, "payloadType": pt, "payload": payload}))
        try:
            rpt, rp = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(mid, None)
        if expect and rpt != expect and rpt != PT["EXECUTION_EVENT"]:
            raise AdapterError(f"unexpected cTrader response {rpt} for {pt}")
        return rpt, rp

    def call(self, pt: int, payload: dict, expect: int | None = None, timeout: float = 15):
        if not self.connected.is_set():
            raise AdapterUnavailable("cTrader not connected")
        payload.setdefault("ctidTraderAccountId", self.account_id)
        fut = asyncio.run_coroutine_threadsafe(self._request(pt, payload, expect, timeout), self.loop)
        try:
            return fut.result(timeout + 2)
        except TimeoutError as e:
            raise AdapterUnavailable("cTrader request timed out (outcome unknown)") from e


class CTraderAdapter(ExecutionAdapter):
    market = "forex"
    venue = "ctrader"

    def __init__(self, conn: CTraderConnection, env: str, account_currency: str = "EUR"):
        self.conn, self.mode = conn, env
        self.account_currency = account_currency
        self.symbols: dict[str, CTraderSymbol] = {}
        self.by_id: dict[int, CTraderSymbol] = {}
        self.feed = PushFeed(max_age_s=15)
        self.fills: dict[str, dict] = {}
        self.listeners: list[Callable[[dict], None]] = []
        prev = conn.on_event
        conn.on_event = lambda pt, p: (self._on_event(pt, p), prev and prev(pt, p))
        self.money_digits = 2

    # ------------------------------------------------------------------ setup
    def connect(self, symbols: list[str]):
        self.conn.start()
        _, tr = self.conn.call(PT["TRADER_REQ"], {}, PT["TRADER_RES"])
        self.money_digits = tr["trader"].get("moneyDigits", 2)
        _, lst = self.conn.call(PT["SYMBOLS_LIST_REQ"], {}, PT["SYMBOLS_LIST_RES"])
        wanted = {fx.normalize_symbol(s) for s in symbols}
        ids = {}
        for ls in lst.get("symbol", []):
            name = ls.get("symbolName", "")
            try:
                canon = fx.normalize_symbol(name)
            except Exception:
                continue
            if canon in wanted and canon not in ids.values():
                ids[ls["symbolId"]] = canon
        if not ids:
            raise AdapterError("none of the requested symbols exist on this cTrader account")
        _, det = self.conn.call(PT["SYMBOL_BY_ID_REQ"], {"symbolId": list(ids)}, PT["SYMBOL_BY_ID_RES"])
        for s in det.get("symbol", []):
            cs = CTraderSymbol(s["symbolId"], ids[s["symbolId"]], s["digits"], s["pipPosition"], s.get("lotSize", 10_000_000),
                               s.get("minVolume", 100_000), s.get("stepVolume", 100_000), s.get("maxVolume", 10_000_000_000))
            self.symbols[cs.name] = cs
            self.by_id[cs.symbol_id] = cs
            # override default contract spec with the broker's own values
            sp = fx.INSTRUMENTS[cs.name]
            fx.INSTRUMENTS[cs.name] = sp.from_broker(contract_size=cs.lot_size / 100, min_volume=cs.min_volume / cs.lot_size,
                                                     volume_step=cs.step_volume / cs.lot_size,
                                                     max_volume=cs.max_volume / cs.lot_size)
        self.conn.call(PT["SUBSCRIBE_SPOTS_REQ"], {"symbolId": list(self.by_id)}, PT["SUBSCRIBE_SPOTS_RES"])

    def _on_event(self, pt: int, p: dict):
        if pt == PT["SPOT_EVENT"]:
            s = self.by_id.get(p.get("symbolId"))
            if s and p.get("bid") and p.get("ask"):
                self.feed.update(s.name, p["bid"] / PRICE_SCALE, p["ask"] / PRICE_SCALE)
            elif s and (p.get("bid") or p.get("ask")):
                try:
                    b, a = self.feed.quote(s.name)
                except Exception:
                    return
                self.feed.update(s.name, (p.get("bid") or b * PRICE_SCALE) / PRICE_SCALE,
                                 (p.get("ask") or a * PRICE_SCALE) / PRICE_SCALE)
        elif pt == PT["EXECUTION_EVENT"]:
            o = p.get("order") or {}
            cid = o.get("clientOrderId")
            if cid:
                self.fills[cid] = p
            for cb in self.listeners:
                cb(p)

    # ------------------------------------------------------------------ helpers
    def _vol(self, symbol: str, lots: float) -> int:
        s = self.symbols[fx.normalize_symbol(symbol)]
        v = int(round(lots * s.lot_size))
        v -= v % s.step_volume
        if v < s.min_volume:
            raise AdapterError(f"volume {lots} lots below broker minimum")
        return min(v, s.max_volume)

    def _lots(self, symbol_id: int, volume: int) -> float:
        return volume / self.by_id[symbol_id].lot_size

    def quote(self, symbol):
        return self.feed.quote(fx.normalize_symbol(symbol))

    def mids(self):
        return self.feed.mids()

    def _money(self, v) -> float:
        return float(v or 0) / 10 ** self.money_digits

    def _wait_fill(self, cid: str, timeout: float = 10) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            ev = self.fills.get(cid)
            if ev and EXEC.get(ev.get("executionType")) in ("ORDER_FILLED", "ORDER_PARTIAL_FILL", "ORDER_REJECTED",
                                                            "ORDER_CANCELLED", "ORDER_EXPIRED"):
                return ev
            time.sleep(0.05)
        return self.fills.get(cid)

    def _result(self, cid: str, ev: dict | None) -> OrderResult:
        if ev is None:
            return OrderResult(cid, OrderStatus.UNKNOWN, error="no execution event received")
        et = EXEC.get(ev.get("executionType"), "")
        o, pos, deal = ev.get("order") or {}, ev.get("position") or {}, ev.get("deal") or {}
        status = {"ORDER_FILLED": OrderStatus.FILLED, "ORDER_PARTIAL_FILL": OrderStatus.PARTIALLY_FILLED,
                  "ORDER_REJECTED": OrderStatus.REJECTED, "ORDER_CANCELLED": OrderStatus.CANCELED,
                  "ORDER_EXPIRED": OrderStatus.CANCELED, "ORDER_ACCEPTED": OrderStatus.ACKED}.get(et, OrderStatus.UNKNOWN)
        sym_id = (pos.get("tradeData") or {}).get("symbolId") or deal.get("symbolId")
        filled = self._lots(sym_id, deal.get("filledVolume") or o.get("executedVolume") or 0) if sym_id in self.by_id else 0
        fee = abs(self._money(deal.get("commission")))
        return OrderResult(cid, status, str(o.get("orderId")) if o.get("orderId") else None,
                           str(pos.get("positionId")) if pos.get("positionId") else None, filled,
                           deal.get("executionPrice") or o.get("executionPrice"), fee, self.account_currency,
                           error=ev.get("errorCode") or deal.get("rejectReason"), raw=ev)

    # ------------------------------------------------------------------ trading
    def account(self) -> AccountSnapshot:
        _, tr = self.conn.call(PT["TRADER_REQ"], {}, PT["TRADER_RES"])
        bal = self._money(tr["trader"]["balance"])
        pos = self.positions()
        upnl = sum(p.unrealized for p in pos)
        return AccountSnapshot(self.account_currency, bal, bal + upnl, 0.0, None, pos, raw={"trader": tr["trader"]})

    def positions(self) -> list[VenuePosition]:
        _, rec = self.conn.call(PT["RECONCILE_REQ"], {}, PT["RECONCILE_RES"])
        out = []
        mids = self.mids()
        for p in rec.get("position", []):
            td = p["tradeData"]
            s = self.by_id.get(td["symbolId"])
            if not s:
                continue
            side = "buy" if td["tradeSide"] == BUY else "sell"
            lots = self._lots(td["symbolId"], td["volume"])
            upnl = 0.0
            try:
                bid, ask = self.quote(s.name)
                upnl = fx.pnl(fx.spec(s.name), side, lots, p.get("price", 0), bid if side == "buy" else ask,
                              self.account_currency, mids)
            except Exception:
                pass
            out.append(VenuePosition(str(p["positionId"]), s.name, side, lots, p.get("price", 0), p.get("stopLoss"),
                                     p.get("takeProfit"), upnl, client_id=td.get("label")))
        return out

    def open_position(self, req: OpenRequest) -> OrderResult:
        sym = fx.normalize_symbol(req.symbol)
        s = self.symbols.get(sym)
        if not s:
            raise AdapterError(f"{sym} not available on this cTrader account")
        bid, ask = self.quote(sym)
        ref_px = ask if req.side == "buy" else bid
        payload = {"symbolId": s.symbol_id, "orderType": 1, "tradeSide": BUY if req.side == "buy" else SELL,
                   "volume": self._vol(sym, req.quantity), "clientOrderId": req.client_id[:50],
                   "label": req.client_id[:100],
                   "relativeStopLoss": int(round(abs(ref_px - req.sl) * PRICE_SCALE))}
        if req.tp:
            payload["relativeTakeProfit"] = int(round(abs(req.tp - ref_px) * PRICE_SCALE))
        self.conn.call(PT["NEW_ORDER_REQ"], payload)
        res = self._result(req.client_id, self._wait_fill(req.client_id))
        if res.ok and res.position_ref:
            # verify the stop-loss exists on the venue; otherwise close immediately
            vp = next((p for p in self.positions() if p.ref == res.position_ref), None)
            if vp is not None and not vp.sl:
                self.close_position(vp, vp.quantity, f"{req.client_id}-x")
                res.status, res.error = OrderStatus.REJECTED, "stop-loss missing after fill; position closed"
            elif vp is not None and abs(vp.sl - req.sl) > 0:
                # relative SL is anchored to the fill price: re-anchor to the copied absolute level
                try:
                    self.modify_protection(vp, req.sl, req.tp, f"{req.client_id}-a")
                except Exception as e:
                    log.warning("re-anchoring SL failed (relative SL kept): %s", e)
        return res

    def modify_protection(self, pos, sl, tp, client_id):
        payload = {"positionId": int(pos.ref)}
        if sl is not None:
            payload["stopLoss"] = sl
        elif pos.sl:
            payload["stopLoss"] = pos.sl
        if tp is not None:
            payload["takeProfit"] = tp
        elif pos.tp:
            payload["takeProfit"] = pos.tp
        self.conn.call(PT["AMEND_SLTP_REQ"], payload)
        return OrderResult(client_id, OrderStatus.ACKED, position_ref=pos.ref)

    def close_position(self, pos, quantity, client_id):
        vol = self._vol(pos.symbol, min(quantity, pos.quantity))
        _, ev = self.conn.call(PT["CLOSE_POSITION_REQ"], {"positionId": int(pos.ref), "volume": vol})
        res = self._result(client_id, ev if isinstance(ev, dict) and ev.get("executionType") else None)
        if res.status == OrderStatus.UNKNOWN:
            # the fill event may arrive separately; reconcile from positions
            time.sleep(0.5)
            still = next((p for p in self.positions() if p.ref == pos.ref), None)
            closed = pos.quantity - (still.quantity if still else 0)
            if closed > 0:
                bid, ask = self.quote(pos.symbol)
                res = OrderResult(client_id, OrderStatus.FILLED, position_ref=pos.ref, filled_qty=closed,
                                  avg_price=bid if pos.side == "buy" else ask, raw={"reconciled": True})
        return res

    def get_order(self, client_id, symbol):
        ev = self.fills.get(client_id[:50])
        if ev:
            return self._result(client_id, ev)
        for p in self.positions():
            if p.client_id == client_id[:100]:
                return OrderResult(client_id, OrderStatus.FILLED, position_ref=p.ref, filled_qty=p.quantity,
                                   avg_price=p.entry_price, raw={"reconciled_from_position": True})
        return None

    def size(self, symbol, side, entry, sl, risk_amount):
        sp = fx.spec(symbol)
        mids = self.mids()
        r = fx.size_by_risk(sp, entry, sl, risk_amount, self.account_currency, mids)
        if r.reason:
            return 0, 0, 0, 0, {}, r.reason
        notional = fx.convert(sp.contract_size * r.lots * entry, sp.quote, self.account_currency, mids)
        margin = fx.margin_required(sp, r.lots, entry, self.account_currency, mids)
        return r.lots, r.risk_amount, notional, margin, fx.currency_legs(symbol, side, r.risk_amount), None
