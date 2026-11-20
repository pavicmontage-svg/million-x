"""Secrets and access control.

* Exchange API keys are encrypted at rest with Fernet (AES-128-CBC + HMAC).
  The master key lives only in the environment (MX_MASTER_KEY), never in the
  database or the Android app.
* Android devices pair with a one-time code and receive a random bearer token;
  only its SHA-256 hash is stored.
* Roles: owner (full administration) and viewer (read-only + emergency stop).
  AI agents are not users: they have no token and cannot call the API.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from .storage import Store

ROLES = ("owner", "viewer")

# action -> roles allowed. Anything not listed is owner-only.
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "read": ("owner", "viewer"),
    "emergency_stop": ("owner", "viewer"),
    "approve_trade": ("owner",),
    "change_settings": ("owner",),
    "change_risk_limits": ("owner",),
    "manage_keys": ("owner",),
    "edit_prompts": ("owner",),
    "manage_bots": ("owner",),
    "unlock_live": ("owner",),
    "pair_device": ("owner",),
}


def can(role: str, action: str) -> bool:
    return role in PERMISSIONS.get(action, ("owner",))


TOKEN_TTL_MS = 30 * 86_400_000          # device tokens expire after 30 days without use
PAIR_FAILS_PER_IP = 5                    # per 15 minutes
PAIR_FAILS_GLOBAL = 30                   # per hour, all sources


def _master_key_from_env() -> Optional[str]:
    key = os.environ.get("MX_MASTER_KEY")
    path = os.environ.get("MX_MASTER_KEY_FILE")  # e.g. a Docker secret mounted at /run/secrets/...
    if not key and path and os.path.exists(path):
        st = os.stat(path)
        if st.st_mode & 0o077:
            raise RuntimeError(f"{path} must not be readable by group/others (chmod 600)")
        with open(path) as fh:
            key = fh.read().strip()
    return key


class Vault:
    def __init__(self, master_key: Optional[str] = None):
        key = master_key or _master_key_from_env()
        if not key:
            raise RuntimeError("MX_MASTER_KEY is not set. Generate one with: python -m millionx.cli genkey")
        self.f = Fernet(key.encode() if isinstance(key, str) else key)

    @staticmethod
    def generate() -> str:
        return Fernet.generate_key().decode()

    def enc(self, s: str) -> bytes:
        return self.f.encrypt(s.encode())

    def dec(self, b: bytes) -> str:
        try:
            return self.f.decrypt(b).decode()
        except InvalidToken as e:
            raise RuntimeError("cannot decrypt API keys: wrong MX_MASTER_KEY") from e


def store_api_keys(store: Store, vault: Vault, exchange: str, mode: str, key: str, secret: str) -> None:
    store.x("DELETE FROM api_keys WHERE exchange=? AND mode=?", (exchange, mode))
    store.x("INSERT INTO api_keys(exchange,mode,key_enc,secret_enc,created_at,permissions) VALUES(?,?,?,?,?,?)",
            (exchange, mode, vault.enc(key), vault.enc(secret), int(time.time() * 1000), "{}"))


def load_api_keys(store: Store, vault: Vault, exchange: str, mode: str) -> Optional[tuple[str, str]]:
    r = store.one("SELECT key_enc, secret_enc FROM api_keys WHERE exchange=? AND mode=?", (exchange, mode))
    if not r:
        return None
    return vault.dec(r["key_enc"]), vault.dec(r["secret_enc"])


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def create_pairing_code(store: Store, role: str = "owner", ttl_s: int = 600) -> str:
    if role not in ROLES:
        raise ValueError("bad role")
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    store.x("INSERT INTO pairing(code_hash,role,expires_at) VALUES(?,?,?)",
            (_h(code), role, int(time.time() * 1000) + ttl_s * 1000))
    store.audit("owner", "pairing_code_created", {"role": role, "ttl_s": ttl_s})
    return code


def pairing_throttled(store: Store, ip: str) -> bool:
    now = int(time.time() * 1000)
    per_ip = store.one("SELECT COUNT(*) n FROM auth_failures WHERE kind='pair' AND ip=? AND ts>?", (ip, now - 900_000))
    total = store.one("SELECT COUNT(*) n FROM auth_failures WHERE kind='pair' AND ts>?", (now - 3_600_000,))
    return per_ip["n"] >= PAIR_FAILS_PER_IP or total["n"] >= PAIR_FAILS_GLOBAL


def redeem_pairing_code(store: Store, code: str, device_name: str, ip: str = "?") -> Optional[dict[str, Any]]:
    if pairing_throttled(store, ip):
        store.audit("unknown", "pairing_throttled", {"ip": ip})
        return None
    r = store.one("SELECT * FROM pairing WHERE code_hash=? AND used=0", (_h(code.strip().upper()),))
    if not r or r["expires_at"] < int(time.time() * 1000):
        store.x("INSERT INTO auth_failures(ts,ip,kind) VALUES(?,?,?)", (int(time.time() * 1000), ip, "pair"))
        store.audit("unknown", "pairing_failed", {"device": device_name, "ip": ip})
        return None
    store.x("UPDATE pairing SET used=1 WHERE code_hash=?", (r["code_hash"],))
    token = secrets.token_urlsafe(32)
    dev_id = secrets.token_hex(8)
    now = int(time.time() * 1000)
    store.x("INSERT INTO devices(id,name,role,token_hash,created_at,last_seen,expires_at) VALUES(?,?,?,?,?,?,?)",
            (dev_id, device_name[:60], r["role"], _h(token), now, now, now + TOKEN_TTL_MS))
    store.audit(f"device:{dev_id}", "device_paired", {"name": device_name, "role": r["role"]})
    return {"token": token, "device_id": dev_id, "role": r["role"]}


def authenticate(store: Store, token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    now = int(time.time() * 1000)
    r = store.one("SELECT id, name, role, expires_at FROM devices WHERE token_hash=? AND revoked=0", (_h(token),))
    if not r:
        return None
    if r["expires_at"] and r["expires_at"] < now:
        store.audit(f"device:{r['id']}", "token_expired", {})
        return None
    # sliding expiry: active devices stay paired; idle ones must re-pair
    store.x("UPDATE devices SET last_seen=?, expires_at=? WHERE id=?", (now, now + TOKEN_TTL_MS, r["id"]))
    return {k: r[k] for k in ("id", "name", "role")}


def bootstrap_owner_code(store: Store, code: Optional[str], ttl_h: int = 72) -> bool:
    """First-run pairing without a terminal: if no owner device exists yet, the
    code from MX_BOOTSTRAP_CODE becomes a single-use owner pairing code."""
    if not code or len(code.strip()) < 12:
        return False
    if store.one("SELECT 1 FROM devices WHERE role='owner' AND revoked=0"):
        return False
    h = _h(code.strip().upper())
    if store.one("SELECT 1 FROM pairing WHERE code_hash=?", (h,)):
        return False
    store.x("INSERT INTO pairing(code_hash,role,expires_at) VALUES(?,?,?)",
            (h, "owner", int(time.time() * 1000) + ttl_h * 3_600_000))
    store.audit("system", "bootstrap_pairing_code_created", {"ttl_h": ttl_h})
    return True
