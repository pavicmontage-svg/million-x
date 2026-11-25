"""Configurable notifications: in-app (stored + pushed to the dashboard / Android notification),
optional Telegram bot and generic webhook. Per-event enable, severity threshold, de-duplication."""
from __future__ import annotations

import asyncio
import time
from typing import Callable

import httpx

from ..config import NotificationConfig
from ..core.logging import get_logger
from ..storage.db import Database

log = get_logger("notify")
SEV = {"info": 0, "warning": 1, "critical": 2}


class Notifier:
    def __init__(self, cfg: NotificationConfig, db: Database | None = None, secret_getter: Callable[[str], str | None] | None = None) -> None:
        self.cfg = cfg
        self.db = db
        self.secret = secret_getter or (lambda name: None)
        self._last: dict[str, float] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self.sent: list[dict] = []
        self._tasks: set[asyncio.Task] = set()

    def notify(self, kind: str, severity: str, message: str) -> dict | None:
        cfg = self.cfg
        always = severity == "critical" and kind in ("emergency_stop", "reconciliation_failure", "unexpected_position")
        if not always:
            if not cfg.enabled_events.get(kind, True):
                return None
            if SEV.get(severity, 0) < SEV.get(cfg.min_severity, 0):
                return None
        key = f"{kind}:{message}"
        now = time.time()
        if now - self._last.get(key, 0.0) < cfg.dedup_window_s:
            return None
        self._last[key] = now
        row = {"ts": int(now * 1000), "kind": kind, "severity": severity, "message": message}
        if self.db is not None:
            row["id"] = self.db.add_alert(kind, severity, message)
        self.sent.append(row)
        self.sent = self.sent[-500:]
        for cb in list(self.listeners):
            try:
                cb(row)
            except Exception:  # pragma: no cover
                log.exception("listener failed")
        self._dispatch_external(row)
        return row

    def _dispatch_external(self, row: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self.cfg.telegram_enabled or self.cfg.webhook_enabled:
            t = loop.create_task(self._send_external(row))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _send_external(self, row: dict) -> None:
        text = f"[QUANT TITAN] {row['severity'].upper()} {row['kind']}: {row['message']}"
        async with httpx.AsyncClient(timeout=8) as c:
            if self.cfg.telegram_enabled and self.cfg.telegram_chat_id:
                token = self.secret("telegram_bot_token")
                if token:
                    try:
                        await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                     json={"chat_id": self.cfg.telegram_chat_id, "text": text})
                    except httpx.HTTPError as e:
                        log.warning("telegram notification failed: %s", type(e).__name__)
            if self.cfg.webhook_enabled and self.cfg.webhook_url.startswith("https://"):
                try:
                    await c.post(self.cfg.webhook_url, json=row)
                except httpx.HTTPError as e:
                    log.warning("webhook notification failed: %s", type(e).__name__)
