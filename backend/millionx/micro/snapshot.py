"""Unified Market Microstructure snapshot (2.1, §7).

One time-aligned view per instrument. Every field carries timestamp, source, instrument, quality
status, freshness and a missing flag (micro.quality.Field). Stale values are returned as MISSING,
never as current; liquidations are labelled as a lower bound.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

import numpy as np

from . import quality as Q


def _one(db: sqlite3.Connection, sql: str, args: tuple) -> Optional[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute(sql, args).fetchone()


def build(symbol: str, db: Optional[sqlite3.Connection], bars=None, now_ms: Optional[int] = None,
          regime: Optional[str] = None, regime_ts: Optional[int] = None) -> dict[str, Any]:
    now = now_ms or int(time.time() * 1000)
    F: list[Q.Field] = []
    if bars is not None and len(bars):
        ts = int(bars.open_time[-1]) + 60_000
        F.append(Q.stamp("ohlcv", {"open": float(bars.open[-1]), "high": float(bars.high[-1]), "low": float(bars.low[-1]),
                                   "close": float(bars.close[-1]), "volume": float(bars.volume[-1])},
                         ts, "rest:klines 1m", symbol, now))
        n = min(60, len(bars))
        r = np.diff(np.log(bars.close[-n:]))
        F.append(Q.stamp("volatility", {"rv_1m_bps": float(r.std() * 1e4) if len(r) > 2 else None},
                         ts, "derived:klines", symbol, now))
    else:
        F += [Q.stamp("ohlcv", None, None, "rest:klines 1m", symbol, now),
              Q.stamp("volatility", None, None, "derived:klines", symbol, now)]
    F.append(Q.stamp("regime", regime, regime_ts, "regime2", symbol, now))
    if db is None:
        for name, src in (("funding", "ws:markPrice"), ("open_interest", "rest:openInterest"),
                          ("order_book", "ws:depth20"), ("spread", "ws:depth20"), ("cvd", "ws:aggTrade")):
            F.append(Q.stamp(name, None, None, src, symbol, now, note="collector not running"))
        F.append(Q.Field("liquidations", None, None, "ws:forceOrder", symbol, Q.MISSING, True, "collector not running"))
    else:
        f = _one(db, "SELECT * FROM funding_1s WHERE sym=? ORDER BY ts DESC LIMIT 1", (symbol,))
        F.append(Q.stamp("funding", dict(f) if f else None, f["ts"] if f else None, "ws:markPrice@1s", symbol, now))
        o = _one(db, "SELECT * FROM oi_1m WHERE sym=? ORDER BY ts DESC LIMIT 1", (symbol,))
        F.append(Q.stamp("open_interest", {"oi": o["oi"]} if o else None, o["rx_ts"] if o else None,
                         "rest:openInterest (60 s poll)", symbol, now))
        b = _one(db, "SELECT * FROM book_1s WHERE sym=? ORDER BY ts DESC LIMIT 1", (symbol,))
        bd = dict(b) if b else None
        F.append(Q.stamp("order_book", {k: bd[k] for k in ("imb5", "imb10", "imb20", "bid10", "ask10", "microprice",
                                                           "bid_wall_px", "ask_wall_px", "removed_bid10", "removed_ask10",
                                                           "impact_buy_bps", "impact_sell_bps")} if bd else None,
                         bd["rx_ts"] if bd else None, "ws:depth20@500ms", symbol, now))
        F.append(Q.stamp("spread", {"spread_bps": bd["spread_bps"]} if bd else None, bd["rx_ts"] if bd else None,
                         "ws:depth20@500ms", symbol, now))
        rows = db.execute("SELECT ts, buy_qty, sell_qty, large_buy_qty, large_sell_qty FROM trades_1s "
                          "WHERE sym=? AND ts>=? ORDER BY ts", (symbol, now - 60_000)).fetchall()
        if rows:
            buy = sum(r[1] for r in rows)
            sell = sum(r[2] for r in rows)
            F.append(Q.stamp("cvd", {"delta_60s": buy - sell, "buy_60s": buy, "sell_60s": sell,
                                     "large_delta_60s": sum(r[3] for r in rows) - sum(r[4] for r in rows),
                                     "seconds_with_trades": len(rows)}, rows[-1][0] + 1000, "ws:aggTrade", symbol, now))
        else:
            F.append(Q.stamp("cvd", None, None, "ws:aggTrade", symbol, now))
        lq = db.execute("SELECT side, SUM(notional), COUNT(*) FROM liquidations WHERE sym=? AND ev_ts>=? GROUP BY side",
                        (symbol, now - 300_000)).fetchall()
        alive = _one(db, "SELECT MAX(ts) AS ts FROM book_1s WHERE sym=?", (symbol,))
        rec_ts = alive["ts"] if alive and alive["ts"] else None
        val = {r[0]: {"notional_5m": r[1], "events": r[2]} for r in lq}
        fl = Q.stamp("liquidations", val, rec_ts, "ws:forceOrder", symbol, now,
                     quality=Q.DEGRADED, note="lower bound: Binance pushes at most 1 liquidation per symbol per second")
        fl.name = "liquidations"
        F.append(fl)
    status = Q.snapshot_status(F)
    return {"symbol": symbol, "ts": now, **status, "fields": {f.name: f.to_dict() for f in F}}


def collector_status(db: Optional[sqlite3.Connection], now_ms: Optional[int] = None) -> dict[str, Any]:
    if db is None:
        return {"running": False, "note": "collector not running"}
    now = now_ms or int(time.time() * 1000)
    out: dict[str, Any] = {"tables": {}}
    for t, col in (("book_1s", "ts"), ("trades_1s", "ts"), ("funding_1s", "ts"), ("oi_1m", "ts"), ("liquidations", "ev_ts")):
        r = db.execute(f"SELECT COUNT(*), MIN({col}), MAX({col}) FROM {t}").fetchone()
        out["tables"][t] = {"rows": r[0], "first_ts": r[1], "last_ts": r[2],
                            "age_s": (now - r[2]) / 1000 if r[2] else None}
    ev = db.execute("SELECT ts, sym, kind, detail FROM quality_events ORDER BY ts DESC LIMIT 50").fetchall()
    out["quality_events"] = [{"ts": e[0], "sym": e[1], "kind": e[2], "detail": e[3]} for e in ev]
    k = db.execute("SELECT kind, COUNT(*) FROM quality_events WHERE ts>=? GROUP BY kind", (now - 86_400_000,)).fetchall()
    out["quality_24h"] = {a: b for a, b in k}
    last_conn = db.execute("SELECT ts, kind FROM quality_events WHERE kind IN ('ws_connected','ws_disconnect') "
                           "ORDER BY ts DESC LIMIT 1").fetchone()
    book_age = out["tables"]["book_1s"]["age_s"]
    out["websocket"] = {"state": "connected" if (last_conn and last_conn[1] == "ws_connected" and book_age is not None
                                                  and book_age < 10) else "disconnected",
                        "last_event": {"ts": last_conn[0], "kind": last_conn[1]} if last_conn else None}
    out["running"] = book_age is not None and book_age < 10
    return out
