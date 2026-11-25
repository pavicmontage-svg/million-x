"""Market-data feeds for the live runtime.

LiveFeed      Binance WebSocket streams (1m/5m klines, partial depth, aggTrades, mark price) with
              heartbeat/staleness detection and automatic fallback to REST polling; REST backfill
              on start and after reconnects so no closed bar is missed.
ReplayFeed    Replays historical bars through the exact live pipeline (demos, drills, tests, and
              environments where Binance is unreachable). Synthesises a 1bps book around the close.

Both expose the same state: closed 5m candles per symbol, the latest book, recent trades, mark,
funding, next funding time, and ``data_age_s``. Bar closes are delivered to ``on_bar_close``.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

import numpy as np

from ..core.logging import get_logger
from .binance_public import WS_URLS, BinancePublic, MarketDataError
from .candles import DAY_MS, TF_MS, Candles, bars_in
from .synthetic import synthetic_book

log = get_logger("feed")
BarCallback = Callable[[str, int], Awaitable[None]]
HISTORY_DAYS = 8.5  # the regime engine needs 7 days for volatility percentiles


def history_bars(tf: str) -> int:
    return bars_in(tf, int(HISTORY_DAYS * DAY_MS))


class FeedState:
    def __init__(self) -> None:
        self.candles: dict[str, Candles] = {}
        self.book: dict[str, dict] = {}
        self.trades: dict[str, deque] = defaultdict(lambda: deque(maxlen=2000))
        self.mark: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.funding: dict[str, float] = {}
        self.next_funding: dict[str, int] = {}
        self.updated: dict[str, float] = {}
        self.status = "idle"
        self.mode = ""
        self.errors = 0

    def data_age_s(self, symbol: str) -> float:
        t = self.updated.get(symbol)
        return time.time() - t if t else float("inf")

    def recent_trades(self, symbol: str, window_ms: int = 300_000) -> list[dict]:
        cut = int(time.time() * 1000) - window_ms
        return [t for t in self.trades[symbol] if t["ts"] >= cut]


class LiveFeed:
    def __init__(self, source: str, symbols: list[str], on_bar_close: BarCallback, *, poll_s: float = 5.0,
                 tf: str = "3m", use_ws: bool = True) -> None:
        self.source = source
        self.rest = BinancePublic(source)
        self.symbols = list(symbols)
        self.on_bar_close = on_bar_close
        self.poll_s = poll_s
        self.tf = tf
        self.use_ws = use_ws
        self.state = FeedState()
        self._stop = asyncio.Event()
        self._last_closed: dict[str, int] = {}

    def set_symbols(self, symbols: list[str]) -> None:
        self.symbols = list(symbols)

    async def stop(self) -> None:
        self._stop.set()
        await self.rest.close()

    async def backfill(self, symbol: str) -> None:
        tfm = TF_MS[self.tf]
        end = int(time.time() * 1000)
        c = await self.rest.klines_range(symbol, self.tf, end - history_bars(self.tf) * tfm, end)
        self.state.candles[symbol] = c if symbol not in self.state.candles else self.state.candles[symbol].append(c)
        if len(c):
            self._last_closed[symbol] = int(c.ts[-1])
        self.state.updated[symbol] = time.time()

    async def run(self) -> None:
        for s in self.symbols:
            try:
                await self.backfill(s)
            except MarketDataError as e:
                log.error("backfill %s failed: %s", s, e)
                self.state.errors += 1
        while not self._stop.is_set():
            ws_ok = False
            if self.use_ws:
                try:
                    import websockets  # noqa: F401
                    ws_ok = True
                except ImportError:
                    ws_ok = False
            try:
                if ws_ok:
                    self.state.mode = "websocket"
                    await self._run_ws()
                else:
                    self.state.mode = "rest"
                    await self._run_rest()
            except Exception as e:  # network loss etc.: fall back to REST for a while, then retry WS
                self.state.errors += 1
                self.state.status = f"degraded: {type(e).__name__}"
                log.warning("feed error (%s) — REST fallback", e)
                self.state.mode = "rest"
                try:
                    await asyncio.wait_for(self._run_rest(max_cycles=12), timeout=120)
                except asyncio.TimeoutError:
                    pass

    async def _poll_once(self) -> None:
        prem = await self.rest.premium_index()
        for s in self.symbols:
            p = prem.get(s)
            if p:
                self.state.mark[s], self.state.funding[s], self.state.next_funding[s] = p["mark"], p["funding"], p["next_funding_ms"]
            try:
                self.state.book[s] = await self.rest.depth(s, 20)
                c = await self.rest.klines(s, self.tf, 3)
                self.state.updated[s] = time.time()
                await self._ingest(s, c)
            except MarketDataError as e:
                log.warning("poll %s: %s", s, e)

    async def _ingest(self, s: str, c: Candles) -> None:
        if len(c) == 0:
            return
        if s not in self.state.candles:
            await self.backfill(s)
        cur = self.state.candles[s]
        last = self._last_closed.get(s, 0)
        tfm = TF_MS[self.tf]
        if int(c.ts[-1]) > last + tfm:  # gap: missed bars (e.g. after reconnect) → backfill the gap
            gap = await self.rest.klines_range(s, self.tf, last + tfm, int(c.ts[-1]) + tfm)
            c = gap.append(c)
        new = c.slice(int(np.searchsorted(c.ts, last, "right")), None)
        if len(new) == 0:
            return
        self.state.candles[s] = cur.append(new).tail(history_bars(self.tf) + 500)
        self.state.last[s] = float(new.close[-1])
        for t in new.ts:
            self._last_closed[s] = int(t)
            await self.on_bar_close(s, int(t) + tfm)

    async def _run_rest(self, max_cycles: int | None = None) -> None:
        self.state.status = "ok"
        n = 0
        while not self._stop.is_set() and (max_cycles is None or n < max_cycles):
            try:
                await self._poll_once()
                self.state.status = "ok"
            except MarketDataError as e:
                self.state.errors += 1
                self.state.status = f"degraded: {e}"
            n += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
            except asyncio.TimeoutError:
                pass

    async def _run_ws(self) -> None:
        import websockets
        streams = []
        for s in self.symbols:
            sl = s.lower()
            streams += [f"{sl}@kline_{self.tf}", f"{sl}@depth20@500ms", f"{sl}@aggTrade", f"{sl}@markPrice@1s"]
        url = f"{WS_URLS[self.source]}/stream?streams={'/'.join(streams)}"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 22) as ws:
            self.state.status = "ok"
            symbols_at_connect = list(self.symbols)
            while not self._stop.is_set():
                if self.symbols != symbols_at_connect:
                    return  # universe changed: reconnect with the new stream list
                msg = await asyncio.wait_for(ws.recv(), timeout=30)  # staleness guard
                d = json.loads(msg).get("data", {})
                await self._on_ws(d)

    async def _on_ws(self, d: dict) -> None:
        e = d.get("e")
        s = d.get("s")
        now = time.time()
        if e == "kline":
            k = d["k"]
            self.state.updated[s] = now
            self.state.last[s] = float(k["c"])
            if k.get("x"):  # closed
                row = [k["t"], k["o"], k["h"], k["l"], k["c"], k["v"], k["T"], k["q"], k["n"], k["V"], k["Q"], 0]
                await self._ingest(s, Candles.from_rows(s, self.tf, [row]))
        elif e == "depthUpdate":
            self.state.book[s] = {"bids": [[float(p), float(q)] for p, q in d["b"]], "asks": [[float(p), float(q)] for p, q in d["a"]],
                                  "ts": int(d.get("T", 0))}
            self.state.updated[s] = now
        elif e == "aggTrade":
            self.state.trades[s].append({"price": float(d["p"]), "qty": float(d["q"]), "ts": int(d["T"]), "buyer_maker": bool(d["m"])})
        elif e == "markPriceUpdate":
            self.state.mark[s] = float(d["p"])
            self.state.funding[s] = float(d.get("r") or 0.0)
            self.state.next_funding[s] = int(d.get("T") or 0)


class ReplayFeed:
    """Replays cached history bar by bar through the live runtime."""

    def __init__(self, data: dict[str, Candles], on_bar_close: BarCallback, *, warmup_bars: int | None = None,
                 speed_s: float = 0.0, funding: dict | None = None) -> None:
        self.data = data
        tf = next(iter(data.values())).timeframe if data else "3m"
        self.hist = history_bars(tf)
        warmup_bars = self.hist if warmup_bars is None else warmup_bars
        self.on_bar_close = on_bar_close
        self.warmup = warmup_bars
        self.speed = speed_s
        self.funding = funding or {}
        self.state = FeedState()
        self.state.mode = "replay"
        self.symbols = sorted(data)
        self._stop = asyncio.Event()
        self.clock_ms = 0
        self.done = False

    def set_symbols(self, symbols: list[str]) -> None:
        self.symbols = [s for s in symbols if s in self.data]

    async def stop(self) -> None:
        self._stop.set()

    def data_age_override(self) -> float:
        return 0.0

    async def run(self, max_bars: int | None = None) -> None:
        tl = np.unique(np.concatenate([self.data[s].close_ts for s in self.symbols]))
        start = min(self.warmup, len(tl) - 1)
        self.state.status = "ok"
        for k, ts in enumerate(tl[start:]):
            if self._stop.is_set() or (max_bars is not None and k >= max_bars):
                break
            ts = int(ts)
            self.clock_ms = ts
            for s in self.symbols:
                c = self.data[s]
                i = int(np.searchsorted(c.close_ts, ts, "right")) - 1
                if i < 0 or int(c.close_ts[i]) != ts:
                    continue
                self.state.candles[s] = c.slice(max(0, i + 1 - self.hist), i + 1)
                px = float(c.close[i])
                self.state.last[s] = self.state.mark[s] = px
                self.state.book[s] = synthetic_book(px, tick=px * 5e-5, depth=float(c.volume[i]) / 50 + 1, seed=k)
                self.state.updated[s] = time.time()
                f = self.funding.get(s)
                if f is not None and len(f[0]):
                    j = int(np.searchsorted(f[0], ts, "right")) - 1
                    self.state.funding[s] = float(f[1][j]) if j >= 0 else 0.0
                self.state.next_funding[s] = (ts // 28_800_000 + 1) * 28_800_000
                await self.on_bar_close(s, ts)
            if self.speed:
                await asyncio.sleep(self.speed)
            else:
                await asyncio.sleep(0)
        self.done = True
        self.state.status = "replay finished"
