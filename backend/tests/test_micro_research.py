"""2.1 microstructure research: causal percentiles, non-overlapping trades, discovery/validation split,
FDR, and no edge found on data without one."""
import pytest
import numpy as np

from millionx.backtest.data import synthetic_bars
from millionx.micro import archive, research as R


def test_bh_fdr_and_trades():
    q = R.bh_fdr([0.01, 0.04, 0.03, 0.5])
    assert q[0] == 0.04 and abs(q[3] - 0.5) < 1e-12 and all(a <= 1 for a in q)
    close = np.array([100, 101, 102, 103, 104, 105, 106.0])
    t = np.arange(7) * 60_000
    st = np.ones(7, bool)
    r, et = R.trades(st, close, t, 2, +1, 0, 0, 10**9)
    assert len(r) == 3 and list(et) == [0, 120_000, 240_000], "non-overlapping: next entry at or after the exit"
    r1, _ = R.trades(st, close, t, 2, -1, 1, 0, 10**9)
    assert r1[0] < 0


def test_pct_is_causal():
    x = np.random.default_rng(0).normal(size=6000)
    full = R._pct(x)
    part = R._pct(x[:4000])
    assert np.allclose(np.nan_to_num(full[:4000], nan=-1), np.nan_to_num(part, nan=-1))


@pytest.mark.skipif(__import__('importlib').util.find_spec('pandas') is None, reason='research-only dependency (pandas) not installed, e.g. Android package set')
def test_end_to_end_on_synthetic_no_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DISC", ("2023-11-14", "2024-01-20"))
    monkeypatch.setattr(R, "VALID", ("2024-01-20", "2024-04-01"))
    monkeypatch.setattr(R, "PCT_WIN", 3 * 1440)
    n = 60 * 24 * 130
    for k, sym in enumerate(("AAAUSDT", "BBBUSDT")):
        b = synthetic_bars(n, seed=20 + k)
        rng = np.random.default_rng(k)
        cols = {"open_time": b.open_time, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "taker_buy_volume": b.taker_buy_volume, "quote_volume": b.quote_volume,
                "premium": rng.normal(0, 1e-4, n), "oi": 1e5 * np.exp(np.cumsum(rng.normal(0, 1e-4, n))),
                "oi_value": np.full(n, 1e9), "depth_m1": rng.uniform(1, 2, n) * 1e7, "depth_p1": rng.uniform(1, 2, n) * 1e7}
        archive.save(cols, str(tmp_path / sym))
    o = R.run(str(tmp_path), log=lambda *_: None)
    assert o["tests"] > 20 and o["status"]["C"]["status"] == "INCOMPLETE"
    assert o["edge_candidates"] == [], "random data must not produce an edge"
    md = R.to_markdown(o)
    assert "INCOMPLETE" in md
