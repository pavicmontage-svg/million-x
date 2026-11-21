"""Continuous Opportunity Scanner + virtual PaperBook (2.2, §1, §4–§7).

Runs every second as a listener of the 2.1 collector (WebSocket /public + /market). For every instrument
it keeps 4 h of 1 s bars, computes the SAME features as the backtest, runs engines A/B (chosen configs)
and C (live order book), ranks each candidate with the Opportunity Engine and records it with the full
snapshot of what the scanner saw, the decision and the reasons — whether or not positions are open.

Execution: ONLY a virtual PaperBook (fills at the next second's price plus the BASE cost model).
Nothing here can send an exchange order. ACCEPT requires a strategy validated by the research verdict;
the Risk Engine limits (0.5 % risk, 2 positions, 10 EUR/day, ISOLATED leverage cap) are enforced as in
the trading engine. Every detected opportunity is also followed afterwards ("shadow outcome") so that
missed opportunities and live calibration can be measured without trading.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Optional

import numpy as np

from . import engines as E
from . import exits as X
from . import features as F
from . import opportunity as O

BUFFER_S = 14_400
SCHEMA = """
CREATE TABLE IF NOT EXISTS scalp_opps (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, sym TEXT, engine TEXT, cfg INTEGER,
  side INTEGER, price REAL, decision TEXT, reasons TEXT, eval TEXT, snapshot TEXT, decide_ms REAL,
  outcome_r REAL, outcome_reason TEXT, outcome_hold INTEGER);
CREATE INDEX IF NOT EXISTS ix_scalp_opps_ts ON scalp_opps(ts);
CREATE TABLE IF NOT EXISTS scalp_paper (id INTEGER PRIMARY KEY AUTOINCREMENT, opp_id INTEGER, sym TEXT, engine TEXT,
  side INTEGER, opened_ts INTEGER, entry REAL, stop_bps REAL, rr REAL, qty REAL, notional REAL, margin REAL, lev REAL,
  closed_ts INTEGER, exit REAL, gross_bps REAL, net_bps REAL, r REAL, pnl_eur REAL, fees_eur REAL, reason TEXT);
