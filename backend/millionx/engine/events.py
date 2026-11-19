"""Thread-safe event bus: the trading thread publishes, the API's WebSocket
handlers (asyncio) consume."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 300):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self.recent: deque = deque(maxlen=history)

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, x) for l, x in self._subs if x is not q]

    def publish(self, kind: str, data: Any) -> None:
        msg = {"type": kind, "ts": int(time.time() * 1000), "data": data}
        self.recent.append(msg)
        payload = json.dumps(msg, default=str)
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put, q, payload)
            except RuntimeError:
                pass


def _put(q: asyncio.Queue, payload: str) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(payload)
