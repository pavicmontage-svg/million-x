"""Firebase Cloud Messaging (HTTP v1) push notifications.

Configure with MX_FCM_SERVICE_ACCOUNT=/path/to/service-account.json (from the
Firebase console). Device tokens are registered by the Android app. Without
configuration notifications are still stored and streamed over WebSocket.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .storage import Store

log = logging.getLogger("millionx.notify")


class FCMNotifier:
    def __init__(self, store: Store, sa_path: Optional[str] = None):
        self.store = store
        self.sa = None
        path = sa_path or os.environ.get("MX_FCM_SERVICE_ACCOUNT")
        if path and os.path.exists(path):
            with open(path) as fh:
                self.sa = json.load(fh)
        self._token: Optional[str] = None
        self._exp = 0.0

    @property
    def enabled(self) -> bool:
        return self.sa is not None

    def _access_token(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {"iss": self.sa["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                  "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}
        enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")
        signing_input = enc(header) + b"." + enc(claims)
        key = serialization.load_pem_private_key(self.sa["private_key"].encode(), password=None)
        sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        jwt = signing_input + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")
        r = requests.post("https://oauth2.googleapis.com/token", timeout=15, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt.decode()})
        r.raise_for_status()
        d = r.json()
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._token

    def send(self, level: str, category: str, title: str, body: str) -> None:
        if not self.enabled:
            return
        prefs = self.store.kv_get("notification_prefs", {})
        if prefs.get(category) is False:
            return
        if level == "info" and prefs.get("only_important"):
            return
        tokens = [r["fcm_token"] for r in self.store.q("SELECT fcm_token FROM devices WHERE revoked=0 AND fcm_token IS NOT NULL")]
        if not tokens:
            return
        url = f"https://fcm.googleapis.com/v1/projects/{self.sa['project_id']}/messages:send"
        at = self._access_token()
        for t in tokens:
            msg = {"message": {"token": t, "notification": {"title": title[:100], "body": body[:240]},
                               "data": {"level": level, "category": category},
                               "android": {"priority": "HIGH" if level in ("critical", "warning") else "NORMAL",
                                           "notification": {"channel_id": f"mx_{level}"}}}}
            try:
                requests.post(url, json=msg, timeout=10, headers={"Authorization": f"Bearer {at}"})
            except requests.RequestException as e:
                log.warning("FCM send failed: %s", e)
