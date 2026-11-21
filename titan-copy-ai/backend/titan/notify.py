"""Notification service: persisted feed + realtime event + optional FCM push (HTTP v1).

FCM is only used when TITAN_FCM_PROJECT_ID and TITAN_FCM_SERVICE_ACCOUNT_FILE are configured;
otherwise the Android app receives notifications over its WebSocket / polling and raises
local notifications itself (works while the app's foreground service runs).
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .events import emit
from .models import Device, Notification

log = logging.getLogger("titan.notify")

KINDS = {
    "trader_discovered", "trader_rank_changed", "trader_risk_increased", "position_opened",
    "position_closed", "stop_loss_modified", "execution_failed", "daily_loss_limit",
    "max_drawdown", "api_disconnected", "broker_disconnected", "copy_engine_stopped",
    "weekly_report", "weekly_loss_limit", "emergency_stop", "system",
}


def notify(s: Session, kind: str, title: str, body: str, severity: str = "info", **data) -> Notification:
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    n = Notification(kind=kind, title=title, body=body, severity=severity, data=data)
    s.add(n)
    s.flush()
    emit("notification", id=n.id, kind=kind, title=title, body=body, severity=severity, data=data)
    try:
        n.pushed = _push_fcm(s, title, body, {"kind": kind, "id": str(n.id), "severity": severity})
    except Exception as e:  # push failures never block trading
        log.warning("FCM push failed: %s", e)
    return n


_token_cache: dict = {}


def _access_token() -> str | None:
    st = get_settings()
    if not (st.fcm_project_id and st.fcm_service_account_file and st.fcm_service_account_file.exists()):
        return None
    if _token_cache.get("exp", 0) > time.time() + 60:
        return _token_cache["tok"]
    # OAuth2 JWT bearer flow for Google service accounts (RFC 7523).
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    sa = json.loads(st.fcm_service_account_file.read_text())
    now = int(time.time())

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).decode().rstrip("=")

    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                             "aud": sa["token_uri"], "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = b64(key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()))
    r = httpx.post(sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": f"{header}.{claims}.{sig}"}, timeout=10)
    r.raise_for_status()
    tok = r.json()
    _token_cache.update(tok=tok["access_token"], exp=now + int(tok.get("expires_in", 3600)))
    return tok["access_token"]


def _push_fcm(s: Session, title: str, body: str, data: dict) -> bool:
    tok = _access_token()
    if not tok:
        return False
    url = f"https://fcm.googleapis.com/v1/projects/{get_settings().fcm_project_id}/messages:send"
    sent = False
    for dev in s.scalars(select(Device).where(Device.revoked.is_(False), Device.fcm_token.is_not(None))):
        r = httpx.post(url, headers={"Authorization": f"Bearer {tok}"}, timeout=10, json={
            "message": {"token": dev.fcm_token, "notification": {"title": title, "body": body},
                        "data": data, "android": {"priority": "high"}}})
        sent = sent or r.status_code == 200
    return sent
