"""2.4 massive search: finds a planted edge with strict multiple-testing control, finds nothing on random data."""
import numpy as np
import pytest

pytest.importorskip("pandas")
from millionx.micro import archive  # noqa: E402
from millionx.mine import search as S  # noqa: E402


def _stem(tmp_path, name, drift_bps_per_min, seed):
    rng = np.random.default_rng(seed)
    t0 = int(np.datetime64("2024-12-01").astype("datetime64[ms]").astype(np.int64))
    n = 60 * 24 * 575
    t = t0 + np.arange(n, dtype=np.int64) * 60_000
    hour = (t // 3_600_000) % 24
    r = rng.normal(0, 8, n) + np.where((hour >= 8) & (hour < 12), drift_bps_per_min, 0.0)
    c = 100 * np.exp(np.cumsum(r) / 1e4)
    vol = rng.exponential(10, n)
    cols = {"open_time": t, "open": c, "high": c * 1.0005, "low": c * 0.9995, "close": c, "volume": vol,
            "taker_buy_volume": vol * rng.uniform(0.3, 0.7, n), "quote_volume": vol * c}
    archive.save(cols, str(tmp_path / name))
    return str(tmp_path / name)


def test_planted_edge_found_and_random_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PCT_WIN", 3 * 1440)
    for k in range(6):
        S.run_symbol(_stem(tmp_path, f"E{k}USDT", 1.5, k), str(tmp_path / f"mine_E{k}.npz"), log=lambda *_: None, max_rules=400)
    o = S.aggregate(str(tmp_path / "mine_E*.npz"), log=lambda *_: None)
    edges = o["scenarios"]["TAKER"]["edges"]
    assert edges and any(any(x.startswith("utc08") for x in e["rule"]) and e["direction"] == "LONG" for e in edges)
    for k in range(6):
        S.run_symbol(_stem(tmp_path, f"R{k}USDT", 0.0, 100 + k), str(tmp_path / f"mine_R{k}.npz"), log=lambda *_: None, max_rules=400)
    o2 = S.aggregate(str(tmp_path / "mine_R*.npz"), log=lambda *_: None)
    assert o2["scenarios"]["TAKER"]["edges"] == [] and o2["scenarios"]["MAKER"]["edges"] == []
