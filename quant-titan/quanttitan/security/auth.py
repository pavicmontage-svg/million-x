"""Authentication, sessions and rate limiting.

* Passwords: scrypt (n=2^14, r=8, p=1) with a random 16-byte salt; constant-time comparison.
* Sessions: 256-bit random bearer tokens; only their SHA-256 hash is stored; absolute expiry and
  idle timeout. Tokens are sent in the Authorization header (no cookies → no CSRF surface).
* Device token: the Android app generates a random token on first launch (app-private storage) and
  passes it to the embedded backend via ``QT_DEVICE_TOKEN``; the backend listens on 127.0.0.1 only.
* Login rate limiting: 5 failures per 10 minutes per client → 15-minute lockout.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from ..storage.db import Database, now_ms

SESSION_TTL_MS = 12 * 3600_000
IDLE_MS = 2 * 3600_000


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, s, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(s), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


class RateLimiter:
    def __init__(self, max_events: int, window_s: float, lockout_s: float = 0.0) -> None:
        self.max = max_events
        self.window = window_s
        self.lockout = lockout_s
        self.events: dict[str, deque] = defaultdict(deque)
        self.locked_until: dict[str, float] = {}

    def blocked(self, key: str) -> bool:
        return time.time() < self.locked_until.get(key, 0.0)

    def hit(self, key: str) -> bool:
        """Record an event; return True if the key is now over the limit."""
        now = time.time()
        q = self.events[key]
        q.append(now)
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) > self.max:
            if self.lockout:
                self.locked_until[key] = now + self.lockout
            return True
        return False

    def reset(self, key: str) -> None:
        self.events.pop(key, None)
        self.locked_until.pop(key, None)


class Auth:
    def __init__(self, db: Database, device_token: str | None = None) -> None:
        self.db = db
        self.device_token = device_token or os.environ.get("QT_DEVICE_TOKEN") or None
        self.login_limiter = RateLimiter(5, 600, 900)

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def has_user(self) -> bool:
        return bool(self.db.query("SELECT username FROM users LIMIT 1"))

    def create_user(self, username: str, password: str) -> None:
        if self.has_user():
            raise PermissionError("an administrator already exists")
        if not username or len(username) > 64:
            raise ValueError("invalid username")
        self.db.execute("INSERT INTO users (username, pw_hash, created_ms) VALUES (?,?,?)",
                        (username, hash_password(password), now_ms()))

    def change_password(self, username: str, old: str, new: str) -> None:
        r = self.db.query("SELECT pw_hash FROM users WHERE username = ?", (username,))
        if not r or not verify_password(old, r[0]["pw_hash"]):
            raise PermissionError("invalid credentials")
        self.db.execute("UPDATE users SET pw_hash = ? WHERE username = ?", (hash_password(new), username))
        self.db.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def login(self, username: str, password: str, ip: str) -> str:
        if self.login_limiter.blocked(ip):
            raise PermissionError("too many failed attempts; try again later")
        r = self.db.query("SELECT pw_hash FROM users WHERE username = ?", (username,))
        if not r or not verify_password(password, r[0]["pw_hash"]):
            self.login_limiter.hit(ip)
            raise PermissionError("invalid credentials")
        self.login_limiter.reset(ip)
        token = secrets.token_urlsafe(32)
        t = now_ms()
        self.db.execute("INSERT INTO sessions (token_hash, username, created_ms, expires_ms, last_ms, ip) VALUES (?,?,?,?,?,?)",
                        (self._h(token), username, t, t + SESSION_TTL_MS, t, ip))
        return token

    def logout(self, token: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._h(token),))

    def check(self, token: str | None) -> str | None:
        """Return the principal for a bearer token, or None."""
        if not token:
            return None
        if self.device_token and hmac.compare_digest(token, self.device_token):
            return "device"
        t = now_ms()
        r = self.db.query("SELECT username, expires_ms, last_ms FROM sessions WHERE token_hash = ?", (self._h(token),))
        if not r:
            return None
        s = r[0]
        if t > s["expires_ms"] or t - s["last_ms"] > IDLE_MS:
            self.logout(token)
            return None
        self.db.execute("UPDATE sessions SET last_ms = ? WHERE token_hash = ?", (t, self._h(token)))
        return s["username"]
