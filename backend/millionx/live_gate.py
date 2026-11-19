"""Live trading gate.

Live orders are allowed only while a valid owner authorisation exists:
  1. a passed Binance DEMO preflight not older than 24 h (execution
     mechanics) — this alone never establishes live readiness;
  2. a passed LIVE key check not older than 1 h: Futures permission on,
     withdrawals off, IP restriction on, account readable, equity and free
     margin sufficient, clock in sync;
  3. an explicit owner confirmation.
The authorisation expires after `max_unlock_hours` and is invalidated by any
key rotation or material configuration change (risk limits, capital mode,
execution mode), detected through a configuration fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from .storage import Store

CONFIRM_PHRASE = "I UNDERSTAND THE RISK"
DEMO_MAX_AGE_MS = 24 * 3_600_000
LIVE_CHECK_MAX_AGE_MS = 3_600_000


def _now() -> int:
    return int(time.time() * 1000)


class LiveGate:
    def __init__(self, store: Store, settings):
        self.store = store
        self.s = settings

    # ------------------------------------------------------------ inputs
    def key_fingerprint(self) -> str:
        r = self.store.one("SELECT key_enc, created_at FROM api_keys WHERE exchange='binance' AND mode='live'")
        if not r:
            return "no-live-key"
        # created_at changes on every rotation; ciphertext changes too
        return hashlib.sha256(f"{r['created_at']}|".encode() + bytes(r["key_enc"])).hexdigest()[:16]

    def config_fingerprint(self) -> str:
        material = {"risk": self.s.risk.__dict__, "capital_mode": self.s.capital_mode,
                    "execution": self.s.execution, "key": self.key_fingerprint()}
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:20]

    def record_demo_preflight(self, result: dict[str, Any]) -> None:
        self.store.kv_set("preflight_demo", {**result, "ts": result.get("ts", _now())})

    def live_check(self, ex, min_equity_usdt: float) -> dict[str, Any]:
        """Checks the real LIVE key and account. `ex` is a BinanceFutures(live=True)."""
        checks: dict[str, Any] = {}

        def check(name, fn):
            try:
                ok, detail = fn()
                checks[name] = {"pass": bool(ok), "detail": detail}
            except Exception as e:
                checks[name] = {"pass": False, "detail": str(e)}

        perms: dict[str, Any] = {}

        def p():
            perms.update(ex.api_permissions())
            return perms.get("checked", False), {k: perms.get(k) for k in ("futures", "withdrawals", "ip_restrict")}

        check("permissions_readable", p)
        check("futures_permission", lambda: (perms.get("futures") is True, perms.get("futures")))
        check("withdrawals_disabled", lambda: (perms.get("withdrawals") is False, perms.get("withdrawals")))
        check("ip_restricted", lambda: (perms.get("ip_restrict") is True, perms.get("ip_restrict")))

        bal: dict[str, float] = {}

        def b():
            bal.update(ex.balance())
            return True, bal

        check("account_readable", b)
        check("equity_sufficient", lambda: (bal.get("equity", 0) >= min_equity_usdt,
                                            f"{bal.get('equity')} >= {min_equity_usdt:.2f}"))
        check("free_margin", lambda: (bal.get("available", 0) > 0, bal.get("available")))
        check("clock_sync", lambda: (abs(ex.server_time() - _now()) < 1000, "server time drift < 1 s"))
        check("commission_readable", lambda: (ex.commission("BTCUSDT") is not None, "ok"))
        ok = all(c["pass"] for c in checks.values())
        res = {"ok": ok, "ts": _now(), "checks": checks, "key_fingerprint": self.key_fingerprint()}
        self.store.kv_set("live_check", res)
        self.store.kv_set("api_permissions", perms)
        self.store.audit("operations", "live_key_check", {}, "n/a", res)
        return res

    # ------------------------------------------------------------ unlock
    def unlock(self, actor: str, phrase: str) -> dict[str, Any]:
        now = _now()
        demo = self.store.kv_get("preflight_demo") or {}
        live = self.store.kv_get("live_check") or {}
        problems = []
        if not demo.get("ok"):
            problems.append("demo preflight not passed")
        elif now - int(demo.get("ts", 0)) > DEMO_MAX_AGE_MS:
            problems.append("demo preflight older than 24 h")
        if not live.get("ok"):
            problems.append("live key/account check not passed")
        elif now - int(live.get("ts", 0)) > LIVE_CHECK_MAX_AGE_MS:
            problems.append("live key/account check older than 1 h")
        elif live.get("key_fingerprint") != self.key_fingerprint():
            problems.append("live key changed since the check")
        if phrase != CONFIRM_PHRASE:
            problems.append("confirmation phrase missing")
        if problems:
            self.store.audit(actor, "live_unlock_refused", {"problems": problems})
            return {"ok": False, "problems": problems}
        rec = {"ts": now, "expires": now + int(self.s.risk.max_unlock_hours) * 3_600_000, "actor": actor,
               "fingerprint": self.config_fingerprint()}
        self.store.kv_set("live_unlock", rec)
        self.store.audit(actor, "live_unlocked", rec, "owner")
        return {"ok": True, **rec}

    def invalidate(self, reason: str, actor: str = "system") -> None:
        if self.store.kv_get("live_unlock"):
            self.store.kv_set("live_unlock", None)
            self.store.audit(actor, "live_unlock_invalidated", {"reason": reason})

    def status(self) -> dict[str, Any]:
        rec: Optional[dict[str, Any]] = self.store.kv_get("live_unlock")
        if not rec:
            return {"valid": False, "reason": "not unlocked"}
        if _now() > int(rec["expires"]):
            return {"valid": False, "reason": "authorisation expired", **rec}
        if rec.get("fingerprint") != self.config_fingerprint():
            return {"valid": False, "reason": "key or material configuration changed since unlock", **rec}
        return {"valid": True, **rec}

    def is_valid(self) -> bool:
        return self.status()["valid"]
