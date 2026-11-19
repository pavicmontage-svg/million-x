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


# Ordered, append-only list of schema migrations. Never edit a released entry;
# add a new one. Version 1 is the original schema above.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", SCHEMA),
    (2, "execution safety, governance, org messages, device expiry", """
ALTER TABLE orders ADD COLUMN filled_qty REAL DEFAULT 0;
ALTER TABLE orders ADD COLUMN avg_price REAL DEFAULT 0;
ALTER TABLE orders ADD COLUMN reconciled_at INTEGER;
ALTER TABLE devices ADD COLUMN expires_at INTEGER;
CREATE TABLE IF NOT EXISTS pending_approvals (signal_id TEXT PRIMARY KEY, created_at INTEGER, expires_at INTEGER,
    data TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS agent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, meeting_id TEXT,
    sender TEXT, recipient TEXT, kind TEXT, run_id TEXT, content TEXT);
CREATE INDEX IF NOT EXISTS agent_messages_ts ON agent_messages(ts);
CREATE TABLE IF NOT EXISTS auth_failures (ts INTEGER, ip TEXT, kind TEXT);
CREATE INDEX IF NOT EXISTS auth_failures_ip ON auth_failures(ip, ts);
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, severity TEXT, symbol TEXT,
    kind TEXT, detail TEXT, resolved INTEGER DEFAULT 0);
"""),
]


class Store:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # ---- migrations ------------------------------------------------------
    def schema_version(self) -> int:
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                        "name TEXT, applied_at INTEGER)")
        r = self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if r is None:
            # databases created before migrations existed already have v1 tables
            has = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
            return 1 if has else 0
        return int(r)

    def migrate(self) -> list[int]:
        applied = []
        with self._lock:
            cur = self.schema_version()
            if cur >= 1 and not self.db.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
                self.db.execute("INSERT INTO schema_migrations VALUES(1, 'initial schema (pre-existing)', ?)", (_now(),))
            for ver, name, sql in MIGRATIONS:
                if ver <= cur:
                    continue
                self.db.execute("BEGIN")
                try:
                    for stmt in _split_sql(sql):
                        self.db.execute(stmt)
                    self.db.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (ver, name, _now()))
                    self.db.execute("COMMIT")
                except Exception:
                    self.db.execute("ROLLBACK")
                    raise
                applied.append(ver)
        return applied

    def integrity_ok(self) -> bool:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def backup(self, dest_dir: str, keep: int = 14) -> str:
        """Consistent online backup (SQLite backup API) with rotation."""
        os.makedirs(dest_dir, exist_ok=True)
        name = time.strftime("millionx-%Y%m%d-%H%M%S.db", time.gmtime())
        path = os.path.join(dest_dir, name)
        with self._lock:
            dst = sqlite3.connect(path)
            self.db.backup(dst)
            dst.close()
        files = sorted(f for f in os.listdir(dest_dir) if f.startswith("millionx-") and f.endswith(".db"))
        for f in files[:-keep]:
            os.remove(os.path.join(dest_dir, f))
        return path

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

    ORDER_COLS = ["client_id", "exchange_id", "symbol", "side", "type", "qty", "price", "status", "purpose",
                  "position_id", "mode", "raw", "created_at", "updated_at", "filled_qty", "avg_price", "reconciled_at"]

    def claim_order(self, client_id: str, symbol: str, side: str, type_: str, qty: float, purpose: str,
                    mode: str) -> bool:
        """Atomically reserve a client id before sending. False = already used (duplicate)."""
        cur = self.x("INSERT OR IGNORE INTO orders(client_id,symbol,side,type,qty,status,purpose,mode,raw,created_at,"
                     "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (client_id, symbol, side, type_, qty, "CLAIMED", purpose, mode, "{}", _now(), _now()))
        return cur.rowcount == 1

    def upsert_order(self, o: dict[str, Any]) -> None:
        """Insert or update only the provided fields (never blanks known values)."""
        o = {k: v for k, v in o.items() if k in self.ORDER_COLS}
        if "raw" in o:
            o["raw"] = json.dumps(o["raw"] or {}, default=str)
        o["updated_at"] = _now()
        with self._lock:
            if self.one("SELECT 1 FROM orders WHERE client_id=?", (o["client_id"],)):
                keys = [k for k in o if k != "client_id"]
                self.x(f"UPDATE orders SET {', '.join(k + '=?' for k in keys)} WHERE client_id=?",
                       [o[k] for k in keys] + [o["client_id"]])
            else:
                o.setdefault("created_at", _now())
                o.setdefault("raw", "{}")
                keys = list(o)
                self.x(f"INSERT INTO orders({','.join(keys)}) VALUES({','.join('?' * len(keys))})", [o[k] for k in keys])

    def notify(self, level: str, category: str, title: str, body: str) -> int:
        cur = self.x("INSERT INTO notifications(ts,level,category,title,body) VALUES(?,?,?,?,?)",
                     (_now(), level, category, title, body))
        return int(cur.lastrowid)


def _split_sql(sql: str) -> list[str]:
    """Split a migration script into statements, keeping trigger bodies whole."""
    out, buf, depth = [], [], 0
    for line in sql.splitlines():
        if not line.strip():
            continue
        buf.append(line)
        up = line.upper()
        depth += up.count(" BEGIN ") + (1 if up.rstrip().endswith(" BEGIN") else 0)
        if line.rstrip().endswith(";"):
            if "END;" in up and depth:
                depth = 0
            if depth == 0:
                out.append("\n".join(buf))
                buf = []
    if buf:
        out.append("\n".join(buf))
    return [x for x in out if x.strip() and not x.strip().upper().startswith("PRAGMA JOURNAL_MODE")]


def _now() -> int:
    return int(time.time() * 1000)
