"""2.2 event-driven research pipeline on synthetic trades (no network): run -> aggregate, holdout refused,
no fake edge on random data, portfolio limits."""
from datetime import date, datetime, timezone

import numpy as np
import pytest

from millionx.scalp import features as F
from millionx.scalp import research as R


def _loader(seed=0):
    def load(day, prev_close):
        d0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
        rng = np.random.default_rng(seed * 10_000 + day.toordinal())
        n = 86_400 * 2
        t = d0 + np.sort(rng.integers(0, 86_400_000, n))
        p0 = prev_close if np.isfinite(prev_close) else 50_000.0
        steps = rng.standard_t(3, n) * 0.8
        for k in rng.integers(0, n - 300, 12):     # random impulses in both directions: no exploitable edge
            steps[k:k + 200] += rng.choice([-1, 1]) * 2.0
        price = p0 + np.cumsum(steps)
        return F.from_trades(d0, t, price, rng.exponential(0.02, n), rng.random(n) < 0.5, prev_close)
    return load


def test_holdout_refused(tmp_path):
    with pytest.raises(ValueError):
        R.run_symbol("X", str(tmp_path / "x.npz"), date(2026, 6, 29), date(2026, 7, 1), loader=_loader())


def test_run_and_aggregate_no_fake_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DISC", ("2025-01-02", "2025-01-05"))
    monkeypatch.setattr(R, "VALID", ("2025-01-05", "2025-01-08"))
    monkeypatch.setattr(R, "QUARTERS", [("2025-01-05", "2025-01-06"), ("2025-01-06", "2025-01-08")])
    monkeypatch.setattr(R.O, "MIN_N", 5)
    for k, sym in enumerate(("AAAUSDT", "BBBUSDT")):
        n = R.run_symbol(sym, str(tmp_path / f"sc_{sym}.npz"), date(2025, 1, 1), date(2025, 1, 8),
                         log=lambda *_: None, loader=_loader(k))
        assert n > 0
    z = np.load(tmp_path / "sc_AAAUSDT.npz")
    assert {"BASE_r", "STRESSED_r", "MAKER_r", "f_v30z", "regime", "tf"} <= set(z.files)
    assert set(np.unique(z["tf"])) <= {1, 60, 180, 300}
    assert np.all(np.isin(z["BASE_reason"], ["stop", "take_profit", "break_even", "trailing", "momentum_exhaustion",
                                                "order_flow_reversal", "max_hold"]))
    o = R.aggregate(str(tmp_path / "sc_*.npz"), log=lambda *_: None)
    assert all(v["verdict"] == "NO_TRADE" for v in o.get("maker_track", {}).values())
    assert set(o["verdict"].values()) == {"NO_TRADE"}, "random data must not pass activation"
    for name, p in o["portfolio"].items():
        for lev, s in p.items():
            assert s.get("trades_per_day", 0) >= 0
    md = R.to_markdown(o)
    assert "Verdict" in md and "Portfolio" in md


def test_portfolio_limits():
    n = 6
    D = {"t": np.array([0, 1000, 2000, 3000, 86_400_000, 86_401_000]), "stop_bps": np.full(n, 20.0),
         "BASE_r": np.array([-1.0, -1.0, -1.0, -1.0, 2.0, 1.0]), "BASE_hold": np.full(n, 10_000),
         "BASE_net": np.full(n, -20.0), "BASE_gross": np.full(n, -7.0)}
    s = R.portfolio(D, np.arange(n), "BASE", 20)
    assert s["skipped"]["max_positions"] >= 1, "at most 2 open positions"
    s5 = R.portfolio(D, np.arange(n), "BASE", 5)
    # 20 bps stop -> notional 1250 EUR -> 250 EUR margin at 5x: two fit in 500 EUR, a third would not
    assert s5["n"] <= s["n"]
