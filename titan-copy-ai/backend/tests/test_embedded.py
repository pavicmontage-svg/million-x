"""On-phone (embedded) mode: source-account tracking, scheduler, and the full Android entry point.
Positions/prices below are TEST FIXTURES."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import select

from titan.copy.sources import SL_GRACE_S, PositionSource, SourceTracker
from titan.models import Market, Signal, SignalKind, Trader, TraderStatus


class FakeSource(PositionSource):
    forex = True

    def __init__(self):
        self.positions = {}

    def fetch(self):
        return {k: dict(v) for k, v in self.positions.items()}


@pytest.fixture()
def tracker(db):
    with db.session_scope() as s:
        t = Trader(platform="mt5", name="My MT5 source", market=Market.FOREX, status=TraderStatus.APPROVED)
        s.add(t)
        s.flush()
        tid = t.id
    clock = {"t": 1_800_000_000.0}
    src = FakeSource()
    return src, SourceTracker(tid, src, now=lambda: clock["t"]), clock, db


def kinds(db):
    with db.session_scope() as s:
        return [(g.kind.value, g.source_position_id, g.sl, g.close_fraction)
                for g in s.scalars(select(Signal).order_by(Signal.id))]


def test_source_tracker_lifecycle(tracker):
    src, tr, clock, db = tracker
    src.positions = {"old": {"symbol": "EURUSD.r", "side": "buy", "qty": 0.1, "entry": 1.08, "sl": 1.07, "tp": None}}
    assert tr.poll() == []                        # baseline: pre-existing positions are NOT copied
    src.positions["t1"] = {"symbol": "GBPUSD", "side": "sell", "qty": 0.2, "entry": 1.27, "sl": None, "tp": None}
    assert tr.poll() == []                        # no SL yet -> wait (grace period)
    clock["t"] += 2
    src.positions["t1"]["sl"] = 1.275
    assert len(tr.poll()) == 1                    # SL attached within grace -> OPEN with SL
    src.positions["t1"]["qty"] = 0.1
    tr.poll()                                     # half closed
    src.positions["t1"]["sl"] = 1.27              # moved to break-even
    tr.poll()
    src.positions["t1"]["qty"] = 0.3              # added: ignored (no pyramiding)
    assert tr.poll() == []
    del src.positions["t1"]
    tr.poll()
    got = kinds(db)
    assert [k for k, *_ in got] == ["open", "partial_close", "modify", "close"]
    assert got[0][2] == 1.275 and got[1][3] == pytest.approx(0.5) and got[2][2] == 1.27
    with db.session_scope() as s:
        assert s.scalar(select(Signal).where(Signal.kind == SignalKind.OPEN)).symbol == "GBPUSD"


def test_open_without_stop_is_emitted_after_grace_and_rejected_by_risk(tracker):
    src, tr, clock, db = tracker
    tr.poll()
    src.positions["n"] = {"symbol": "EURUSD", "side": "buy", "qty": 0.1, "entry": 1.08, "sl": None, "tp": None}
    tr.poll()
    clock["t"] += SL_GRACE_S + 1
    tr.poll()
    assert kinds(db) == [("open", "n", None, None)]   # copy engine rejects it: "missing stop-loss"


def test_tracker_state_survives_restart(tracker):
    src, tr, clock, db = tracker
    tr.poll()
    src.positions["a"] = {"symbol": "USDJPY", "side": "buy", "qty": 0.1, "entry": 150.0, "sl": 149.5, "tp": None}
    tr.poll()
    tr2 = SourceTracker(tr.trader_id, src, now=lambda: clock["t"])   # "app restarted"
    assert tr2.poll() == []                                          # no duplicate open
    assert len(kinds(db)) == 1


def test_scheduler_runs_due_jobs_once(db, monkeypatch):
    import threading

    from titan import scheduler
    calls = []
    monkeypatch.setattr(scheduler, "JOBS", [("a", 3600, lambda: calls.append("a")), ("b", 3600, lambda: 1 / 0)])
    sch = scheduler.Scheduler(threading.Event())
    assert sch.run_once() == ["a"]          # failing job is logged and retried next interval
    assert sch.run_once() == [] and calls == ["a"]


ENTRY = textwrap.dedent('''
    import json, sys, time, httpx
    from titan import android_entry as e
    base, key = sys.argv[1], sys.argv[2]
    port = e.start_background(base, key)
    assert e.start_background(base, key) == port          # idempotent
    tok = e.local_token("pytest phone")
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    for _ in range(100):
        try:
            h = c.get("/api/health").json()
            if h.get("engine") == "ok":
                break
        except Exception:
            pass
        time.sleep(0.2)
    d = c.get("/api/dashboard").json()
    out = {"health": h, "portfolios": [p["name"] for p in d["portfolios"]], "engine": d["copy_engine"].get("running"),
           "unauth": httpx.get(f"http://127.0.0.1:{port}/api/dashboard").status_code,
           "kill": c.post("/api/emergency/stop", json={"reason": "test"}).status_code, "running": e.is_running()}
    e.stop()
    print("RESULT" + json.dumps(out))
''')


def test_android_entry_starts_full_stack(tmp_path):
    """Starts exactly what the Android service starts (in a fresh process) and talks to it over 127.0.0.1."""
    from titan.security import generate_master_key
    env = {k: v for k, v in os.environ.items() if not k.startswith("TITAN_")}
    env["PYTHONTZPATH"] = "/nonexistent"   # like Android: no system time-zone database (needs the tzdata package)
    backend = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", ENTRY, str(tmp_path), generate_master_key()], cwd=backend, env=env,
                       capture_output=True, text=True, timeout=180)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stdout[-2000:] + r.stderr[-4000:]
    out = json.loads(line[6:])
    assert out["health"]["database"] == "ok" and out["health"]["engine"] == "ok" and out["health"]["embedded"] is True
    assert out["portfolios"] == ["Forex Paper", "Crypto Paper"] and out["engine"] is True
    assert out["unauth"] == 401 and out["kill"] == 200 and out["running"] is True
    assert (tmp_path / "titan" / "titan.db").exists() and (tmp_path / "titan" / "engine.log").exists()
