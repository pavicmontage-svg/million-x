"""2.2 live scanner: continuous decisions with reasons, no execution without validation, risk limits, paper fills."""
import json

import numpy as np

from millionx.micro import collector as C
from millionx.scalp import live as L
from millionx.scalp import opportunity as O


def _feed(sc, n=2400, impulse_at=2100, start=1_735_689_600_000, sym="BTCUSDT"):
    rng = np.random.default_rng(3)
    p = 100_000.0
    for k in range(n):
        step = rng.normal(0, 3) + (40 if impulse_at <= k < impulse_at + 40 else 0)
        o, p = p, p + step
        buy = 5.0 if impulse_at <= k < impulse_at + 40 else rng.exponential(0.5)
        sell = 0.2 if impulse_at <= k < impulse_at + 40 else rng.exponential(0.5)
        sc.on_second(start + k * 1000, {sym: {"o": o, "h": max(o, p) + 1, "l": min(o, p) - 1, "c": p, "buy": buy,
                                              "sell": sell, "n": 10, "book": None, "lat_ms": 120}})


def _cal_good():
    rows = [{"engine": "A", "cfg": 0, "score": 3.0, "regime": r, "stop_bps": 20.0, "gross_bps": 100.0 if i % 3 == 0 else -20.0,
             "hold_s": 200, "mfe_bps": 50} for i in range(300) for r in ("MOMENTUM_EXPANSION",)]
    return O.build_calibration(rows)


def test_scanner_records_and_rejects_when_not_validated(tmp_path):
    db = C.open_db(str(tmp_path / "m.db"))
    sc = L.Scanner(db, __import__("threading").RLock(), {"chosen": {"A": "A0@1s"}, "verdict": {}, "calibration": _cal_good()})
    _feed(sc)
    st = sc.status()
    assert st["stats"]["seconds"] == 2400 and st["stats"]["detected"] >= 1
    rows = db.execute("SELECT decision, reasons, snapshot, engine FROM scalp_opps").fetchall()
    assert rows and all(r[0] == "REJECT" for r in rows), "nothing executes without a validated strategy"
    assert any("not validated" in r[1] for r in rows)
    snap = json.loads(rows[0][2])
    assert "v30z" in snap and "regime" in snap and snap["book"] is None, "what the scanner saw is stored"
    assert st["stats"]["incomplete"] > 0, "engine C without a live book is INCOMPLETE"
    assert db.execute("SELECT COUNT(*) FROM scalp_paper").fetchone()[0] == 0


def test_scanner_paper_executes_validated_and_respects_risk(tmp_path):
    db = C.open_db(str(tmp_path / "m.db"))
    sc = L.Scanner(db, __import__("threading").RLock(),
                   {"chosen": {"A": "A0@1s"}, "verdict": {"A": "CANDIDATE_FOR_PAPER"}, "calibration": _cal_good()},
                   max_positions=1)
    _feed(sc, n=2400 + 3000)
    acc = db.execute("SELECT COUNT(*) FROM scalp_opps WHERE decision='ACCEPT'").fetchone()[0]
    paper = db.execute("SELECT r, reason, lev, margin FROM scalp_paper").fetchall()
    assert acc >= 1 and len(paper) >= 1
    assert all(p[2] <= 20 and p[3] <= 500 for p in paper)
    assert sc.stats["executed"] <= acc
    # shadow outcome filled for decisions older than the max holding window
    assert db.execute("SELECT COUNT(*) FROM scalp_opps WHERE outcome_reason IS NOT NULL").fetchone()[0] >= 1


def test_collector_listener_gets_seconds(tmp_path):
    db = C.open_db(str(tmp_path / "m.db"))
    ag = C.Aggregator(db, now=lambda: 1_000_000)
    got = []
    ag.listeners.append(lambda ts, sec: got.append((ts, sec)))
    ag.on_message(json.dumps({"stream": "btcusdt@aggTrade", "data": {"e": "aggTrade", "E": 999_990, "T": 999_900, "s": "BTCUSDT",
                                                                        "a": 1, "p": "100", "q": "2", "m": False}}))
    ag.flush(999)
    ts, sec = got[0]
    assert sec["BTCUSDT"]["c"] == 100.0 and sec["BTCUSDT"]["buy"] == 2.0 and sec["BTCUSDT"]["lat_ms"] == 100
    ag.flush(1000)  # no trade this second: carries the close, zero volume
    assert got[1][1]["BTCUSDT"]["c"] == 100.0 and got[1][1]["BTCUSDT"]["buy"] == 0.0
