"""Real-time price feeds.

* BinanceRestFeed - Binance public market data (no key). Default host data-api.binance.vision
  (public spot market data). On servers where Binance futures hosts are reachable, pass
  base=https://fapi.binance.com and product="usdm" to price futures from futures books.
* PushFeed - prices pushed by a broker adapter (cTrader spot events, MT5 ticks) or by signal
  payloads. Quotes older than `max_age_s` are refused (never priced from stale data).
"""
from __future__ import annotations

import threading
import time

import httpx

from ..services.http import RateLimiter, with_retries


class StalePrice(LookupError):
    pass


class PriceFeed:
    def quote(self, symbol: str) -> tuple[float, float]:
        raise NotImplementedError

    def mids(self) -> dict[str, float]:
        return {}


class PushFeed(PriceFeed):
    def __init__(self, max_age_s: float = 30.0):
        self.max_age_s = max_age_s
        self._q: dict[str, tuple[float, float, float]] = {}
        self._lock = threading.Lock()

    def update(self, symbol: str, bid: float, ask: float, ts: float | None = None) -> None:
        with self._lock:
            self._q[symbol] = (float(bid), float(ask), ts or time.time())

    def quote(self, symbol):
        with self._lock:
            q = self._q.get(symbol)
        if q is None:
            raise StalePrice(f"no price for {symbol}")
        if time.time() - q[2] > self.max_age_s:
            raise StalePrice(f"price for {symbol} is {time.time() - q[2]:.0f}s old")
        return q[0], q[1]

    def mids(self):
        now = time.time()
        with self._lock:
            return {s: (b + a) / 2 for s, (b, a, t) in self._q.items() if now - t <= max(self.max_age_s, 3600)}


class BinanceRestFeed(PriceFeed):
    PATHS = {"spot": "/api/v3", "usdm": "/fapi/v1", "coinm": "/dapi/v1"}

    def __init__(self, base: str = "https://data-api.binance.vision", product: str = "spot",
                 ttl_s: float = 1.0, client: httpx.Client | None = None):
        self.base, self.product, self.ttl = base.rstrip("/"), product, ttl_s
        self.http = client or httpx.Client(timeout=10)
        self.cache: dict[str, tuple[float, float, float]] = {}
        self.limiter = RateLimiter(10, 20)

    def _get(self, path, **params):
        self.limiter.acquire()
        r = with_retries(lambda: self.http.get(f"{self.base}{self.PATHS[self.product]}{path}", params=params))
        r.raise_for_status()
        return r.json()

    def quote(self, symbol):
        c = self.cache.get(symbol)
        if c and time.time() - c[2] < self.ttl:
            return c[0], c[1]
        d = self._get("/ticker/bookTicker", symbol=symbol)
        if isinstance(d, list):
            d = d[0]
        bid, ask = float(d["bidPrice"]), float(d["askPrice"])
        self.cache[symbol] = (bid, ask, time.time())
        return bid, ask

    def klines(self, symbol: str, interval: str = "1h", limit: int = 300) -> list[dict]:
        rows = self._get("/klines", symbol=symbol, interval=interval, limit=limit)
        return [{"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                 "v": float(r[5])} for r in rows]

    def exchange_info(self, symbols: list[str] | None = None) -> dict:
        params = {}
        if symbols and self.product == "spot":
            import json

            params["symbols"] = json.dumps(symbols, separators=(",", ":"))
        return self._get("/exchangeInfo", **params)
