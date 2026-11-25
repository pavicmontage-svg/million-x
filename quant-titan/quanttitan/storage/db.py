"""Persistence: SQLite by default (also on Android), PostgreSQL when QT_DATABASE_URL=postgresql://...

The same SQL runs on both; placeholders are written as ``?`` and converted for psycopg2.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ms BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS config_versions (id {AUTO}, ts BIGINT NOT NULL, actor TEXT, note TEXT, config_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, ts BIGINT NOT NULL, symbol TEXT, outcome TEXT, engine TEXT,
    direction TEXT, confidence DOUBLE PRECISION, net_edge_r DOUBLE PRECISION, status TEXT, explanation TEXT, data_json TEXT);
CREATE INDEX IF NOT EXISTS ix_decisions_ts ON decisions (ts);
CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, closed_ts BIGINT NOT NULL, opened_ts BIGINT, symbol TEXT, engine TEXT,
    setup TEXT, direction TEXT, regime TEXT, net_pnl DOUBLE PRECISION, r_multiple DOUBLE PRECISION, mode TEXT, data_json TEXT);
CREATE INDEX IF NOT EXISTS ix_trades_closed ON trades (closed_ts);
CREATE TABLE IF NOT EXISTS order_events (id {AUTO}, ts BIGINT NOT NULL, client_id TEXT, event TEXT, data_json TEXT);
CREATE TABLE IF NOT EXISTS alerts (id {AUTO}, ts BIGINT NOT NULL, kind TEXT, severity TEXT, message TEXT, is_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit (id {AUTO}, ts BIGINT NOT NULL, actor TEXT, action TEXT, detail_json TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS equity (ts BIGINT PRIMARY KEY, equity DOUBLE PRECISION, balance DOUBLE PRECISION,
    unrealized DOUBLE PRECISION, open_risk DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS breaker_events (id {AUTO}, ts BIGINT NOT NULL, event TEXT, name TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, ts BIGINT NOT NULL, kind TEXT, status TEXT, params_json TEXT, result_json TEXT);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, ciphertext TEXT NOT NULL, updated_ms BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT, created_ms BIGINT, expires_ms BIGINT, last_ms BIGINT, ip TEXT);
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, created_ms BIGINT NOT NULL);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, url: str | None = None) -> None:
        url = url or os.environ.get("QT_DATABASE_URL") or "sqlite:///data/quanttitan.db"
        self.url = url
        self._lock = threading.RLock()
        if url.startswith("postgres"):
            import psycopg2  # optional server dependency
            self.kind = "postgres"
            self.conn = psycopg2.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
            self.conn.autocommit = True
            auto = "SERIAL PRIMARY KEY"
        else:
            self.kind = "sqlite"
            path = url.replace("sqlite:///", "")
            if path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            auto = "INTEGER PRIMARY KEY AUTOINCREMENT"
        for stmt in SCHEMA.replace("{AUTO}", auto).split(";"):
            if stmt.strip():
                self.execute(stmt)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(self._sql(sql), params)
            cur.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(self._sql(sql), params)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows

    def upsert(self, table: str, key: str, row: dict) -> None:
        cols = list(row)
        ph = ",".join("?" for _ in cols)
        upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != key)
        self.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) ON CONFLICT ({key}) DO UPDATE SET {upd}",
                     tuple(row[c] for c in cols))

    # ---------------------------------------------------------------- key/value
    def kv_set(self, key: str, value: Any) -> None:
        self.upsert("kv", "key", {"key": key, "value": json.dumps(value, default=str), "updated_ms": now_ms()})

    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.query("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r[0]["value"]) if r else default

    # ---------------------------------------------------------------- domain helpers
    def save_trade(self, t) -> None:
        d = t.to_dict()
        self.upsert("trades", "id", {"id": t.id, "closed_ts": t.closed_ts, "opened_ts": t.opened_ts, "symbol": t.symbol,
                                     "engine": t.engine, "setup": t.setup, "direction": t.direction, "regime": t.regime,
                                     "net_pnl": t.net_pnl, "r_multiple": t.r_multiple, "mode": t.mode,
                                     "data_json": json.dumps(d, default=str)})

    def trades(self, limit: int = 500, mode: str | None = None, since_ms: int = 0) -> list[dict]:
        q = "SELECT data_json FROM trades WHERE closed_ts >= ?" + (" AND mode = ?" if mode else "") + " ORDER BY closed_ts DESC LIMIT ?"
        params = (since_ms, mode, limit) if mode else (since_ms, limit)
        return [json.loads(r["data_json"]) for r in self.query(q, params)]

    def save_decision(self, d) -> None:
        dd = d.to_dict()
        dd.pop("approval", None)  # the signed token is never persisted
        self.upsert("decisions", "id", {"id": d.id, "ts": d.ts, "symbol": d.symbol, "outcome": d.outcome.value,
                                        "engine": d.engine, "direction": d.direction.value, "confidence": d.confidence,
                                        "net_edge_r": d.net_edge_r, "status": d.status, "explanation": d.explanation,
                                        "data_json": json.dumps(dd, default=str)})

    def decisions(self, limit: int = 200, symbol: str | None = None, actionable_only: bool = False) -> list[dict]:
        where, params = [], []
        if symbol:
            where.append("symbol = ?"); params.append(symbol)
        if actionable_only:
            where.append("outcome <> 'WAIT'")
        q = "SELECT data_json FROM decisions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY ts DESC LIMIT ?"
        return [json.loads(r["data_json"]) for r in self.query(q, tuple(params) + (limit,))]

    def add_alert(self, kind: str, severity: str, message: str) -> int:
        self.execute("INSERT INTO alerts (ts, kind, severity, message) VALUES (?,?,?,?)", (now_ms(), kind, severity, message))
        return self.query("SELECT MAX(id) AS id FROM alerts")[0]["id"]

    def alerts(self, limit: int = 200, after_id: int = 0) -> list[dict]:
        return self.query("SELECT * FROM alerts WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit))

    def audit(self, actor: str, action: str, detail: dict | None = None, ip: str = "") -> None:
        self.execute("INSERT INTO audit (ts, actor, action, detail_json, ip) VALUES (?,?,?,?,?)",
                     (now_ms(), actor, action, json.dumps(detail or {}, default=str), ip))

    def audit_log(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def save_equity(self, ts: int, equity: float, balance: float, unrealized: float, open_risk: float) -> None:
        self.upsert("equity", "ts", {"ts": ts, "equity": equity, "balance": balance, "unrealized": unrealized, "open_risk": open_risk})

    def equity_curve(self, since_ms: int = 0, limit: int = 5000) -> list[dict]:
        rows = self.query("SELECT * FROM equity WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ms, limit))
        return rows[::-1]

    def save_config_version(self, cfg_json: dict, actor: str, note: str = "") -> int:
        self.execute("INSERT INTO config_versions (ts, actor, note, config_json) VALUES (?,?,?,?)",
                     (now_ms(), actor, note, json.dumps(cfg_json)))
        return self.query("SELECT MAX(id) AS id FROM config_versions")[0]["id"]

    def config_versions(self, limit: int = 50) -> list[dict]:
        return self.query("SELECT id, ts, actor, note FROM config_versions ORDER BY id DESC LIMIT ?", (limit,))

    def config_version(self, vid: int) -> dict | None:
        r = self.query("SELECT config_json FROM config_versions WHERE id = ?", (vid,))
        return json.loads(r[0]["config_json"]) if r else None

    def save_experiment(self, eid: str, kind: str, status: str, params: dict, result: dict | None) -> None:
        self.upsert("experiments", "id", {"id": eid, "ts": now_ms(), "kind": kind, "status": status,
                                          "params_json": json.dumps(params, default=str),
                                          "result_json": json.dumps(result, default=str) if result is not None else None})

    def experiments(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM experiments ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = json.loads(r.pop("params_json") or "{}")
            r["result"] = json.loads(r.pop("result_json")) if r.get("result_json") else None
        return rows
