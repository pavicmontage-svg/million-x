"""Structured logging with secret redaction.

Every log record passes through :class:`RedactingFilter`, which masks API keys, secrets, signatures,
passwords and bearer tokens, regardless of which module produced the record.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|apikey|secret|signature|password|passwd|token|x-mbx-apikey)(\"?\s*[:=]\s*\"?)([^\s\"&,}]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-_.=]+)"),
]
_registered_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Register a literal secret so it is masked wherever it appears in logs."""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def redact(text: str) -> str:
    for s in _registered_secrets:
        if s in text:
            text = text.replace(s, mask(s))
    text = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}***", text)
    return text


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class MemoryHandler(logging.Handler):
    """Keeps the most recent records for the SYSTEM LOGS dashboard page."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.capacity = capacity
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "ts": int(record.created * 1000),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )
        if len(self.records) > self.capacity:
            del self.records[: len(self.records) - self.capacity]


memory_handler = MemoryHandler()
_configured = False


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return
    root.setLevel(level)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    f = RedactingFilter()
    for handler in (h, memory_handler):
        handler.addFilter(f)
        root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"qt.{name}")
