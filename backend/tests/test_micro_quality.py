"""2.1 Data Quality Engine: missing, stale, drift, gaps, duplicates, out-of-order, invalid, INCOMPLETE."""
import numpy as np

from millionx.micro import quality as Q


def test_stamp_fresh_stale_missing_and_future():
    now = 10_000_000
    f = Q.stamp("order_book", {"imb": 0.1}, now - 1_000, "ws:depth20", "BTCUSDT", now)
    assert f.quality == Q.OK and f.fresh and not f.missing
    s = Q.stamp("order_book", {"imb": 0.1}, now - 10_000, "ws:depth20", "BTCUSDT", now)
    assert s.quality == Q.STALE and s.value is None and s.missing, "stale data is never used as current"
    m = Q.stamp("funding", None, None, "ws:markPrice", "BTCUSDT", now)
    assert m.quality == Q.MISSING and m.missing
    fut = Q.stamp("cvd", 1.0, now + 60_000, "archive", "BTCUSDT", now)
    assert fut.quality == Q.INVALID and fut.value is None


def test_snapshot_incomplete_without_critical():
    now = 1_000_000
    fields = [Q.stamp("ohlcv", 1, now, "k", "X", now), Q.stamp("cvd", 1, now, "k", "X", now),
              Q.stamp("order_book", None, None, "ws", "X", now)]
    st = Q.snapshot_status(fields)
    assert st["status"] == Q.INCOMPLETE and st["missing_critical"] == ["order_book"]


def test_series_checks():
    step = 60_000
    t = list(range(0, 100 * step, step))
    t = t[:40] + t[45:]            # gap of 5 points
    t.insert(10, t[9])             # duplicate
    t[20], t[21] = t[21], t[20]    # out of order
    v = np.full(len(t), 100.0)
    v[30] = -1                     # invalid
    rep = Q.check_series("ohlcv", "BTCUSDT", t, v, step, 0, 100 * step)
    assert rep.gaps == 1 and rep.missing_points == 5
    assert rep.duplicates == 1 and rep.out_of_order >= 1 and rep.invalid_values == 1
    assert rep.status == Q.DEGRADED and 0.9 < rep.coverage < 0.96


def test_abnormal_jump_and_mapping():
    rng = np.random.default_rng(0)
    p = 100 * np.exp(np.cumsum(rng.normal(0, 1e-3, 2000)))
    p[1000:] *= 3.0
    rep = Q.check_series("ohlcv", "X", np.arange(2000) * 60_000, p, 60_000)
    assert rep.abnormal_values >= 1
    assert Q.check_symbol_mapping("BTCUSDT", ["BTCUSDT", "ETHUSDT"]) == ["rows for ETHUSDT found in BTCUSDT file"]


def test_analysis_status():
    ok = Q.check_series("funding", "X", np.arange(100) * 1000, np.ones(100), 1000)
    st = Q.analysis_status(["funding", "liquidations"], {"funding": ok})
    assert st["status"] == Q.INCOMPLETE and st["missing"] == ["liquidations"]
    assert Q.analysis_status(["funding"], {"funding": ok})["status"] == Q.COMPLETE
