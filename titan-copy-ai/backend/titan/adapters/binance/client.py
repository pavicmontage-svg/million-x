"""Signed Binance REST client for Spot, USDⓈ-M futures and COIN-M futures.

Base URLs (developers.binance.com, verified 2026-11-21):
  Spot demo     https://demo-api.binance.com        (REST /api/v3/...)
  Spot live     https://api.binance.com
  USDⓈ-M demo   https://demo-fapi.binance.com       (REST /fapi/...)
  USDⓈ-M live   https://fapi.binance.com
  COIN-M demo   https://demo-dapi.binance.com       (REST /dapi/...)
  COIN-M live   https://dapi.binance.com
  WS market streams: demo-stream.binance.com (spot), demo-fstream.binance.com (USDⓈ-M),
                     demo-dstream.binance.com (COIN-M); live stream/fstream/dstream.binance.com

Authentication: X-MBX-APIKEY header + HMAC-SHA256 `signature` over the query string
(Ed25519 keys are supported by Binance too; HMAC is used here). `timestamp` is corrected by the
measured server-time offset; `recvWindow` = 5000 ms.

Safety:
* Order POSTs are never blindly retried. On timeout/5xx the order is reported UNKNOWN and the
  caller reconciles it by client order id.
* Request weight is tracked from X-MBX-USED-WEIGHT-1M; the client pauses before the limit and
  stops on HTTP 418 (IP ban).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from urllib.parse import urlencode

import httpx

from ..base import AdapterError, AdapterUnavailable

log = logging.getLogger("titan.binance")

HOSTS = {
    ("spot", "demo"): "https://demo-api.binance.com",
    ("spot", "live"): "https://api.binance.com",
    ("usdm", "demo"): "https://demo-fapi.binance.com",
    ("usdm", "live"): "https://fapi.binance.com",
    ("coinm", "demo"): "https://demo-dapi.binance.com",
    ("coinm", "live"): "https://dapi.binance.com",
}
WS_HOSTS = {
    ("spot", "demo"): "wss://demo-stream.binance.com/ws",
    ("spot", "live"): "wss://stream.binance.com:9443/ws",
    ("usdm", "demo"): "wss://demo-fstream.binance.com/ws",
    ("usdm", "live"): "wss://fstream.binance.com/ws",
    ("coinm", "demo"): "wss://demo-dstream.binance.com/ws",
    ("coinm", "live"): "wss://dstream.binance.com/ws",
}
PREFIX = {"spot": "/api/v3", "usdm": "/fapi", "coinm": "/dapi"}
WEIGHT_LIMIT = {"spot": 6000, "usdm": 2400, "coinm": 2400}


class BinanceAPIError(AdapterError):
    def __init__(self, status: int, code: int | None, msg: str):
        super().__init__(f"Binance {status} {code}: {msg}")
        self.status, self.code, self.msg = status, code, msg


class BinanceClient:
    def __init__(self, product: str, env: str, api_key: str | None = None, api_secret: str | None = None,
                 base_url: str | None = None, http: httpx.Client | None = None, timeout: float = 10.0):
        if product not in PREFIX:
            raise ValueError(f"unknown product {product}")
        if env not in ("demo", "live"):
            raise ValueError("env must be demo or live")
        self.product, self.env = product, env
        self.base = (base_url or HOSTS[(product, env)]).rstrip("/")
        self.key, self.secret = api_key, api_secret
        self.http = http or httpx.Client(timeout=timeout)
        self.offset_ms = 0
        self.used_weight = 0
        self.banned_until = 0.0
        self._lock = threading.Lock()

    # ----------------------------------------------------------------------------- core
    def _path(self, p: str) -> str:
        if p.startswith("/"):
            return p
        pre = PREFIX[self.product]
        return f"{pre}/{p}" if self.product == "spot" else f"{pre}/{p}"

    def sync_time(self) -> int:
        path = {"spot": "/api/v3/time", "usdm": "/fapi/v1/time", "coinm": "/dapi/v1/time"}[self.product]
        t0 = time.time() * 1000
        st = self.request("GET", path)["serverTime"]
        t1 = time.time() * 1000
        self.offset_ms = int(st - (t0 + t1) / 2)
        return self.offset_ms

    def _sign(self, params: dict) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = int(time.time() * 1000) + self.offset_ms
        params.setdefault("recvWindow", 5000)
        qs = urlencode(params)
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return f"{qs}&signature={sig}"

    def request(self, method: str, path: str, params: dict | None = None, signed: bool = False,
                api_key_only: bool = False, retry_safe: bool | None = None):
        if time.time() < self.banned_until:
            raise AdapterUnavailable(f"Binance IP ban/backoff active for {self.banned_until - time.time():.0f}s")
        self._throttle()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed or api_key_only:
            if not self.key or (signed and not self.secret):
                raise AdapterError("Binance API credentials are not configured")
            headers["X-MBX-APIKEY"] = self.key
        url = self.base + path
        qs = self._sign(params) if signed else urlencode(params)
        # GET/DELETE are idempotent -> safe to retry on transport errors; POST/PUT orders are not.
        retry_safe = method in ("GET",) if retry_safe is None else retry_safe
        attempts = 3 if retry_safe else 1
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                if method in ("GET", "DELETE"):
                    r = self.http.request(method, f"{url}?{qs}" if qs else url, headers=headers)
                else:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    r = self.http.request(method, url, content=qs, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if signed and retry_safe:
                    qs = self._sign(params)  # fresh timestamp
                time.sleep(0.3 * 2 ** i)
                continue
            self._track(r)
            if r.status_code == 418:
                self.banned_until = time.time() + int(r.headers.get("Retry-After", "120"))
                raise AdapterUnavailable("Binance returned 418 (IP banned) - backing off")
            if r.status_code == 429:
                self.banned_until = time.time() + int(r.headers.get("Retry-After", "30"))
                raise AdapterUnavailable("Binance rate limit (429) - backing off")
            if r.status_code >= 500:
                last_exc = AdapterUnavailable(f"Binance {r.status_code}: {r.text[:200]}")
                if retry_safe:
                    time.sleep(0.3 * 2 ** i)
                    continue
                raise last_exc
            if r.status_code == 451:
                raise AdapterUnavailable("Binance 451: service unavailable from this location (geo-restricted)")
            data = r.json() if r.content else {}
            if r.status_code >= 400:
                code = data.get("code") if isinstance(data, dict) else None
                msg = data.get("msg") if isinstance(data, dict) else r.text
                if code == -1021 and retry_safe:  # timestamp outside recvWindow -> resync once
                    self.sync_time()
                    qs = self._sign(params) if signed else qs
                    continue
                raise BinanceAPIError(r.status_code, code, msg)
            return data
        raise AdapterUnavailable(f"Binance request failed: {last_exc}")

    def _track(self, r: httpx.Response):
        for h in ("X-MBX-USED-WEIGHT-1M", "x-mbx-used-weight-1m", "X-MBX-USED-WEIGHT-1m"):
            if h in r.headers:
                try:
                    self.used_weight = int(r.headers[h])
                except ValueError:
                    pass
                break

    def _throttle(self):
        limit = WEIGHT_LIMIT[self.product]
        if self.used_weight > 0.85 * limit:
            wait = 60 - (time.time() % 60) + 0.5
            log.warning("Binance weight %s/%s: pausing %.1fs", self.used_weight, limit, wait)
            time.sleep(wait)
            self.used_weight = 0

    # ----------------------------------------------------------------------------- endpoints
    def ping(self):
        return self.request("GET", {"spot": "/api/v3/ping", "usdm": "/fapi/v1/ping", "coinm": "/dapi/v1/ping"}[self.product])

    def exchange_info(self):
        return self.request("GET", {"spot": "/api/v3/exchangeInfo", "usdm": "/fapi/v1/exchangeInfo",
                                    "coinm": "/dapi/v1/exchangeInfo"}[self.product])

    def book_ticker(self, symbol: str):
        return self.request("GET", {"spot": "/api/v3/ticker/bookTicker", "usdm": "/fapi/v1/ticker/bookTicker",
                                    "coinm": "/dapi/v1/ticker/bookTicker"}[self.product], {"symbol": symbol})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 300):
        return self.request("GET", {"spot": "/api/v3/klines", "usdm": "/fapi/v1/klines",
                                    "coinm": "/dapi/v1/klines"}[self.product],
                            {"symbol": symbol, "interval": interval, "limit": limit})

    def account(self):
        return self.request("GET", {"spot": "/api/v3/account", "usdm": "/fapi/v2/account",
                                    "coinm": "/dapi/v1/account"}[self.product],
                            {"omitZeroBalances": "true"} if self.product == "spot" else {}, signed=True)

    def position_risk(self, symbol: str | None = None):
        path = {"usdm": "/fapi/v2/positionRisk", "coinm": "/dapi/v1/positionRisk"}[self.product]
        return self.request("GET", path, {"symbol": symbol} if symbol and self.product == "usdm" else
                            ({"pair": symbol} if symbol else {}), signed=True)

    def position_mode_dual(self) -> bool:
        path = {"usdm": "/fapi/v1/positionSide/dual", "coinm": "/dapi/v1/positionSide/dual"}[self.product]
        return bool(self.request("GET", path, signed=True).get("dualSidePosition"))

    def set_leverage(self, symbol: str, leverage: int):
        path = {"usdm": "/fapi/v1/leverage", "coinm": "/dapi/v1/leverage"}[self.product]
        return self.request("POST", path, {"symbol": symbol, "leverage": leverage}, signed=True, retry_safe=True)

    def set_isolated(self, symbol: str):
        path = {"usdm": "/fapi/v1/marginType", "coinm": "/dapi/v1/marginType"}[self.product]
        try:
            return self.request("POST", path, {"symbol": symbol, "marginType": "ISOLATED"}, signed=True, retry_safe=True)
        except BinanceAPIError as e:
            if e.code == -4046:   # "No need to change margin type."
                return {"msg": "already isolated"}
            raise

    def new_order(self, **params):
        path = {"spot": "/api/v3/order", "usdm": "/fapi/v1/order", "coinm": "/dapi/v1/order"}[self.product]
        params.setdefault("newOrderRespType", "RESULT" if self.product != "spot" else "FULL")
        return self.request("POST", path, params, signed=True, retry_safe=False)

    def query_order(self, symbol: str, client_id: str):
        path = {"spot": "/api/v3/order", "usdm": "/fapi/v1/order", "coinm": "/dapi/v1/order"}[self.product]
        return self.request("GET", path, {"symbol": symbol, "origClientOrderId": client_id}, signed=True)

    def cancel_order(self, symbol: str, client_id: str):
        path = {"spot": "/api/v3/order", "usdm": "/fapi/v1/order", "coinm": "/dapi/v1/order"}[self.product]
        return self.request("DELETE", path, {"symbol": symbol, "origClientOrderId": client_id}, signed=True,
                            retry_safe=True)

    # USDⓈ-M conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET) go through the Algo service.
    def new_algo_order(self, **params):
        if self.product != "usdm":
            raise AdapterError("algo orders are implemented for USDⓈ-M only")
        params.setdefault("algoType", "CONDITIONAL")
        return self.request("POST", "/fapi/v1/algoOrder", params, signed=True, retry_safe=False)

    def query_algo_order(self, client_algo_id: str):
        return self.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id}, signed=True)

    def cancel_algo_order(self, client_algo_id: str):
        return self.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id}, signed=True,
                            retry_safe=True)

    # Spot protective OCO (take-profit LIMIT_MAKER above, STOP_LOSS below for a long position).
    def new_oco_sell(self, symbol: str, quantity: str, tp_price: str, sl_stop: str, list_client_id: str):
        return self.request("POST", "/api/v3/orderList/oco", {
            "symbol": symbol, "side": "SELL", "quantity": quantity,
            "aboveType": "LIMIT_MAKER", "abovePrice": tp_price, "aboveClientOrderId": f"{list_client_id}-tp",
            "belowType": "STOP_LOSS", "belowStopPrice": sl_stop, "belowClientOrderId": f"{list_client_id}-sl",
            "listClientOrderId": list_client_id}, signed=True, retry_safe=False)

    def cancel_order_list(self, symbol: str, list_client_id: str):
        return self.request("DELETE", "/api/v3/orderList", {"symbol": symbol, "listClientOrderId": list_client_id},
                            signed=True, retry_safe=True)

    def open_orders(self, symbol: str | None = None):
        path = {"spot": "/api/v3/openOrders", "usdm": "/fapi/v1/openOrders", "coinm": "/dapi/v1/openOrders"}[self.product]
        return self.request("GET", path, {"symbol": symbol} if symbol else {}, signed=True)

    # User data stream (listenKey) - futures; Spot uses the WebSocket API subscription instead.
    def start_user_stream(self) -> str:
        path = {"usdm": "/fapi/v1/listenKey", "coinm": "/dapi/v1/listenKey"}.get(self.product)
        if not path:
            raise AdapterError("spot user data is consumed via the WebSocket API (userDataStream.subscribe)")
        return self.request("POST", path, api_key_only=True, retry_safe=True)["listenKey"]

    def keepalive_user_stream(self):
        path = {"usdm": "/fapi/v1/listenKey", "coinm": "/dapi/v1/listenKey"}[self.product]
        return self.request("PUT", path, api_key_only=True, retry_safe=True)

    def api_restrictions(self):
        """Live keys only (SAPI is not part of Demo Trading): verifies withdrawals are disabled."""
        c = BinanceClient("spot", "live", self.key, self.secret, http=self.http)
        c.offset_ms = self.offset_ms
        return c.request("GET", "/sapi/v1/account/apiRestrictions", signed=True)
