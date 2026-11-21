"""Credential encryption and device authentication.

* Credentials: AES-256-GCM with a 32-byte master key supplied by the secrets manager
  (TITAN_MASTER_KEY or TITAN_MASTER_KEY_FILE). Each record has a random 96-bit nonce and the
  record identity (venue|environment|label) is bound as associated data, so a ciphertext
  cannot be swapped between records.
* Devices: one-time pairing code -> random 256-bit bearer token; only SHA-256 hashes are stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Device, PairingCode

_VERSION = b"v1"


class SecretsUnavailable(RuntimeError):
    pass


def _key() -> bytes:
    raw = get_settings().resolved_master_key()
    if not raw:
        raise SecretsUnavailable("TITAN_MASTER_KEY is not configured; credentials cannot be stored")
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise SecretsUnavailable("TITAN_MASTER_KEY must decode to exactly 32 bytes")
    return key


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def encrypt_secret(payload: dict, aad: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, json.dumps(payload).encode(), aad.encode())
    return base64.b64encode(_VERSION + nonce + ct).decode()


def decrypt_secret(token: str, aad: str) -> dict:
    blob = base64.b64decode(token)
    if blob[:2] != _VERSION:
        raise ValueError("unknown ciphertext version")
    nonce, ct = blob[2:14], blob[14:]
    return json.loads(AESGCM(_key()).decrypt(nonce, ct, aad.encode()))


def fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --------------------------------------------------------------------------- pairing
def create_pairing_code(s: Session, ttl_minutes: int = 15) -> str:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    s.add(PairingCode(code_hash=_h(code.upper()),
                      expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)))
    return code


def redeem_pairing_code(s: Session, code: str, device_name: str) -> str | None:
    code_hash = _h(code.strip().upper())
    now = datetime.now(timezone.utc)
    pc = s.scalar(select(PairingCode).where(PairingCode.code_hash == code_hash).with_for_update())
    bootstrap = get_settings().bootstrap_code
    ok = pc is not None and not pc.used and _aware(pc.expires_at) > now
    if not ok and bootstrap and hmac.compare_digest(code_hash, _h(bootstrap.strip().upper())):
        # Bootstrap code is only valid while no device has ever been paired.
        ok = s.scalar(select(Device.id).limit(1)) is None
    if not ok:
        return None
    if pc is not None:
        pc.used = True
    token = secrets.token_urlsafe(32)
    s.add(Device(name=device_name[:120], token_hash=_h(token)))
    return token


def authenticate(s: Session, token: str) -> Device | None:
    if not token:
        return None
    dev = s.scalar(select(Device).where(Device.token_hash == _h(token), Device.revoked.is_(False)))
    if dev:
        dev.last_seen_at = datetime.now(timezone.utc)
    return dev


def verify_hmac_signature(secret: str, body: bytes, timestamp: str, signature: str,
                          max_skew_s: float = 30.0) -> bool:
    """Webhook signature: hex(HMAC_SHA256(secret, f"{timestamp}.{body}")), timestamp in ms."""
    try:
        ts = float(timestamp) / 1000.0
    except ValueError:
        return False
    if abs(datetime.now(timezone.utc).timestamp() - ts) > max_skew_s:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
