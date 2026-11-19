"""Binance USDⓈ-M Futures adapter (Demo Trading and live).

* Demo:  https://demo-fapi.binance.com (keys created in Binance Demo Trading)
* Live:  https://fapi.binance.com
* Conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET) use the Algo Order
  service, POST /fapi/v1/algoOrder, which replaced /fapi/v1/order for these
  types in December 2025.
* Every order carries a deterministic client id; on a timeout the order is
  looked up by that id before any retry (no duplicate orders).
* Withdrawal permission is checked on live keys and trading refuses to start
  if it is enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from ..models import Bars, OrderBookSnapshot
from ..risk import SymbolRules
from .base import Exchange, ExchangeError, OrderResult

log = logging.getLogger("millionx.binance")


class BinanceFutures(Exchange):
    name = "binance_futures"

    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", live: bool = False,
                 timeout: float = 10.0, recv_window: int = 5000):
        self.base = base_url.rstrip("/")
        self.key, self.secret = api_key, api_secret
        self.live = live
        self.timeout = timeout
        self.recv_window = recv_window
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "million-x/1.0"})
        if api_key:
            self.s.headers["X-MBX-APIKEY"] = api_key
        self._time_offset = 0
        self._lock = threading.Lock()
        self.used_weight = 0
        self.consecutive_errors = 0
        self.last_ok_ms = 0
        self.latency_ms: list[float] = []

    # ---- transport -------------------------------------------------------
    def _sync_time(self) -> None:
        r = self.s.get(self.base + "/fapi/v1/time", timeout=self.timeout)
        r.raise_for_status()
        self._time_offset = int(r.json()["serverTime"]) - int(time.time() * 1000)

    def _req(self, method: str, path: str, params: Optional[dict[str, Any]] = None, signed: bool = False,
             base: Optional[str] = None, _retry: bool = True) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if signed:
            if not self.key or not self.secret:
                raise ExchangeError("API key not configured")
            params["timestamp"] = int(time.time() * 1000) + self._time_offset
            params["recvWindow"] = self.recv_window
            q = urlencode(params)
            sig = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            q += "&signature=" + sig
        else:
            q = urlencode(params)
        url = (base or self.base) + path + ("?" + q if q else "")
        t0 = time.time()
        try:
            r = self.s.request(method, url, timeout=self.timeout)
        except requests.RequestException as e:
            self.consecutive_errors += 1
            raise ExchangeError(f"network error: {e}", retriable=True) from e
        self.latency_ms.append((time.time() - t0) * 1000)
        self.latency_ms = self.latency_ms[-200:]
        self.used_weight = int(r.headers.get("X-MBX-USED-WEIGHT-1M", self.used_weight) or 0)
        if r.status_code in (418, 429):
            self.consecutive_errors += 1
            raise ExchangeError(f"rate limited ({r.status_code})", code=r.status_code, retriable=True)
        if r.status_code in (451, 403):
            self.consecutive_errors += 1
            raise ExchangeError(f"access denied from this location/IP ({r.status_code}): {r.text[:200]}",
                                code=r.status_code)
        if r.status_code >= 500:
            self.consecutive_errors += 1
            raise ExchangeError(f"exchange error {r.status_code}, order status unknown", code=r.status_code,
                                retriable=True)
        try:
            data = r.json()
        except ValueError:
            raise ExchangeError(f"bad response {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            if code == -1021 and _retry:
                self._sync_time()
                return self._req(method, path, {k: v for k, v in params.items()
                                                if k not in ("timestamp", "recvWindow")}, signed, base, False)
            raise ExchangeError(f"{data.get('msg', data) if isinstance(data, dict) else data}", code=code)
        self.consecutive_errors = 0
        self.last_ok_ms = int(time.time() * 1000)
        return data

    # ---- market data -----------------------------------------------------
    def symbols(self) -> dict[str, SymbolRules]:
        info = self._req("GET", "/fapi/v1/exchangeInfo")
        out = {}
        for s in info["symbols"]:
            if s.get("status") != "TRADING" or s.get("contractType") != "PERPETUAL":
                continue
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = SymbolRules(
                symbol=s["symbol"],
                tick_size=float(f["PRICE_FILTER"]["tickSize"]),
                step_size=float(f.get("MARKET_LOT_SIZE", f["LOT_SIZE"])["stepSize"]),
                min_qty=float(f.get("MARKET_LOT_SIZE", f["LOT_SIZE"])["minQty"]),
                min_notional=float(f.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
                maint_margin_rate=float(s.get("maintMarginPercent", 0.4)) / 100,
            )
            out[s["symbol"]].quote = s.get("quoteAsset")  # type: ignore[attr-defined]
            out[s["symbol"]].base = s.get("baseAsset")    # type: ignore[attr-defined]
        return out

    def tickers_24h(self) -> list[dict[str, Any]]:
        return self._req("GET", "/fapi/v1/ticker/24hr")

    def klines(self, symbol: str, limit: int = 500, end_time: Optional[int] = None) -> Bars:
        rows = self._req("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": limit,
                                                    "endTime": end_time})
        return Bars.from_rows(rows)

    def order_book(self, symbol: str, limit: int = 20) -> OrderBookSnapshot:
        d = self._req("GET", "/fapi/v1/depth", {"symbol": symbol, "limit": limit})
        return OrderBookSnapshot(ts=int(d.get("T") or d.get("E") or time.time() * 1000),
                                 bids=[(float(p), float(q)) for p, q in d["bids"]],
                                 asks=[(float(p), float(q)) for p, q in d["asks"]])

    def premium_index(self, symbol: str) -> dict[str, Any]:
        return self._req("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})

    def open_interest(self, symbol: str) -> dict[str, Any]:
        return self._req("GET", "/fapi/v1/openInterest", {"symbol": symbol})

    def server_time(self) -> int:
        return int(self._req("GET", "/fapi/v1/time")["serverTime"])

    # ---- account ---------------------------------------------------------
    def balance(self) -> dict[str, float]:
        a = self._req("GET", "/fapi/v2/account", signed=True)
        return {"equity": float(a["totalMarginBalance"]), "wallet": float(a["totalWalletBalance"]),
                "available": float(a["availableBalance"]), "unrealized": float(a["totalUnrealizedProfit"])}

    def position_amount(self, symbol: str) -> float:
        rows = self._req("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        return sum(float(r["positionAmt"]) for r in rows)

    def commission(self, symbol: str) -> Optional[tuple[float, float]]:
        d = self._req("GET", "/fapi/v1/commissionRate", {"symbol": symbol}, signed=True)
        return float(d["makerCommissionRate"]), float(d["takerCommissionRate"])

    def api_permissions(self) -> dict[str, Any]:
        if not self.live:
            return {"checked": False, "note": "demo keys: permission endpoint not available"}
        d = self._req("GET", "/sapi/v1/account/apiRestrictions", signed=True, base="https://api.binance.com")
        return {"checked": True, "withdrawals": bool(d.get("enableWithdrawals")),
                "futures": bool(d.get("enableFutures")), "ip_restrict": bool(d.get("ipRestrict")), "raw": d}

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._req("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)}, signed=True)

    def set_isolated(self, symbol: str) -> None:
        try:
            self._req("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}, signed=True)
        except ExchangeError as e:
            if e.code != -4046:  # "No need to change margin type."
                raise

    # ---- orders ----------------------------------------------------------
    @staticmethod
    def _res(d: dict[str, Any], client_id: str) -> OrderResult:
        return OrderResult(client_id=client_id, exchange_id=str(d.get("orderId") or d.get("algoId") or ""),
                           symbol=d.get("symbol", ""), side=d.get("side", ""),
                           type=d.get("type") or d.get("orderType", ""),
                           status=d.get("status") or d.get("algoStatus", "UNKNOWN"),
                           qty=float(d.get("origQty") or d.get("quantity") or 0),
                           filled_qty=float(d.get("executedQty") or 0), avg_price=float(d.get("avgPrice") or 0), raw=d)

    def _place(self, params: dict[str, Any], client_id: str, path: str = "/fapi/v1/order") -> OrderResult:
        try:
            d = self._req("POST", path, params, signed=True)
            return self._res(d, client_id)
        except ExchangeError as e:
            if not e.retriable:
                raise
            # status unknown: look it up by client id before deciding anything
            time.sleep(0.5)
            try:
                if path.endswith("algoOrder"):
                    d = self._req("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_id}, signed=True)
                    return self._res(d, client_id)
                return self.query_order(params["symbol"], client_id)
            except ExchangeError:
                return OrderResult(client_id, None, params["symbol"], params.get("side", ""),
                                   params.get("type", ""), "UNKNOWN", float(params.get("quantity") or 0))

    def market_order(self, symbol, side, qty, client_id, reduce_only=False) -> OrderResult:
        return self._place({"symbol": symbol, "side": side, "type": "MARKET", "quantity": _fmt(qty),
                            "newClientOrderId": client_id, "reduceOnly": "true" if reduce_only else None,
                            "newOrderRespType": "RESULT"}, client_id)

    def limit_order(self, symbol, side, qty, price, client_id, post_only=True) -> OrderResult:
        return self._place({"symbol": symbol, "side": side, "type": "LIMIT", "quantity": _fmt(qty),
                            "price": _fmt(price), "timeInForce": "GTX" if post_only else "GTC",
                            "newClientOrderId": client_id, "newOrderRespType": "RESULT"}, client_id)

    def query_order(self, symbol, client_id) -> OrderResult:
        d = self._req("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}, signed=True)
        return self._res(d, client_id)

    def cancel_order(self, symbol, client_id) -> None:
        try:
            self._req("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}, signed=True)
        except ExchangeError as e:
            if e.code not in (-2011, -2013):  # unknown order / does not exist
                raise

    def conditional_order(self, symbol, side, type_, trigger, client_id, qty=None, close_position=False) -> OrderResult:
        p = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": type_,
             "triggerPrice": _fmt(trigger), "clientAlgoId": client_id, "workingType": "CONTRACT_PRICE",
             "priceProtect": "true", "newOrderRespType": "RESULT"}
        if close_position:
            p["closePosition"] = "true"
        else:
            p["quantity"] = _fmt(qty)
            p["reduceOnly"] = "true"
        return self._place(p, client_id, "/fapi/v1/algoOrder")

    def cancel_conditional(self, symbol, client_id) -> None:
        try:
            self._req("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_id}, signed=True)
        except ExchangeError as e:
            if e.retriable:
                raise
            log.info("cancel algo %s: %s", client_id, e)

    def cancel_all(self, symbol) -> None:
        self._req("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)
        try:
            self._req("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, signed=True)
        except ExchangeError as e:
            log.info("cancel algo open orders %s: %s", symbol, e)

    def positions(self) -> dict[str, tuple[float, float]]:
        rows = self._req("GET", "/fapi/v2/positionRisk", signed=True)
        out: dict[str, tuple[float, float]] = {}
        for r in rows:
            amt = float(r.get("positionAmt", 0))
            if amt != 0:
                prev = out.get(r["symbol"], (0.0, 0.0))
                out[r["symbol"]] = (prev[0] + amt, float(r.get("entryPrice", 0)))
        return out

    def open_conditionals(self, symbol: str) -> list[dict[str, Any]]:
        rows = self._req("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
        rows = rows.get("orders", rows) if isinstance(rows, dict) else rows
        return [{"client_id": r.get("clientAlgoId"), "type": r.get("orderType") or r.get("type"),
                 "trigger": float(r.get("triggerPrice") or 0), "status": r.get("algoStatus"),
                 "close_position": str(r.get("closePosition")).lower() == "true",
                 "qty": float(r.get("quantity") or 0)} for r in rows]

    def query_conditional(self, symbol: str, client_id: str) -> OrderResult:
        d = self._req("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_id}, signed=True)
        return self._res(d, client_id)

    def realized_since(self, symbol, since_ms) -> dict[str, float]:
        rows = self._req("GET", "/fapi/v1/income", {"symbol": symbol, "startTime": since_ms, "limit": 1000},
                         signed=True)
        out = {"realized": 0.0, "commission": 0.0, "funding": 0.0}
        for r in rows:
            t = r.get("incomeType")
            v = float(r.get("income", 0))
            if t == "REALIZED_PNL":
                out["realized"] += v
            elif t == "COMMISSION":
                out["commission"] += -v
            elif t == "FUNDING_FEE":
                out["funding"] += -v
        return out


def _fmt(x: Optional[float]) -> Optional[str]:
    if x is None:
        return None
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s or "0"
