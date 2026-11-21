"""In-process scheduler used when the backend is embedded in the Android app (no Celery/Redis).

Last-run times are persisted, so a phone restart does not re-run every job at once and a missed
weekly report / backup still runs after the phone was off.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from . import jobs
from .db import session_scope
from .services import state as st

log = logging.getLogger("titan.scheduler")
KEY = "scheduler"

# (name, interval seconds, callable)
JOBS = [
    ("risk_agent", 15 * 60, lambda: jobs.run_agent("risk_management")),
    ("market_agent", 15 * 60, jobs.market_intelligence),
    ("execution_agent", 3600, lambda: jobs.run_agent("execution")),
    ("audit_agent", 6 * 3600, lambda: jobs.run_agent("audit_performance")),
    ("refresh_sources", 6 * 3600, jobs.refresh_sources),
    ("rescore", 6 * 3600, jobs.rescore_all),
    ("backup", 24 * 3600, jobs.backup),
    ("weekly_report", 7 * 24 * 3600, jobs.weekly_report),
]


class Scheduler:
    def __init__(self, stop: threading.Event, tick_s: float = 30.0):
        self.stop, self.tick = stop, tick_s

    def due(self) -> list[tuple[str, int, object]]:
        now = datetime.now(timezone.utc).timestamp()
        with session_scope() as s:
            last = st.get(s, KEY, {})
        return [j for j in JOBS if now - float(last.get(j[0], 0)) >= j[1]]

    def mark(self, name: str):
        with session_scope() as s:
            last = st.get(s, KEY, {})
            last[name] = datetime.now(timezone.utc).timestamp()
            st.put(s, KEY, last)

    def run_once(self) -> list[str]:
        ran = []
        for name, _, fn in self.due():
            if self.stop.is_set():
                break
            try:
                fn()
                ran.append(name)
            except Exception:
                log.exception("job %s failed", name)
            self.mark(name)   # failed jobs retry at the next interval, not in a tight loop
        return ran

    def loop(self):
        self.stop.wait(60)   # let the engine settle after start-up
        while not self.stop.is_set():
            self.run_once()
            self.stop.wait(self.tick)
