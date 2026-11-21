"""Live Market Microstructure Collector (2.1, §5 and §11).

Binance publishes NO historical order book for USDⓈ-M futures and the public liquidation
archive stopped in 2024, so MillionX records its own. One WebSocket connection carries, per
instrument: partial book depth (top 20 levels, 500 ms), aggregated trades, liquidation orders
and mark price / funding (1 s). Open interest is polled over REST every 60 s.

Since 2026-03 Binance routes futures streams by path (/public, /market); one connection per route.
Everything is aggregated into 1-second rows and stored in a SEPARATE SQLite file (micro.db) so
recording never contends with the trading database. Every row keeps its exchange event time,
the local receive time and the source stream. Disconnects, gaps, drift and invalid messages are
logged as data-quality events; nothing is interpolated.

  python -m millionx.micro.collector --db data/micro.db --symbols BTCUSDT,ETHUSDT --minutes 60

Liquidations: the stream pushes at most ONE liquidation per symbol per second (the latest),
so recorded liquidation volume is a LOWER BOUND, flagged as such in every row and report.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Since 2026-03 Binance routes USDⓈ-M streams by path: depth is on /public, aggTrade / markPrice /
# forceOrder on /market. An unrouted connection silently receives only /public data (seen on CI:
# depth arrived, trades and mark price did not), so the collector opens one connection per route.
WS_ROOT = os.environ.get("MX_FUTURES_WS", "wss://fstream.binance.com")
ROUTES = {"public": ("depth20@500ms",), "market": ("aggTrade", "forceOrder", "markPrice@1s")}
REST_BASE = os.environ.get("MX_FUTURES_REST", "https://fapi.binance.com")
SCHEMA = """
CREATE TABLE IF NOT EXISTS book_1s (sym TEXT, ts INTEGER, ev_ts INTEGER, rx_ts INTEGER, mid REAL, spread_bps REAL,
  bid5 REAL, ask5 REAL, bid10 REAL, ask10 REAL, bid20 REAL, ask20 REAL, imb5 REAL, imb10 REAL, imb20 REAL,
  microprice REAL, bid_wall_px REAL, bid_wall_qty REAL, ask_wall_px REAL, ask_wall_qty REAL,
  removed_bid10 REAL, removed_ask10 REAL, impact_buy_bps REAL, impact_sell_bps REAL, updates INTEGER,
  PRIMARY KEY (sym, ts));
CREATE TABLE IF NOT EXISTS trades_1s (sym TEXT, ts INTEGER, buy_qty REAL, sell_qty REAL, buy_n INTEGER, sell_n INTEGER,
  large_buy_qty REAL, large_sell_qty REAL, vwap REAL, first_id INTEGER, last_id INTEGER, PRIMARY KEY (sym, ts));
