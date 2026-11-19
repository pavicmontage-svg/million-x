"""SQLite persistence. Trades and the audit log are append-only: database
triggers reject UPDATE and DELETE on them, and the audit log is hash-chained
so any tampering with history is detectable (GET /api/audit/verify)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY, exchange TEXT, mode TEXT, key_enc BLOB,
    secret_enc BLOB, created_at INTEGER, permissions TEXT);
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name TEXT, role TEXT, token_hash TEXT UNIQUE,
    created_at INTEGER, last_seen INTEGER, fcm_token TEXT, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pairing (code_hash TEXT PRIMARY KEY, role TEXT, expires_at INTEGER, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, exchange_id TEXT, symbol TEXT, side TEXT,
    type TEXT, qty REAL, price REAL, status TEXT, purpose TEXT, position_id TEXT, mode TEXT, raw TEXT,
    created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, symbol TEXT, bot_id TEXT, is_open INTEGER,
    data TEXT, mode TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, bot_id TEXT, symbol TEXT, side TEXT, qty REAL,
    entry_price REAL, exit_price REAL, opened_at INTEGER, closed_at INTEGER, gross_pnl REAL, fees REAL,
    funding REAL, net_pnl REAL, r_multiple REAL, risk_amount REAL, exit_reason TEXT, regime TEXT,
    virtual INTEGER, mode TEXT);
CREATE INDEX IF NOT EXISTS trades_bot ON trades(bot_id, closed_at);
CREATE TRIGGER IF NOT EXISTS trades_no_update BEFORE UPDATE ON trades BEGIN SELECT RAISE(ABORT, 'trades are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trades_no_delete BEFORE DELETE ON trades BEGIN SELECT RAISE(ABORT, 'trades are immutable'); END;
CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, ts INTEGER, bot_id TEXT, symbol TEXT, data TEXT,
    verdict TEXT, approval TEXT DEFAULT 'auto', decided_by TEXT);
CREATE INDEX IF NOT EXISTS signals_ts ON signals(ts);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, actor TEXT, action TEXT,
    evidence TEXT, approval TEXT, result TEXT, prev_hash TEXT, hash TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TABLE IF NOT EXISTS bot_state (bot_id TEXT PRIMARY KEY, status TEXT, reason TEXT, params TEXT,
    weight REAL, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, request TEXT, result TEXT,
    progress TEXT, created_at INTEGER, finished_at INTEGER);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, level TEXT,
    category TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS equity (ts INTEGER, mode TEXT, equity REAL, PRIMARY KEY (ts, mode));
CREATE TABLE IF NOT EXISTS prompt_versions (agent_id TEXT, version INTEGER, prompt TEXT, author TEXT,
    approved_by TEXT, note TEXT, created_at INTEGER, PRIMARY KEY (agent_id, version));
CREATE TABLE IF NOT EXISTS agent_config (agent_id TEXT PRIMARY KEY, enabled INTEGER, model TEXT,
    active_version INTEGER, max_tokens INTEGER, temperature REAL, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, ts INTEGER, agent_id TEXT, kind TEXT, model TEXT,
    prompt_version INTEGER, input TEXT, output TEXT, confidence REAL, input_tokens INTEGER, output_tokens INTEGER,
    cost_usd REAL, latency_ms INTEGER, error TEXT, evidence_ids TEXT);
CREATE INDEX IF NOT EXISTS agent_runs_agent ON agent_runs(agent_id, ts);
CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, ts INTEGER, data TEXT);
"""


class Store:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- generic ---------------------------------------------------------
    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, tuple(args))

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        r = self.q(sql, args)
        return r[0] if r else None

    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def kv_set(self, key: str, value: Any) -> None:
        self.x("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
               "value=excluded.value, updated_at=excluded.updated_at", (key, json.dumps(value), _now()))

    # ---- audit (hash chain) ---------------------------------------------
    def audit(self, actor: str, action: str, evidence: Any = None, approval: str = "n/a", result: Any = None) -> int:
        with self._lock:
            last = self.one("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
            prev = last["hash"] if last else "GENESIS"
            ts = _now()
            ev = json.dumps(evidence, default=str, sort_keys=True)
            res = json.dumps(result, default=str, sort_keys=True)
            h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{ev}|{approval}|{res}".encode()).hexdigest()
            cur = self.x("INSERT INTO audit(ts,actor,action,evidence,approval,result,prev_hash,hash) "
                         "VALUES(?,?,?,?,?,?,?,?)", (ts, actor, action, ev, approval, res, prev, h))
            return int(cur.lastrowid)

    def verify_audit(self) -> dict[str, Any]:
        prev = "GENESIS"
        n = 0
        for r in self.q("SELECT * FROM audit ORDER BY seq"):
            h = hashlib.sha256(f"{prev}|{r['ts']}|{r['actor']}|{r['action']}|{r['evidence']}|"
                               f"{r['approval']}|{r['result']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return {"ok": False, "broken_at_seq": r["seq"], "checked": n}
            prev = h
            n += 1
        return {"ok": True, "checked": n}

    # ---- trades ------------------------------------------------------------
    def insert_trade(self, t: dict[str, Any]) -> None:
        cols = ["id", "bot_id", "symbol", "side", "qty", "entry_price", "exit_price", "opened_at", "closed_at",
                "gross_pnl", "fees", "funding", "net_pnl", "r_multiple", "risk_amount", "exit_reason", "regime",
                "virtual", "mode"]
        self.x(f"INSERT OR IGNORE INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
               [int(t[c]) if c == "virtual" else t[c] for c in cols])

    def upsert_order(self, o: dict[str, Any]) -> None:
        o = dict(o)
        o.setdefault("created_at", _now())
        o["updated_at"] = _now()
        cols = ["client_id", "exchange_id", "symbol", "side", "type", "qty", "price", "status", "purpose",
                "position_id", "mode", "raw", "created_at", "updated_at"]
        o["raw"] = json.dumps(o.get("raw", {}), default=str)
        self.x(f"INSERT INTO orders({','.join(cols)}) VALUES({','.join('?' * len(cols))}) ON CONFLICT(client_id) "
               "DO UPDATE SET exchange_id=excluded.exchange_id, status=excluded.status, raw=excluded.raw, "
               "updated_at=excluded.updated_at", [o.get(c) for c in cols])

    def notify(self, level: str, category: str, title: str, body: str) -> int:
        cur = self.x("INSERT INTO notifications(ts,level,category,title,body) VALUES(?,?,?,?,?)",
                     (_now(), level, category, title, body))
        return int(cur.lastrowid)


def _now() -> int:
    return int(time.time() * 1000)
