"""MetaTrader 5 adapter: talks to the owner's self-hosted bridge (bridges/mt5_bridge.py).

The official MetaTrader5 Python package needs a running MT5 terminal, so it cannot run inside the
Linux backend container. The bridge exposes order_send / positions_get / symbol_info /
symbol_info_tick / history_deals_get over HMAC-signed HTTP.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from ...markets import forex as fx
from ...models import OrderStatus
from ..base import AccountSnapshot, AdapterError, AdapterUnavailable, ExecutionAdapter, OpenRequest, OrderResult, VenuePosition
from ..feeds import PushFeed


class MT5BridgeAdapter(ExecutionAdapter):
    market = "forex"
    venue = "mt5"

    def __init__(self, url: str, secret: str, env: str = "demo", symbol_map: dict[str, str] | None = None,
                 http: httpx.Client | None = None, alt_urls: list[str] | None = None):
        # several addresses of the same PC (Tailscale, home Wi-Fi): the first reachable one is used
        self.urls = [u.rstrip("/") for u in [url, *(alt_urls or [])] if u]
        self.url, self.secret, self.mode = self.urls[0], secret, env
        self.http = http or httpx.Client(timeout=15)
        self.symbol_map = symbol_map or {}          # canonical -> broker name (e.g. EURUSD -> EURUSD.r)
        self.rev = {v: k for k, v in self.symbol_map.items()}
        self.feed = PushFeed(max_age_s=10)
        self.account_currency = "EUR"

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else b""
        ts = str(int(time.time() * 1000))
        sig = hmac.new(self.secret.encode(), f"{ts}.{method}.{path}.".encode() + data, hashlib.sha256).hexdigest()
        headers = {"X-Titan-Ts": ts, "X-Titan-Sig": sig, "Content-Type": "application/json"}
        order = [self.url] + [u for u in self.urls if u != self.url]
        if method == "POST":
            order = order[:1]   # orders are never re-sent to a second address: the first may have executed it
        r, last = None, None
        for base in order:
            try:
                r = self.http.request(method, base + path, content=data or None, headers=headers)
                self.url = base   # remember the address that works
                break
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
        if r is None:
            if method == "POST" and len(self.urls) > 1:
                self.url = next(u for u in self.urls if u != order[0])   # next attempt uses the other address
            raise AdapterUnavailable(f"MT5 bridge unreachable at {', '.join(order)}: {last}")
        if r.status_code >= 500:
            raise AdapterUnavailable(f"MT5 bridge {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise AdapterError(f"MT5 bridge {r.status_code}: {r.text[:200]}")
        return r.json()

    def _b(self, sym: str) -> str:
        c = fx.normalize_symbol(sym)
        return self.symbol_map.get(c, c)

    def _c(self, broker_sym: str) -> str:
        return self.rev.get(broker_sym, fx.normalize_symbol(broker_sym))

    def sync_specs(self, symbols: list[str]):
        for s in symbols:
            d = self._req("GET", f"/symbol/{self._b(s)}")
            c = fx.normalize_symbol(s)
            fx.INSTRUMENTS[c] = fx.INSTRUMENTS[c].from_broker(contract_size=d["contract_size"], min_volume=d["volume_min"],
                                                              volume_step=d["volume_step"], max_volume=d["volume_max"])

    def account(self) -> AccountSnapshot:
        a = self._req("GET", "/account")
        self.account_currency = a["currency"]
        return AccountSnapshot(a["currency"], a["balance"], a["equity"], a["margin"], a["free_margin"],
                               self.positions(), raw=a)

    def quote(self, symbol):
        c = fx.normalize_symbol(symbol)
        try:
            return self.feed.quote(c)
        except Exception:
            t = self._req("GET", f"/tick/{self._b(c)}")
            self.feed.update(c, t["bid"], t["ask"], t["time_msc"] / 1000)
            return t["bid"], t["ask"]

    def mids(self):
        for s in fx.INSTRUMENTS:
            try:
                self.quote(s)
            except Exception:
                pass
        return self.feed.mids()

    def positions(self):
        return [VenuePosition(str(p["ticket"]), self._c(p["symbol"]), p["side"], p["volume"], p["price_open"], p["sl"],
                              p["tp"], p["profit"], client_id=p.get("comment")) for p in self._req("GET", "/positions")]

    def open_position(self, req: OpenRequest) -> OrderResult:
        d = self._req("POST", "/open", {"symbol": self._b(req.symbol), "side": req.side, "volume": req.quantity,
                                        "sl": req.sl, "tp": req.tp, "client_id": req.client_id})
        if not d.get("done"):
            return OrderResult(req.client_id, OrderStatus.REJECTED, error=f"retcode {d.get('retcode')}: {d.get('comment')}",
                               raw=d)
        res = OrderResult(req.client_id, OrderStatus.FILLED, str(d.get("order")), None, d["volume"], d["price"], raw=d)
        pos = next((p for p in self.positions() if p.client_id == req.client_id[:31]), None)
        if pos:
            res.position_ref = pos.ref
            if not pos.sl:
                self.close_position(pos, pos.quantity, f"{req.client_id}-x")
                res.status, res.error = OrderStatus.REJECTED, "stop-loss missing after fill; position closed"
        return res

    def modify_protection(self, pos, sl, tp, client_id):
        d = self._req("POST", "/modify", {"ticket": pos.ref, "symbol": self._b(pos.symbol),
                                          "sl": sl if sl is not None else pos.sl, "tp": tp if tp is not None else pos.tp})
        return OrderResult(client_id, OrderStatus.ACKED if d.get("done") else OrderStatus.REJECTED, position_ref=pos.ref,
                           error=None if d.get("done") else d.get("comment"), raw=d)

    def close_position(self, pos, quantity, client_id):
        d = self._req("POST", "/close", {"ticket": pos.ref, "volume": min(quantity, pos.quantity), "client_id": client_id})
        return OrderResult(client_id, OrderStatus.FILLED if d.get("done") else OrderStatus.REJECTED, str(d.get("deal")),
                           pos.ref, d.get("volume", 0), d.get("price"), error=None if d.get("done") else d.get("comment"),
                           raw=d)

    def get_order(self, client_id, symbol):
        d = self._req("GET", f"/order/{client_id[:31]}")
        if not d.get("found"):
            return None
        return OrderResult(client_id, OrderStatus.FILLED, str(d["deal"]), str(d["position"]), d["volume"], d["price"],
                           abs(d.get("commission") or 0), raw=d)

    def size(self, symbol, side, entry, sl, risk_amount):
        sp = fx.spec(symbol)
        mids = self.mids()
        r = fx.size_by_risk(sp, entry, sl, risk_amount, self.account_currency, mids)
        if r.reason:
            return 0, 0, 0, 0, {}, r.reason
        notional = fx.convert(sp.contract_size * r.lots * entry, sp.quote, self.account_currency, mids)
        margin = fx.margin_required(sp, r.lots, entry, self.account_currency, mids)
        return r.lots, r.risk_amount, notional, margin, fx.currency_legs(symbol, side, r.risk_amount), None
