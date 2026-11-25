"""Signed Binance USDT-M Futures REST client.

* HMAC-SHA256 signatures; the API secret never leaves this object and is never logged.
* Server-time offset sync and ``recvWindow`` to tolerate clock drift.
* Conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET) go to the Algo service
  (``/fapi/v1/algoOrder``, ``algoType=CONDITIONAL``, ``triggerPrice``) by default, which Binance
  requires since its December 2025 change; ``conditional_endpoint="legacy"`` uses ``/fapi/v1/order``.
* Every order carries a deterministic client id so a retried request can be looked up instead of
  duplicated. Timeouts and 5xx responses are reported as ``unknown_state`` — the caller must query
  the order before doing anything else (never assume a fill or a rejection).
* One-way position mode is required; ``check_account_setup`` refuses hedge mode.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

from ..core.logging import get_logger, register_secret
from .exchange import Balance, Exchange, ExchangeError, ExchangePosition, OrderInfo, OrderRequest

log = get_logger("exchange.binance")

BASE = {"TESTNET": "https://testnet.binancefuture.com", "LIVE": "https://fapi.binance.com"}
RETRIABLE_CODES = {-1001, -1003, -1007, -1008, -1021}  # disconnected, rate, timeout, overloaded, timestamp
ALGO_STATUS = {"NEW": "NEW", "TRIGGERING": "NEW", "TRIGGERED": "FILLED", "FINISHED": "FILLED", "CANCELED": "CANCELED",
               "CANCELLED": "CANCELED", "EXPIRED": "EXPIRED", "REJECTED": "REJECTED"}


def _fmt(x: float) -> str:
    s = f"{x:.12f}".rstrip("0").rstrip(".")
    return s or "0"


class BinanceFutures(Exchange):
    name = "binance_usdm"

    def __init__(self, api_key: str, api_secret: str, env: str = "TESTNET", *, timeout: float = 8.0,
                 conditional_endpoint: str = "algo", recv_window: int = 5000,
                 client: httpx.AsyncClient | None = None) -> None:
        if env not in BASE:
            raise ValueError("env must be TESTNET or LIVE")
        if not api_key or not api_secret:
            raise ValueError("API key and secret are required")
        register_secret(api_key)
        register_secret(api_secret)
        self.env = env
        self.base = BASE[env]
        self._key = api_key
        self._secret = api_secret.encode()
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self.conditional_endpoint = conditional_endpoint
        self.recv_window = recv_window
        self.time_offset_ms = 0
        self.last_ok_ms = 0
        self._algo_ids: dict[str, str] = {}

    def __repr__(self) -> str:  # never expose credentials
        return f"BinanceFutures(env={self.env})"

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ transport
    def _sign(self, params: dict) -> str:
        q = urlencode(params)
        sig = hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
        return f"{q}&signature={sig}"

    async def _request(self, method: str, path: str, params: dict | None = None, signed: bool = True):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"X-MBX-APIKEY": self._key}
        if signed:
            params["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
            params["recvWindow"] = self.recv_window
            query = self._sign(params)
        else:
            query = urlencode(params)
        url = f"{self.base}{path}?{query}" if query else f"{self.base}{path}"
        try:
            r = await self._client.request(method, url, headers=headers)
        except httpx.TimeoutException as e:
            raise ExchangeError(f"{method} {path} timed out", retriable=True, unknown_state=method != "GET") from e
        except httpx.TransportError as e:
            raise ExchangeError(f"{method} {path} transport error: {type(e).__name__}", retriable=True,
                                unknown_state=method != "GET") from e
        if r.status_code >= 500:
            raise ExchangeError(f"{method} {path} HTTP {r.status_code}", retriable=True, unknown_state=method != "GET")
        try:
            data = r.json()
        except ValueError:
            raise ExchangeError(f"{method} {path} non-JSON response HTTP {r.status_code}", retriable=r.status_code == 429)
        if r.status_code != 200 or (isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0):
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", "") if isinstance(data, dict) else str(data)[:200]
            if code == -1021:
                await self.sync_time()
            raise ExchangeError(f"{method} {path} HTTP {r.status_code} code {code}: {msg}", code=code,
                                retriable=code in RETRIABLE_CODES or r.status_code in (418, 429))
        self.last_ok_ms = int(time.time() * 1000)
        return data

    async def sync_time(self) -> int:
        d = await self._request("GET", "/fapi/v1/time", signed=False)
        self.time_offset_ms = int(d["serverTime"]) - int(time.time() * 1000)
        return self.time_offset_ms

    # ------------------------------------------------------------------ account
    async def server_time(self) -> int:
        d = await self._request("GET", "/fapi/v1/time", signed=False)
        return int(d["serverTime"])

    async def check_account_setup(self) -> dict:
        """Verify one-way mode and return a summary. Never requests or uses withdrawal permissions."""
        mode = await self._request("GET", "/fapi/v1/positionSide/dual")
        if mode.get("dualSidePosition"):
            raise ExchangeError("account is in hedge (dual-side) mode; QUANT TITAN requires one-way mode")
        bal = await self.balance()
        return {"one_way_mode": True, "wallet": bal.wallet, "available": bal.available}

    async def api_restrictions(self) -> dict | None:
        """Spot-API key restrictions (live only). Used to refuse keys that allow withdrawals."""
        if self.env != "LIVE":
            return None
        url_base = "https://api.binance.com"
        params = {"timestamp": int(time.time() * 1000) + self.time_offset_ms, "recvWindow": self.recv_window}
        r = await self._client.get(f"{url_base}/sapi/v1/account/apiRestrictions?{self._sign(params)}",
                                   headers={"X-MBX-APIKEY": self._key})
        if r.status_code != 200:
            raise ExchangeError(f"apiRestrictions HTTP {r.status_code}")
        return r.json()

    async def balance(self) -> Balance:
        d = await self._request("GET", "/fapi/v3/account")
        return Balance(float(d.get("totalWalletBalance", 0)), float(d.get("availableBalance", 0)),
                       float(d.get("totalUnrealizedProfit", 0)))

    async def positions(self) -> list[ExchangePosition]:
        rows = await self._request("GET", "/fapi/v3/positionRisk")
        out = []
        for p in rows:
            q = float(p.get("positionAmt", 0))
            if q == 0:
                continue
            out.append(ExchangePosition(p["symbol"], q, float(p.get("entryPrice", 0)), float(p.get("markPrice", 0)),
                                        float(p.get("leverage", 1) or 1), float(p.get("liquidationPrice", 0) or 0),
                                        float(p.get("unRealizedProfit", 0)), p.get("marginType", "isolated")))
        return out

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        try:
            await self._request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})
        except ExchangeError as e:
            if e.code != -4046:  # "No need to change margin type."
                raise

    # ------------------------------------------------------------------ orders
    def _parse(self, d: dict, conditional: bool = False) -> OrderInfo:
        if conditional or "algoId" in d:
            st = ALGO_STATUS.get(str(d.get("algoStatus", d.get("status", "NEW"))).upper(), "UNKNOWN")
            cid = d.get("clientAlgoId") or d.get("clientOrderId") or ""
            if d.get("algoId") is not None and cid:
                self._algo_ids[cid] = str(d["algoId"])
            return OrderInfo(cid, d.get("symbol", ""), d.get("side", ""), d.get("orderType", d.get("type", "")), st,
                             float(d.get("quantity", d.get("origQty", 0)) or 0), float(d.get("executedQty", 0) or 0),
                             float(d.get("avgPrice", 0) or 0), float(d.get("price", 0) or 0),
                             float(d.get("triggerPrice", d.get("stopPrice", 0)) or 0), str(d.get("reduceOnly", "")).lower() == "true",
                             str(d.get("closePosition", "")).lower() == "true", str(d.get("algoId", "")),
                             0.0, int(d.get("updateTime", d.get("createTime", 0)) or 0), d)
        return OrderInfo(d.get("clientOrderId", ""), d.get("symbol", ""), d.get("side", ""), d.get("type", ""),
                         d.get("status", "UNKNOWN"), float(d.get("origQty", 0) or 0), float(d.get("executedQty", 0) or 0),
                         float(d.get("avgPrice", 0) or 0), float(d.get("price", 0) or 0), float(d.get("stopPrice", 0) or 0),
                         bool(d.get("reduceOnly", False)), bool(d.get("closePosition", False)), str(d.get("orderId", "")),
                         0.0, int(d.get("updateTime", 0) or 0), d)

    async def place_order(self, req: OrderRequest) -> OrderInfo:
        if req.conditional and self.conditional_endpoint == "algo":
            p = {"algoType": "CONDITIONAL", "symbol": req.symbol, "side": req.side, "type": req.type,
                 "triggerPrice": _fmt(req.stop_price), "workingType": req.working_type, "clientAlgoId": req.client_id,
                 "priceProtect": "TRUE"}
            if req.close_position:
                p["closePosition"] = "true"
            else:
                p["quantity"] = _fmt(req.qty)
                p["reduceOnly"] = "true" if req.reduce_only else None
            d = await self._request("POST", "/fapi/v1/algoOrder", p)
            info = self._parse({**p, **d}, conditional=True)
            info.client_id = info.client_id or req.client_id
            return info
        p = {"symbol": req.symbol, "side": req.side, "type": req.type, "newClientOrderId": req.client_id,
             "newOrderRespType": "RESULT"}
        if req.type == "LIMIT":
            p.update(timeInForce=req.tif, price=_fmt(req.price))
        if req.conditional:
            p.update(stopPrice=_fmt(req.stop_price), workingType=req.working_type)
        if req.close_position:
            p["closePosition"] = "true"
        else:
            p["quantity"] = _fmt(req.qty)
            if req.reduce_only:
                p["reduceOnly"] = "true"
        return self._parse(await self._request("POST", "/fapi/v1/order", p))

    async def get_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo | None:
        try:
            if conditional and self.conditional_endpoint == "algo":
                algo_id = self._algo_ids.get(client_id)
                params = {"algoId": algo_id} if algo_id else {"clientAlgoId": client_id}
                return self._parse(await self._request("GET", "/fapi/v1/algoOrder", params), conditional=True)
            return self._parse(await self._request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}))
        except ExchangeError as e:
            if e.code in (-2013, -2011):  # order does not exist
                return None
            raise

    async def cancel_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo:
        if conditional and self.conditional_endpoint == "algo":
            algo_id = self._algo_ids.get(client_id)
            params = {"algoId": algo_id} if algo_id else {"clientAlgoId": client_id}
            d = await self._request("DELETE", "/fapi/v1/algoOrder", params)
            return self._parse({**d, "algoStatus": "CANCELED", "clientAlgoId": client_id, "symbol": symbol}, conditional=True)
        return self._parse(await self._request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}))

    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        rows = await self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol} if symbol else {})
        out = [self._parse(r) for r in rows]
        if self.conditional_endpoint == "algo":
            algo = await self._request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol} if symbol else {})
            if isinstance(algo, dict):
                algo = algo.get("orders", [])
            out += [self._parse(r, conditional=True) for r in algo]
        return out

    async def cancel_all(self, symbol: str) -> None:
        await self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        if self.conditional_endpoint == "algo":
            try:
                await self._request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})
            except ExchangeError as e:
                log.warning("cancel algo orders %s: %s", symbol, e)

    # ------------------------------------------------------------------ user data stream
    async def listen_key(self) -> str:
        return (await self._request("POST", "/fapi/v1/listenKey", signed=False))["listenKey"]

    async def keepalive_listen_key(self) -> None:
        await self._request("PUT", "/fapi/v1/listenKey", signed=False)
