"""2.3 research: no look-ahead, costs/funding applied, trend found on trending data, nothing on random data."""
import numpy as np

from millionx.strat23 import research as R

D0 = int(np.datetime64("2020-01-01").astype("datetime64[ms]").astype(np.int64))


def _raw(trend: float, seed: int):
    rng = np.random.default_rng(seed)
    raw = {}
    n = (int(np.datetime64("2026-07-01").astype("datetime64[ms]").astype(np.int64)) - D0) // (4 * 3600_000)
    for j, s in enumerate(R.SYMBOLS):
        # regime-switching drift so that trend following has something to find when trend > 0
        regime = np.repeat(rng.choice([-1, 1], n // 300 + 1), 300)[:n]
        lr = rng.normal(0, 0.01, n) + trend * regime
        c = 100 * np.exp(np.cumsum(lr))
        t = D0 + np.arange(n) * 4 * 3600_000
        k = np.column_stack([t, c * 1.002, c * 0.998, c])
        f = np.column_stack([D0 + np.arange(n // 2) * 8 * 3600_000, np.full(n // 2, 1e-4)])
        raw[s] = {"k": k, "f": f}
    return raw


def test_no_lookahead_signals():
    P = R.daily_panel(_raw(0.0, 1))
    full = R.signals(P, "T", R.GRID["T"][1])
    cut = {k: (v[:1500] if k != "days" else v[:1500]) for k, v in P.items()}
    part = R.signals(cut, "T", R.GRID["T"][1])
    assert np.allclose(full[:1500], part)


def test_random_has_no_edge_and_trend_is_found():
    o = R.run(_raw(0.0, 2), log=lambda *_: None)
    assert all(v["verdict"] == "NO_TRADE" for k, v in o["strategies"].items() if k != "C")
    o2 = R.run(_raw(0.002, 3), log=lambda *_: None)
    t = o2["strategies"]["T"]
    assert t["configs"][t["chosen_on_discovery"]]["validation"]["sharpe"] > 1.0
    c = o2["strategies"]["C"]["configs"][0]["validation"]
    assert c["avg_daily_pct"] > 0, "positive funding is income for the hedged short"
    assert "## Chosen configuration by year" in R.to_markdown(o2)
