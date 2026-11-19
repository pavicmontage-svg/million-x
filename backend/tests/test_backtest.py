from millionx.backtest import montecarlo
from millionx.backtest.data import synthetic_bars
from millionx.backtest.engine import BacktestConfig, run
from millionx.backtest.walkforward import walk_forward
from millionx.config import Settings


def test_accounting_is_consistent():
    data = {"AAAUSDT": synthetic_bars(25_000, seed=1), "BBBUSDT": synthetic_bars(25_000, seed=2, price=30)}
    res = run(data, Settings(), BacktestConfig(start_equity=40.0))
    m = res.metrics
    total = sum(t.net_pnl for t in res.trades)
    assert abs(m["end_equity"] - (40.0 + total)) < 1e-9
    for t in res.trades:
        assert t.fees > 0, "every real trade pays fees"
        assert abs(t.net_pnl - (t.gross_pnl - t.fees - t.funding)) < 1e-9
    assert res.virtual_trades, "competition shadow book must record trades"


def test_no_edge_on_random_walk():
    """Synthetic data has no edge by construction: a correct engine with
    realistic costs must not report a convincing profit."""
    data = {"AAAUSDT": synthetic_bars(40_000, seed=9)}
    res = run(data, Settings(), BacktestConfig(start_equity=40.0, virtual_only=True))
    rs = [t.r_multiple for t in res.virtual_trades]
    assert len(rs) > 50
    assert sum(rs) / len(rs) < 0.1


def test_walk_forward_rejects_unvalidated_bot():
    data = {"AAAUSDT": synthetic_bars(60 * 24 * 30, seed=3)}
    wf = walk_forward(data, Settings(), "bot2_ema", train_days=8, test_days=4, max_windows=2)
    assert wf.activation["status"] == "RESEARCH"
    assert wf.windows and all("params" in w for w in wf.windows)


def test_monte_carlo_requires_data():
    assert montecarlo.simulate([0.5] * 5)["available"] is False
    r = montecarlo.simulate([1, -1, 0.5, -0.5] * 10, 0.005, runs=500)
    assert r["available"] and 0 <= r["max_dd_p95"] <= 1