CREATE TABLE IF NOT EXISTS liquidations (sym TEXT, ev_ts INTEGER, rx_ts INTEGER, side TEXT, price REAL, qty REAL,
  notional REAL, status TEXT, lower_bound INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS funding_1s (sym TEXT, ts INTEGER, mark REAL, index_px REAL, funding_rate REAL,
  next_funding INTEGER, PRIMARY KEY (sym, ts));
CREATE TABLE IF NOT EXISTS oi_1m (sym TEXT, ts INTEGER, oi REAL, rx_ts INTEGER, PRIMARY KEY (sym, ts));
CREATE TABLE IF NOT EXISTS quality_events (ts INTEGER, sym TEXT, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_liq ON liquidations(sym, ev_ts);
CREATE INDEX IF NOT EXISTS ix_q ON quality_events(ts);
"""
LARGE_TRADE_QUANTILE_WINDOW = 5000  # trades used for the rolling "large trade" threshold (p99 notional)


def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


def book_features(bids: list[list[float]], asks: list[list[float]], prev: Optional[dict] = None,
                  impact_notional: float = 50_000.0) -> dict[str, float]:
    """Features of one top-20 partial book. bids/asks: [[price, qty], ...] best first."""
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    out: dict[str, float] = {"mid": mid, "spread_bps": (ba - bb) / mid * 1e4}
    for n in (5, 10, 20):
        b = sum(q for _, q in bids[:n])
        a = sum(q for _, q in asks[:n])
        out[f"bid{n}"], out[f"ask{n}"] = b, a
        out[f"imb{n}"] = (b - a) / (b + a) if b + a > 0 else 0.0
    qb, qa = bids[0][1], asks[0][1]
    out["microprice"] = (ba * qb + bb * qa) / (qb + qa) if qb + qa > 0 else mid
    for side, lv in (("bid", bids), ("ask", asks)):  # liquidity wall: level >= 5x median level size
        qs = sorted(q for _, q in lv)
        med = qs[len(qs) // 2] if qs else 0
        big = max(lv, key=lambda x: x[1]) if lv else (math.nan, 0)
        wall = big[1] >= 5 * med and med > 0
        out[f"{side}_wall_px"] = big[0] if wall else math.nan
        out[f"{side}_wall_qty"] = big[1] if wall else 0.0
    for side, lv in (("buy", asks), ("sell", bids)):  # price impact of a market order of `impact_notional`
        need, cost, qty = impact_notional, 0.0, 0.0
        for p, q in lv:
            take = min(q, need / p)
            cost += take * p
            qty += take
            need -= take * p
            if need <= 1e-9:
                break
        out[f"impact_{side}_bps"] = (abs(cost / qty - mid) / mid * 1e4) if qty > 0 and need <= 1e-9 else math.nan
    if prev:  # liquidity removed from the top 10 since the previous second (not refilled)
        out["removed_bid10"] = max(0.0, prev["bid10"] - out["bid10"])
        out["removed_ask10"] = max(0.0, prev["ask10"] - out["ask10"])
    else:
        out["removed_bid10"] = out["removed_ask10"] = math.nan
    return out


@dataclass
class _SymState:
    book: Optional[dict] = None
    book_ev: int = 0
    book_updates: int = 0
    prev_feat: Optional[dict] = None
    tr: dict = field(default_factory=lambda: defaultdict(float))
    tr_first: Optional[int] = None
    tr_last: Optional[int] = None
    last_trade_id: Optional[int] = None
    notionals: list = field(default_factory=list)
    mark: Optional[dict] = None
    lat: list = field(default_factory=list)     # exchange trade time -> local receive, ms
    last_close: Optional[float] = None


class Aggregator:
    """Pure message -> 1-second rows logic (no network), so it is unit-tested."""

    def __init__(self, db: sqlite3.Connection, now: Callable[[], int] = lambda: int(time.time() * 1000)):
        self.db = db
        self.now = now
        self.st: dict[str, _SymState] = defaultdict(_SymState)
        self.lock = threading.RLock()
        self.counts: dict[str, int] = defaultdict(int)
        self.listeners: list[Callable[[int, dict[str, dict]], None]] = []   # called once per second (2.2 scanner)

    def quality(self, sym: str, kind: str, detail: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO quality_events VALUES(?,?,?,?)", (self.now(), sym, kind, detail[:500]))

    def on_message(self, raw: str) -> None:
        rx = self.now()
        try:
            m = json.loads(raw)
            stream, d = m.get("stream", ""), m.get("data", m)
            sym = (d.get("s") or (d.get("o") or {}).get("s") or "").upper()
            ev = int(d.get("E") or d.get("T") or 0)
        except (ValueError, AttributeError, TypeError) as e:
            self.quality("*", "invalid_message", str(e))
            return
        if not sym:
            self.quality("*", "invalid_message", "no symbol")
            return
        if stream and not stream.lower().startswith(sym.lower()):
            self.quality(sym, "symbol_mapping", f"{sym} on stream {stream}")
            return
        if ev and abs(rx - ev) > 5_000:
            self.quality(sym, "timestamp_drift", f"receive - event = {rx - ev} ms")
        with self.lock:
            s = self.st[sym]
            et = d.get("e")
            if et == "depthUpdate":
                try:
                    bids = [[float(p), float(q)] for p, q in d["b"]][:20]
                    asks = [[float(p), float(q)] for p, q in d["a"]][:20]
                except (KeyError, ValueError):
                    self.quality(sym, "invalid_message", "depth parse")
                    return
                if not bids or not asks or bids[0][0] <= 0 or asks[0][0] <= bids[0][0]:
                    self.quality(sym, "invalid_price", f"crossed/empty book {bids[:1]} {asks[:1]}")
                    return
                if ev and ev < s.book_ev:
                    self.quality(sym, "out_of_order", f"depth {ev} < {s.book_ev}")
                    return
                s.book, s.book_ev = {"b": bids, "a": asks, "rx": rx}, ev
                s.book_updates += 1
            elif et == "aggTrade":
                p, q, aid = float(d["p"]), float(d["q"]), int(d["a"])
                if p <= 0 or q <= 0:
                    self.quality(sym, "invalid_price", f"trade {p} {q}")
                    return
                if s.last_trade_id is not None:
                    if aid <= s.last_trade_id:
                        self.quality(sym, "duplicate_or_out_of_order", f"aggTrade {aid} <= {s.last_trade_id}")
                        return
                    if aid > s.last_trade_id + 1:
                        self.quality(sym, "trade_gap", f"{aid - s.last_trade_id - 1} aggTrades missing")
                s.last_trade_id = aid
                notional = p * q
                s.notionals.append(notional)
                if len(s.notionals) > LARGE_TRADE_QUANTILE_WINDOW:
                    s.notionals = s.notionals[-LARGE_TRADE_QUANTILE_WINDOW:]
                thr = sorted(s.notionals)[int(0.99 * (len(s.notionals) - 1))] if len(s.notionals) >= 200 else math.inf
                side = "sell" if d["m"] else "buy"  # m = buyer is maker -> aggressive SELL
                s.tr[f"{side}_qty"] += q
                s.tr[f"{side}_n"] += 1
                s.tr["pq"] += notional
                s.tr["q"] += q
                if notional >= thr:
                    s.tr[f"large_{side}_qty"] += q
                s.tr_first = s.tr_first if s.tr_first is not None else aid
                s.tr_last = aid
                if "o" not in s.tr:  # 1 s OHLC for the live scanner
                    s.tr["o"] = s.tr["h"] = s.tr["l"] = p
                s.tr["h"] = max(s.tr["h"], p)
                s.tr["l"] = min(s.tr["l"], p)
                s.tr["c"] = p
                s.lat.append(rx - int(d.get("T") or ev or rx))
                if len(s.lat) > 600:
                    del s.lat[:300]
            elif et == "forceOrder":
                o = d["o"]
                qty, px = float(o.get("z") or o.get("q")), float(o.get("ap") or o.get("p"))
                side = "long_liquidated" if o["S"] == "SELL" else "short_liquidated"
                self.db.execute("INSERT INTO liquidations VALUES(?,?,?,?,?,?,?,?,1)",
                                (sym, int(o.get("T") or ev), rx, side, px, qty, px * qty, o.get("X", "")))
            elif et == "markPriceUpdate":
                s.mark = {"mark": float(d["p"]), "index": float(d.get("i") or 0) or None,
                          "r": float(d["r"]) if d.get("r") not in (None, "") else None, "T": int(d.get("T") or 0)}
            else:
                return
            self.counts[et] += 1

    def flush(self, ts_s: Optional[int] = None) -> int:
        """Write one row per symbol for the second that just ended. Returns rows written."""
        ts = (ts_s if ts_s is not None else self.now() // 1000) * 1000
        n = 0
        second: dict[str, dict] = {}
        with self.lock:
            for sym, s in self.st.items():
                t_ = s.tr
                c = t_.get("c", s.last_close)
                if c is not None:
                    second[sym] = {"o": t_.get("o", c), "h": t_.get("h", c), "l": t_.get("l", c), "c": c,
                                   "buy": t_.get("buy_qty", 0.0), "sell": t_.get("sell_qty", 0.0),
                                   "n": t_.get("buy_n", 0) + t_.get("sell_n", 0),
                                   "book": None, "lat_ms": (sorted(s.lat)[len(s.lat) // 2] if s.lat else None)}
                    s.last_close = c
                if s.book and self.now() - s.book["rx"] <= 3_000:
                    f = book_features(s.book["b"], s.book["a"], s.prev_feat)
                    s.prev_feat = f
                    self.db.execute("INSERT OR REPLACE INTO book_1s VALUES(" + ",".join("?" * 25) + ")",
                                    (sym, ts, s.book_ev, s.book["rx"], f["mid"], f["spread_bps"], f["bid5"], f["ask5"],
                                     f["bid10"], f["ask10"], f["bid20"], f["ask20"], f["imb5"], f["imb10"], f["imb20"],
                                     f["microprice"], f["bid_wall_px"], f["bid_wall_qty"], f["ask_wall_px"],
                                     f["ask_wall_qty"], f["removed_bid10"], f["removed_ask10"], f["impact_buy_bps"],
                                     f["impact_sell_bps"], s.book_updates))
                    s.book_updates = 0
                    n += 1
                    if sym in second:
                        second[sym]["book"] = {**f, "age_ms": self.now() - s.book["rx"]}
                elif s.book:
                    s.prev_feat = None  # stale book: no row, and no "removed liquidity" across the gap
                if s.tr:
                    t = s.tr
                    self.db.execute("INSERT OR REPLACE INTO trades_1s VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    (sym, ts, t["buy_qty"], t["sell_qty"], int(t["buy_n"]), int(t["sell_n"]),
                                     t["large_buy_qty"], t["large_sell_qty"], t["pq"] / t["q"] if t["q"] else None,
                                     s.tr_first, s.tr_last))
                    s.tr = defaultdict(float)
                    s.tr_first = None
                    n += 1
                if s.mark:
                    mk = s.mark
                    self.db.execute("INSERT OR REPLACE INTO funding_1s VALUES(?,?,?,?,?,?)",
                                    (sym, ts, mk["mark"], mk["index"], mk["r"], mk["T"]))
                    s.mark = None
                    n += 1
        for fn in list(self.listeners):
            try:
                fn(ts, second)
            except Exception as e:  # a listener can never stop recording
                self.quality("*", "listener_error", f"{type(e).__name__}: {e}")
        return n


def streams_for(symbols: list[str], route: Optional[str] = None) -> str:
    kinds = ROUTES[route] if route else tuple(k for r in ROUTES.values() for k in r)
    order = ("depth20@500ms", "aggTrade", "forceOrder", "markPrice@1s")
    return "/".join(f"{s.lower()}@{k}" for s in symbols for k in order if k in kinds)


def url_for(symbols: list[str], route: str) -> str:
    return f"{WS_ROOT}/{route}/stream?streams={streams_for(symbols, route)}"


def poll_open_interest(db: sqlite3.Connection, symbols: list[str], stop: threading.Event, agg: Aggregator) -> None:
    import requests
    sess = requests.Session()
    while not stop.is_set():
        t0 = time.time()
        for s in symbols:
            try:
                r = sess.get(f"{REST_BASE}/fapi/v1/openInterest", params={"symbol": s}, timeout=10)
                if r.status_code != 200:
                    agg.quality(s, "oi_poll_failed", f"HTTP {r.status_code}")
                    continue
                d = r.json()
                with agg.lock:
                    db.execute("INSERT OR REPLACE INTO oi_1m VALUES(?,?,?,?)",
                               (s, int(d["time"]) // 60_000 * 60_000, float(d["openInterest"]), int(time.time() * 1000)))
            except Exception as e:
                agg.quality(s, "oi_poll_failed", str(e)[:200])
        stop.wait(max(1.0, 60 - (time.time() - t0)))


def retention(db: sqlite3.Connection, book_days: int = 90, trade_days: int = 180) -> None:
    now = int(time.time() * 1000)
    db.execute("DELETE FROM book_1s WHERE ts < ?", (now - book_days * 86_400_000,))
    db.execute("DELETE FROM trades_1s WHERE ts < ?", (now - trade_days * 86_400_000,))
    db.execute("DELETE FROM quality_events WHERE ts < ?", (now - 365 * 86_400_000,))


def backup(db: sqlite3.Connection, path: str) -> str:
    dst = f"{path}.{time.strftime('%Y%m%d')}.bak"
    out = sqlite3.connect(dst)
    db.backup(out)
    out.close()
    return dst


def _ws_loop(url: str, route: str, agg: Aggregator, stop: threading.Event, end: Optional[float],
             stats: dict[str, int], log: Callable[[str], None]) -> None:
    from .wsclient import WebSocket, WSClosed
    backoff = 1.0
    while not stop.is_set() and (end is None or time.time() < end):
        ws = None
        try:
            ws = WebSocket.connect(url)
            stats[route] = stats.get(route, 0) + 1
            agg.quality("*", "ws_connected", f"{route} connection #{stats[route]}")
            backoff = 1.0
            ws.sock.settimeout(10)
            while not stop.is_set() and (end is None or time.time() < end):
                agg.on_message(ws.recv())
        except (WSClosed, OSError, ConnectionError) as e:
            agg.quality("*", "ws_disconnect", f"{route}: {type(e).__name__}: {e}"[:300])
            log(f"collector[{route}]: disconnected ({e}); reconnect in {backoff:.0f}s")
            stop.wait(backoff)
            backoff = min(60.0, backoff * 2)
        finally:
            if ws:
                ws.close()


def run(db_path: str, symbols: list[str], minutes: float = 0, stop: Optional[threading.Event] = None,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    """Collect until `minutes` elapse (0 = forever) or `stop` is set. One reconnecting WebSocket per route."""
    db = open_db(db_path)
    agg = Aggregator(db)
    stop = stop or threading.Event()
    end = time.time() + minutes * 60 if minutes else None
    stats: dict[str, int] = {}
    threads = [threading.Thread(target=poll_open_interest, args=(db, symbols, stop, agg), daemon=True)]
    for route in ROUTES:
        threads.append(threading.Thread(target=_ws_loop, args=(url_for(symbols, route), route, agg, stop, end, stats, log),
                                        daemon=True, name=f"ws-{route}"))
    for t in threads:
        t.start()
    last_flush = int(time.time())
    last_maint = time.time()
    while not stop.is_set() and (end is None or time.time() < end):
        stop.wait(0.2)
        sec = int(time.time())
        if sec != last_flush:
            agg.flush(sec - 1)
            last_flush = sec
        if time.time() - last_maint > 6 * 3600:
            with agg.lock:
                retention(db)
                backup(db, db_path)
            last_maint = time.time()
    stop.set()
    for t in threads[1:]:
        t.join(timeout=15)
    return {"connects": stats, "messages": dict(agg.counts)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("MX_MICRO_DB", "data/micro.db"))
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,LTCUSDT")
    p.add_argument("--minutes", type=float, default=0)
    p.add_argument("--out", default="")
    a = p.parse_args()
    res = run(a.db, a.symbols.split(","), a.minutes)
    print(json.dumps(res))
    if a.out:
        json.dump(res, open(a.out, "w"))


if __name__ == "__main__":
    main()