"""


class _Buf:
    def __init__(self):
        self.t, self.o, self.h, self.l, self.c, self.buy, self.sell, self.n = (deque(maxlen=BUFFER_S) for _ in range(8))

    def add(self, ts: int, b: dict[str, Any]) -> None:
        for k, v in (("t", ts), ("o", b["o"]), ("h", b["h"]), ("l", b["l"]), ("c", b["c"]), ("buy", b["buy"]),
                     ("sell", b["sell"]), ("n", b["n"])):
            getattr(self, k).append(v)

    def sec(self) -> F.Sec:
        return F.Sec(*(np.asarray(getattr(self, k), dtype=float if k != "t" else np.int64)
                       for k in ("t", "o", "h", "l", "c", "buy", "sell", "n")))


class Scanner:
    def __init__(self, db: sqlite3.Connection, lock, config: Optional[dict[str, Any]] = None,
                 capital_eur: float = 500.0, risk_frac: float = 0.005, max_positions: int = 2, daily_loss_eur: float = 10.0,
                 max_leverage: float = 20.0):
        self.db, self.lock = db, lock
        with lock:
            db.executescript(SCHEMA)
        cfg = config or {}
        self.chosen = cfg.get("chosen") or {"A": "A0@1s", "B": "B0@1s"}
        self.validated = {k for k, v in (cfg.get("verdict") or {}).items() if v == "CANDIDATE_FOR_PAPER"}
        self.calib = cfg.get("calibration") or {"edges": {}, "cells": {}}
        self.cost = X.SCENARIOS["BASE"]
        self.buf: dict[str, _Buf] = {}
        self.cap, self.risk_eur = capital_eur, capital_eur * risk_frac
        self.max_pos, self.daily_loss, self.max_lev = max_positions, daily_loss_eur, max_leverage
        self.open: list[dict[str, Any]] = []
        self.pending_shadow: list[dict[str, Any]] = []
        self.stats = {"seconds": 0, "detected": 0, "rejected": 0, "accepted": 0, "executed": 0, "incomplete": 0,
                      "decide_ms_avg": 0.0, "data_latency_ms": {}}
        self.last: dict[str, dict[str, Any]] = {}

    # ----------------------------------------------------------------- per second
    def on_second(self, ts: int, second: dict[str, dict]) -> None:
        t0 = time.perf_counter()
        self.stats["seconds"] += 1
        for sym, b in second.items():
            buf = self.buf.setdefault(sym, _Buf())
            buf.add(ts, b)
            if b.get("lat_ms") is not None:
                self.stats["data_latency_ms"][sym] = b["lat_ms"]
            self._manage(sym, ts, b)
            if len(buf.t) >= 900:
                self._scan(sym, ts, buf, b.get("book"))
        self._shadow(ts)
        dt = (time.perf_counter() - t0) * 1000
        self.stats["decide_ms_avg"] = 0.95 * self.stats["decide_ms_avg"] + 0.05 * dt

    def _scan(self, sym: str, ts: int, buf: _Buf, book: Optional[dict]) -> None:
        s = buf.sec()
        f = F.compute(s)
        i = len(s) - 1
        snap = {k: (float(f[k][i]) if np.isfinite(f[k][i]) else None) for k in
                ("v10", "v30z", "acc10", "relvol30", "taker30", "cvd10", "cvd_acc", "break_up", "break_dn", "ext_up",
                 "ext_dn", "trend5", "mom3m", "ret15m", "sigma300", "ema9_1m", "ema21_1m", "vwap4h")}
        snap["regime"] = str(f["regime"][i])
        snap["price"] = float(s.c[i])
        snap["book"] = {k: book.get(k) for k in ("spread_bps", "imb10", "removed_bid10", "removed_ask10", "age_ms")} if book else None
        self.last[sym] = {"ts": ts, "snapshot": snap}
        for eng, fn in (("A", E.engine_a), ("B", E.engine_b)):
            key = self.chosen.get(eng)
            if not key:
                continue
            cfg = int(key[1])
            tf = int(key.split("@")[1][:-1])
            if tf > 1 and (ts // 1000 + 1) % tf != 0:
                continue
            for j, side, stop, score in fn(f, s.c, E.GRID[eng][cfg]):
                if j == i:
                    self._decide(sym, ts, eng, cfg, side, stop, E.GRID[eng][cfg]["rr"], score, snap, book_ok=True)
        # Engine C: real order book only
        n10 = min(10, len(buf.c))
        flow = {"buy": float(sum(list(buf.buy)[-n10:])), "sell": float(sum(list(buf.sell)[-n10:])),
                "move_bps": float((s.c[i] / s.c[i - n10 + 1] - 1) * 1e4) if n10 > 1 else 0.0}
        c_res = E.engine_c(book, flow)
        if c_res["status"] == "INCOMPLETE":
            self.stats["incomplete"] += 1
        elif c_res["side"]:
            stop = max(5.0, 1.5 * float(snap["sigma300"] or 1.0) * np.sqrt(60))
            self._decide(sym, ts, "C", 0, c_res["side"], stop, 3.0, abs(book.get("imb10") or 0), snap, True,
                         extra=c_res["reasons"])

    def _decide(self, sym, ts, eng, cfg, side, stop, rr, score, snap, book_ok, extra=None) -> None:
        self.stats["detected"] += 1
        validated = f"{eng}" in self.validated or eng in "".join(self.validated)
        ev = O.rank({"engine": eng, "cfg": cfg, "side": side, "score": score, "regime": snap["regime"], "stop_bps": stop,
                     "rr": rr}, self.calib, self.cost, validated=validated, quality_ok=book_ok, risk_eur=self.risk_eur)
        reasons = list(extra or []) + ev["reasons"]
        if ev["decision"] == "ACCEPT":
            risk_reason = self._risk_check(sym, ts, stop)
            if risk_reason:
                ev["decision"], reasons = "REJECT", reasons + [risk_reason]
        self.stats["accepted" if ev["decision"] == "ACCEPT" else "rejected"] += 1
        with self.lock:
            cur = self.db.execute("INSERT INTO scalp_opps(ts,sym,engine,cfg,side,price,decision,reasons,eval,snapshot,decide_ms) "
                                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (ts, sym, eng, cfg, side, snap["price"], ev["decision"], json.dumps(reasons),
                                   json.dumps(ev, default=str), json.dumps(snap, default=str), self.stats["decide_ms_avg"]))
            oid = cur.lastrowid
        self.pending_shadow.append({"id": oid, "sym": sym, "i_ts": ts, "side": side, "stop": stop, "rr": rr})
        if ev["decision"] == "ACCEPT":
            self._open(oid, sym, ts, eng, side, stop, rr, snap["price"])

    # ----------------------------------------------------------------- risk + paper execution
    def _risk_check(self, sym: str, ts: int, stop_bps: float) -> Optional[str]:
        if len(self.open) >= self.max_pos:
            return f"max {self.max_pos} positions open"
        if any(p["sym"] == sym for p in self.open):
            return "position already open on this instrument"
        day = ts // 86_400_000
        with self.lock:
            lost = self.db.execute("SELECT COALESCE(-SUM(pnl_eur),0) FROM scalp_paper WHERE closed_ts/86400000=? AND pnl_eur<0",
                                   (day,)).fetchone()[0]
        if lost >= self.daily_loss:
            return f"daily loss limit {self.daily_loss:.0f} EUR reached"
        stop = stop_bps / 1e4
        lev = self.max_lev
        while lev > 1 and (1 / lev - 0.004) < 3 * stop:  # liquidation >= 3x stop distance (ISOLATED)
            lev -= 1
        notional = self.risk_eur / stop
        margin = notional / lev
        if margin > self.cap - sum(p["margin"] for p in self.open):
            return f"margin {margin:.0f} EUR at {lev:.0f}x does not fit"
        return None

    def _open(self, oid, sym, ts, eng, side, stop, rr, price) -> None:
        stopf = stop / 1e4
        lev = self.max_lev
        while lev > 1 and (1 / lev - 0.004) < 3 * stopf:
            lev -= 1
        notional = self.risk_eur / stopf
        pos = {"opp_id": oid, "sym": sym, "engine": eng, "side": side, "opened_ts": ts + 1000, "entry": None,
               "stop_bps": stop, "rr": rr, "notional": notional, "margin": notional / lev, "lev": lev, "best": 0.0}
        self.open.append(pos)
        self.stats["executed"] += 1

    def _manage(self, sym: str, ts: int, b: dict[str, Any]) -> None:
        for p in [p for p in self.open if p["sym"] == sym]:
            if p["entry"] is None:            # filled at the first price after the decision second (latency >= 1 s)
                p["entry"] = b["c"]
                p["qty"] = p["notional"] / b["c"]
                continue
            e, side, sd = p["entry"], p["side"], p["stop_bps"] / 1e4 * p["entry"]
            rt = self.cost.round_trip() / 1e4 * e
            fav = (b["h"] - e) if side > 0 else (e - b["l"])
            adv = (e - b["l"]) if side > 0 else (b["h"] - e)
            stop_off = -sd
            if p["best"] >= max(X.BE_R * sd, 2 * rt):
                stop_off = max(stop_off, rt)
            trailing = p["best"] >= X.TRAIL_R * sd
            if trailing:
                stop_off = max(stop_off, p["best"] - X.TRAIL_DIST_R * sd)
            held = (ts - p["opened_ts"]) / 1000
            reason, px_off = None, None
            if -adv <= stop_off:
                reason, px_off = ("stop" if stop_off < 0 else "break_even" if not trailing else "trailing"), stop_off
            elif fav >= p["rr"] * sd:
                reason, px_off = "take_profit", p["rr"] * sd
            elif (held >= X.MAX_HOLD_S and not trailing) or held >= X.MAX_HOLD_WINNER_S:
                reason, px_off = "max_hold", (b["c"] - e) * side
            p["best"] = max(p["best"], fav)
            if reason:
                gross = px_off / e * 1e4
                net = gross - self.cost.round_trip(False, held)
                r = net / p["stop_bps"]
                with self.lock:
                    self.db.execute("INSERT INTO scalp_paper(opp_id,sym,engine,side,opened_ts,entry,stop_bps,rr,qty,notional,margin,lev,"
                                    "closed_ts,exit,gross_bps,net_bps,r,pnl_eur,fees_eur,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (p["opp_id"], sym, p["engine"], side, p["opened_ts"], e, p["stop_bps"], p["rr"], p["qty"],
                                     p["notional"], p["margin"], p["lev"], ts, e + px_off * side, gross, net, r,
                                     r * self.risk_eur, (gross - net) / 1e4 * p["notional"], reason))
                self.open.remove(p)

    def _shadow(self, ts: int) -> None:
        """Outcome of every detected opportunity (accepted or not) once its maximum holding window has passed."""
        keep = []
        for o in self.pending_shadow:
            if ts - o["i_ts"] < (X.MAX_HOLD_WINNER_S + 5) * 1000:
                keep.append(o)
                continue
            buf = self.buf.get(o["sym"])
            if not buf:
                continue
            t = np.asarray(buf.t)
            i = int(np.searchsorted(t, o["i_ts"]))
            if i >= len(t) or t[i] != o["i_ts"]:
                continue
            s = buf.sec()
            z = np.zeros(len(s))
            res = X.simulate(i, o["side"], o["stop"], o["rr"], s.c, s.h, s.l, z, z, self.cost)
            if res:
                with self.lock:
                    self.db.execute("UPDATE scalp_opps SET outcome_r=?, outcome_reason=?, outcome_hold=? WHERE id=?",
                                    (res["r"], res["reason"], res["hold_s"], o["id"]))
        self.pending_shadow = keep

    def status(self) -> dict[str, Any]:
        return {"stats": self.stats, "open": [{k: v for k, v in p.items()} for p in self.open],
                "chosen": self.chosen, "validated": sorted(self.validated), "last": self.last}


def load_config() -> dict[str, Any]:
    """Research verdict, chosen configs and discovery calibration packaged with the build (CI output)."""
    import pkgutil
    try:
        raw = pkgutil.get_data("millionx", "data/scalp_config.json")
        return json.loads(raw) if raw else {}
    except (OSError, FileNotFoundError, ValueError):
        return {}
