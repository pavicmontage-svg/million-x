"""Encrypted secret storage (API keys, notification tokens).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The master key comes from the
``QT_MASTER_KEY`` environment variable or a key file created once with 0600 permissions (on Android:
app-private storage that other apps cannot read). Plaintext secrets are never written to disk,
never returned by the API (only masked previews) and are registered with the log redactor.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from ..core.logging import mask, register_secret
from ..storage.db import Database, now_ms


def load_master_key(path: str = "data/master.key") -> bytes:
    env = os.environ.get("QT_MASTER_KEY")
    if env:
        return env.encode()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(Fernet.generate_key())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    with open(path, "rb") as fh:
        return fh.read().strip()


class SecretStore:
    def __init__(self, db: Database, master_key: bytes) -> None:
        self.db = db
        self.f = Fernet(master_key)

    def set(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("empty secret")
        register_secret(value)
        self.db.upsert("secrets", "name", {"name": name, "ciphertext": self.f.encrypt(value.encode()).decode(),
                                           "updated_ms": now_ms()})

    def get(self, name: str) -> str | None:
        r = self.db.query("SELECT ciphertext FROM secrets WHERE name = ?", (name,))
        if not r:
            return None
        try:
            v = self.f.decrypt(r[0]["ciphertext"].encode()).decode()
        except InvalidToken:
            raise RuntimeError(f"secret {name!r} cannot be decrypted with the current master key")
        register_secret(v)
        return v

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM secrets WHERE name = ?", (name,))

    def preview(self, name: str) -> dict:
        v = self.get(name)
        r = self.db.query("SELECT updated_ms FROM secrets WHERE name = ?", (name,))
        return {"name": name, "set": v is not None, "preview": mask(v) if v else "", "updated_ms": r[0]["updated_ms"] if r else None}
