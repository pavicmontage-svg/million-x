"""Binance USDT-M Futures public market data (REST) with rate limiting, retries and failover.

Base URLs:
  live     https://fapi.binance.com
  testnet  https://testnet.binancefuture.com

Only public, unauthenticated endpoints live here. Signed trading endpoints are in
``quanttitan.execution.binance_futures``.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from ..core.logging import get_logger
from ..core.types import ContractSpec
from .candles import TF_MS, Candles

log = get_logger("data.rest")

BASE_URLS = {
    "binance_live": "https://fapi.binance.com",
    "binance_testnet": "https://testnet.binancefuture.com",
}
WS_URLS = {
    "binance_live": "wss://fstream.binance.com",
    "binance_testnet": "wss://stream.binancefuture.com",
}


class MarketDataError(RuntimeError):
    pass


@dataclass
class WeightLimiter:
    """Client-side request-weight budget (Binance: 2400/min live, 6000/min testnet). We use a
    conservative fraction and also honour the server's X-MBX-USED-WEIGHT-1M header."""

    limit_per_min: int = 1200
    used: int = 0
    window_start: float = 0.0

    async def acquire(self, weight: int) -> None:
        now = time.monotonic()
        if now - self.window_start >= 60:
            self.window_start, self.used = now, 0
        if self.used + weight > self.limit_per_min:
            wait = 60 - (now - self.window_start) + 0.05
            log.warning("request-weight budget exhausted, sleeping %.1fs", wait)
            await asyncio.sleep(max(0.0, wait))
            self.window_start, self.used = time.monotonic(), 0
        self.used += weight

    def observe(self, header: str | None) -> None:
        if header and header.isdigit():
            self.used = max(self.used, int(header))


class BinancePublic:
    def __init__(self, source: str = "binance_live", timeout: float = 10.0, retries: int = 3,
                 client: httpx.AsyncClient | None = None) -> None:
        self.base = BASE_URLS[source]
        self.source = source
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self.limiter = WeightLimiter()
        self.last_ok_ms = 0
        self.consecutive_failures = 0

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None, weight: int = 1):
        await self.limiter.acquire(weight)
        delay = 0.5
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.get(self.base + path, params=params)
                self.limiter.observe(r.headers.get("x-mbx-used-weight-1m"))
                if r.status_code == 429 or r.status_code == 418:
                    retry_after = float(r.headers.get("retry-after", "5"))
                    log.warning("rate limited (%s) on %s, backing off %.0fs", r.status_code, path, retry_after)
                    await asyncio.sleep(min(retry_after, 60))
                    continue
                if r.status_code == 451:
                    raise MarketDataError("Binance refused service from this location (HTTP 451)")
                if r.status_code >= 500:
                    raise MarketDataError(f"server error {r.status_code}")
                if r.status_code != 200:
                    raise MarketDataError(f"HTTP {r.status_code}: {r.text[:200]}")
                self.last_ok_ms = int(time.time() * 1000)
                self.consecutive_failures = 0
                return r.json()
            except (httpx.TransportError, MarketDataError) as e:
                last_exc = e
                if isinstance(e, MarketDataError) and "451" in str(e):
                    break
                if attempt < self.retries:
                    await asyncio.sleep(delay)
                    delay *= 2
        self.consecutive_failures += 1
        raise MarketDataError(f"GET {path} failed: {last_exc}")

    # ------------------------------------------------------------------ endpoints
    async def ping(self) -> bool:
        await self._get("/fapi/v1/ping")
        return True

    async def server_time(self) -> int:
        return int((await self._get("/fapi/v1/time"))["serverTime"])

    async def exchange_info(self) -> dict[str, ContractSpec]:
        d = await self._get("/fapi/v1/exchangeInfo", weight=1)
        out: dict[str, ContractSpec] = {}
        for s in d.get("symbols", []):
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
                continue
            f = {x["filterType"]: x for x in s.get("filters", [])}
            lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE", {})
            out[s["symbol"]] = ContractSpec(
                symbol=s["symbol"],
                tick_size=float(f.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
                step_size=float(f.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                min_qty=float(f.get("LOT_SIZE", {}).get("minQty", 0.001)),
                max_qty=float(lot.get("maxQty", 1e9)),
                min_notional=float(f.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
                onboard_ms=int(s.get("onboardDate", 0)),
                status=s.get("status", "TRADING"),
            )
        return out

    async def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None,
                     end_ms: int | None = None, closed_only: bool = True) -> Candles:
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        w = 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
        rows = await self._get("/fapi/v1/klines", params, weight=w)
        if closed_only:
            now = int(time.time() * 1000)
            rows = [r for r in rows if int(r[6]) < now]
        return Candles.from_rows(symbol, interval, rows)

    async def klines_range(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> Candles:
        out = Candles(symbol, interval)
        cur = start_ms
        step = TF_MS[interval]
        while cur < end_ms:
            c = await self.klines(symbol, interval, 1500, start_ms=cur, end_ms=end_ms)
            if len(c) == 0:
                break
            out = out.append(c)
            cur = int(c.ts[-1]) + step
        return out

    async def depth(self, symbol: str, limit: int = 20) -> dict:
        w = 2 if limit <= 50 else 5 if limit <= 100 else 10
        d = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit}, weight=w)
        return {"bids": [[float(p), float(q)] for p, q in d["bids"]],
                "asks": [[float(p), float(q)] for p, q in d["asks"]],
                "ts": int(d.get("T", d.get("E", 0)))}

    async def agg_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        rows = await self._get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit}, weight=20)
        return [{"price": float(r["p"]), "qty": float(r["q"]), "ts": int(r["T"]),
                 "buyer_maker": bool(r["m"])} for r in rows]

    async def tickers_24h(self) -> list[dict]:
        rows = await self._get("/fapi/v1/ticker/24hr", weight=40)
        return [{"symbol": r["symbol"], "quote_volume": float(r["quoteVolume"]), "last": float(r["lastPrice"]),
                 "change_pct": float(r["priceChangePercent"]), "count": int(r.get("count", 0))} for r in rows]

    async def book_tickers(self) -> dict[str, dict]:
        rows = await self._get("/fapi/v1/ticker/bookTicker", weight=5)
        return {r["symbol"]: {"bid": float(r["bidPrice"]), "ask": float(r["askPrice"]),
                              "bid_qty": float(r["bidQty"]), "ask_qty": float(r["askQty"])} for r in rows}

    async def premium_index(self) -> dict[str, dict]:
        rows = await self._get("/fapi/v1/premiumIndex", weight=10)
        if isinstance(rows, dict):
            rows = [rows]
        return {r["symbol"]: {"mark": float(r["markPrice"]), "index": float(r["indexPrice"]),
                              "funding": float(r["lastFundingRate"] or 0.0),
                              "next_funding_ms": int(r["nextFundingTime"])} for r in rows}

    async def funding_history(self, symbol: str, start_ms: int | None = None, limit: int = 1000) -> list[dict]:
        p = {"symbol": symbol, "limit": limit}
        if start_ms:
            p["startTime"] = start_ms
        rows = await self._get("/fapi/v1/fundingRate", p, weight=1)
        return [{"ts": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows]
