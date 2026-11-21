"""Event bus on Redis Streams (durable, consumer groups) with an in-process fallback for tests.

Streams
  titan:signals   authorised trading signals -> copy engine (at-least-once; the engine is
                  idempotent on Signal.idempotency_key so redelivery is harmless)
  titan:events    domain events (orders, positions, risk, notifications) -> API websocket fan-out
"""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from typing import Any, Callable

log = logging.getLogger("titan.events")

SIGNALS = "titan:signals"
EVENTS = "titan:events"


class EventBus:
    def publish(self, stream: str, event: dict[str, Any]) -> str: ...
    def ensure_group(self, stream: str, group: str) -> None: ...
    def consume(self, stream: str, group: str, consumer: str, count: int = 10,
                block_ms: int = 1000) -> list[tuple[str, dict]]: ...
    def ack(self, stream: str, group: str, msg_id: str) -> None: ...
    def tail(self, stream: str, last_id: str, block_ms: int = 1000) -> list[tuple[str, dict]]: ...


class RedisBus(EventBus):
    def __init__(self, url: str):
        import redis

        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=10,
                                      health_check_interval=30, retry_on_timeout=True)

    def publish(self, stream, event):
        return self.r.xadd(stream, {"data": json.dumps(event, default=str)}, maxlen=100_000, approximate=True)

    def ensure_group(self, stream, group):
        import redis

        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def consume(self, stream, group, consumer, count=10, block_ms=1000):
        out = []
        # First re-deliver our own pending (unacked) messages, e.g. after a crash/restart.
        for sid in ("0", ">"):
            resp = self.r.xreadgroup(group, consumer, {stream: sid}, count=count,
                                     block=None if sid == "0" else block_ms)
            for _, msgs in resp or []:
                out += [(mid, json.loads(f["data"])) for mid, f in msgs if f]
            if out:
                break
        return out

    def ack(self, stream, group, msg_id):
        self.r.xack(stream, group, msg_id)

    def tail(self, stream, last_id, block_ms=1000):
        resp = self.r.xread({stream: last_id}, block=block_ms, count=100)
        return [(mid, json.loads(f["data"])) for _, msgs in resp or [] for mid, f in msgs]

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except Exception:
            return False


class MemoryBus(EventBus):
    """Single-process bus used by tests and `titan run --all-in-one`."""

    def __init__(self):
        self._streams: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self._pending: dict[tuple[str, str], deque] = defaultdict(deque)
        self._cursor: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Condition()
        self._seq = 0
        self.subscribers: list[Callable[[str, dict], None]] = []

    def publish(self, stream, event):
        with self._lock:
            self._seq += 1
            mid = f"{self._seq}-0"
            self._streams[stream].append((mid, json.loads(json.dumps(event, default=str))))
            self._lock.notify_all()
        for cb in list(self.subscribers):
            try:
                cb(stream, event)
            except Exception:  # pragma: no cover
                log.exception("subscriber failed")
        return mid

    def ensure_group(self, stream, group):
        pass

    def consume(self, stream, group, consumer, count=10, block_ms=0):
        with self._lock:
            key = (stream, group)
            if self._pending[key]:
                return list(self._pending[key])[:count]
            items = self._streams[stream][self._cursor[key]:self._cursor[key] + count]
            self._cursor[key] += len(items)
            self._pending[key].extend(items)
            return list(items)

    def ack(self, stream, group, msg_id):
        with self._lock:
            pend = self._pending[(stream, group)]
            for item in list(pend):
                if item[0] == msg_id:
                    pend.remove(item)

    def tail(self, stream, last_id, block_ms=0):
        with self._lock:
            n = int(last_id.split("-")[0]) if last_id not in ("$", "0") else 0
            return [(m, e) for m, e in self._streams[stream] if int(m.split("-")[0]) > n]

    def ping(self) -> bool:
        return True


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        from .config import get_settings

        _bus = RedisBus(get_settings().redis_url)
    return _bus


def set_bus(bus: EventBus) -> None:
    global _bus
    _bus = bus


def emit(event: str, **data) -> None:
    """Best-effort domain event (never raises into trading code paths)."""
    try:
        get_bus().publish(EVENTS, {"event": event, **data})
    except Exception as e:  # pragma: no cover - redis outage must not break trading
        log.warning("event emit failed (%s): %s", event, e)
