"""Shared HTTP helpers: token-bucket rate limiting and retry with exponential backoff."""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

import httpx

log = logging.getLogger("titan.http")


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: int | None = None):
        self.rate = rate_per_s
        self.capacity = burst or max(1, int(rate_per_s))
        self.tokens = float(self.capacity)
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


class TransientError(Exception):
    pass


def with_retries(fn: Callable[[], httpx.Response], attempts: int = 4, base: float = 0.5,
                 retry_status: tuple = (408, 425, 429, 500, 502, 503, 504)) -> httpx.Response:
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = fn()
            if r.status_code not in retry_status:
                return r
            ra = r.headers.get("Retry-After")
            delay = float(ra) if ra and ra.isdigit() else base * 2 ** i
            last = TransientError(f"HTTP {r.status_code}: {r.text[:200]}")
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last, delay = e, base * 2 ** i
        if i < attempts - 1:
            time.sleep(delay + random.uniform(0, base))
    assert last is not None
    raise last
