import copy

import numpy as np
import pytest

from quanttitan.backtest.engine import Backtester
from quanttitan.backtest.metrics import compute_metrics, max_drawdown
from quanttitan.config import AppConfig, merge_patch
from quanttitan.core.types import Direction, Position, TargetLevel
from quanttitan.data.candles import Candles
from quanttitan.data.synthetic import generate
from quanttitan.execution.exits import simulate_bar
from quanttitan.orchestrator.edge import EdgeBook
from quanttitan.research.lab import ResearchLab
from quanttitan.research.stats import deflated_sharpe, probabilistic_sharpe

FIELDS = ("ts", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy")


def research_cfg():
    # research configuration: synthetic prices around 100 have tight stops relative to fees
    return merge_patch(AppConfig(), {"orchestrator": {"max_cost_r": 1.0, "min_rr_after_costs": 0.3},
                                     "risk": {"max_correlated_risk_pct": 2.0}})


@pytest.fixture(scope="module")
def data():
    a, _ = generate(4000, seed=21, symbol="AAAUSDT", base_vol=0.004)
    b, _ = generate(4000, seed=22, symbol="BBBUSDT", start_price=50, base_vol=0.004)
    for c in (a, b):  # realistic liquidity (~100M USDT per 5m bar is BTC-like; use ~50M)
        c.volume *= 500; c.quote_volume *= 500; c.taker_buy *= 500
    return {"AAAUSDT": a, "BBBUSDT": b}


@pytest.fixture(scope="module")
def result(data):
    return Backtester(research_cfg(), data).run()


def test_backtest_produces_trades_with_full_cost_accounting(result):
    assert result.metrics["trades"] > 5, result.decisions
    for t in result.trades:
        assert t.net_pnl == pytest.approx(t.gross_pnl - t.fees - t.funding, abs=1e-9)
        assert t.fees > 0
        assert t.closed_ts >= t.opened_ts
    assert result.metrics["fees"] > 0
    assert set(result.metrics["by_engine"]).issubset({"vwap", "trend", "breakout", "order_flow"})


def test_equity_consistency(result):
    final = result.equity0 + sum(t.net_pnl for t in result.trades)
    assert result.equity[-1] == pytest.approx(final, rel=1e-6)


def test_no_lookahead_future_perturbation(data, result):
    """Rewriting every bar after T must not change any trade that closed before T."""
    a = data["AAAUSDT"]
    T_idx = 2500
    T = int(a.close_ts[T_idx])
    rng = np.random.default_rng(99)
    pert = {}
    for s, c in data.items():
        arrs = {f: getattr(c, f).copy() for f in FIELDS}
        k = int(np.searchsorted(c.close_ts, T, "right"))
        noise = np.exp(np.cumsum(0.01 * rng.standard_normal(len(c) - k)))
        for f in ("open", "high", "low", "close"):
            arrs[f][k:] = arrs[f][k:] * noise
        arrs["volume"][k:] *= 3
        pert[s] = Candles(s, c.timeframe, *(arrs[f] for f in FIELDS))
    r2 = Backtester(research_cfg(), pert).run()
    before = lambda r: [(t.symbol, t.opened_ts, t.closed_ts, round(t.net_pnl, 8)) for t in r.trades if t.closed_ts < T - 300_000]
    assert before(result) == before(r2)
    assert before(result), "test needs trades before T"


def test_backtest_does_not_mutate_caller_config(data):
    cfg = research_cfg()
    snap = cfg.json()
    Backtester(cfg, data).run(end_ts=int(data["AAAUSDT"].close_ts[600]))
    assert cfg.json() == snap


def _pos(direction=Direction.LONG):
    return Position("p", "X", direction, "e", "s", 100.0, 1.0, 1.0, 98.0, 98.0,
                    [TargetLevel(102.0, 0.5), TargetLevel(104.0, 1.0)], 0, best_price=100.0)


def test_stop_first_when_bar_hits_both():
    p = _pos()
    fills = simulate_bar(p, 100, 103, 97, 101)
    assert len(fills) == 1 and fills[0].reason == "stop_loss" and fills[0].price == 98.0


def test_gap_through_stop_fills_at_open():
    p = _pos()
    fills = simulate_bar(p, 95, 96, 94, 95.5)
    assert fills[0].price == 95.0


def test_partial_then_breakeven():
    p = _pos()
    f1 = simulate_bar(p, 100, 102.5, 99.5, 102)
    assert f1[0].reason == "take_profit_1" and p.qty == pytest.approx(0.5) and p.stop == 100.0
    f2 = simulate_bar(p, 101, 101.2, 99.0, 99.5)
    assert f2[0].reason == "breakeven_stop" and p.qty == 0


def test_short_mirror():
    p = Position("p", "X", Direction.SHORT, "e", "s", 100.0, 1.0, 1.0, 102.0, 102.0,
                 [TargetLevel(98.0, 1.0)], 0, best_price=100.0)
    f = simulate_bar(p, 100, 100.5, 97.5, 98.5)
    assert f[0].reason == "take_profit_1" and f[0].price == 98.0


def test_metrics_known_values():
    from quanttitan.core.types import TradeRecord
    mk = lambda pnl, k: TradeRecord(str(k), "X", "LONG", "e", "s", "R", 1, 1, 1, k * 86_400_000, k * 86_400_000 + 1000,
                                    pnl, 0, 0, 0, pnl, pnl / 10, 10, "x")
    trades = [mk(10, 1), mk(-5, 2), mk(10, 3), mk(-5, 4)]
    m = compute_metrics(trades, 1000)
    assert m["win_rate"] == 0.5 and m["profit_factor"] == 2.0 and m["net_pnl"] == 10
    assert m["expectancy_r"] == pytest.approx(0.25)
    dd, abs_dd = max_drawdown(np.array([1010, 1005, 1015, 1010.0]), 1000)
    assert abs_dd == 5 and dd == pytest.approx(5 / 1010 * 100)


def test_monte_carlo_and_dsr(result):
    mc = ResearchLab.monte_carlo(result.trades, result.equity0, n=300)
    assert mc["simulations"] == 300 and 0 <= mc["prob_loss"] <= 1
    assert mc["bootstrap_max_dd_pct"]["p5"] <= mc["bootstrap_max_dd_pct"]["p95"]
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(500) * 0.01
    assert probabilistic_sharpe(noise + 0.01) > 0.99
    d = deflated_sharpe(noise, n_trials=100)
    assert d["dsr"] < 0.95  # pure noise is not significant after 100 trials


def test_walk_forward_records_only_oos_evidence(data):
    lab = ResearchLab(research_cfg(), data, warmup_days=2)
    eb = EdgeBook()
    wf = lab.walk_forward({"orchestrator.conflict_margin": [20.0, 30.0]}, train_days=4, test_days=2, min_trades=1,
                          edge_book=eb, max_folds=2)
    assert len(wf["folds"]) == 2
    assert all(f["test"]["label"].startswith("wf-test") for f in wf["folds"])
    n_oos = wf["oos_metrics"]["trades"]
    assert sum(e.n for e in eb.all_estimates()) == n_oos
    for e in eb.all_estimates():
        assert set(e.by_source) == {"backtest_oos"}


def test_policy_comparison_runs(data):
    lab = ResearchLab(research_cfg(), data)
    end = int(data["AAAUSDT"].close_ts[1500])
    out = lab.policy_comparison(end=end)
    assert set(out["results"]) == {"regime_gated", "majority_vote", "confidence_average"}


def test_tp1_and_tp2_in_the_same_candle_closes_cleanly():
    """Regression: one bar hitting both targets used to close the trade on the first fill and crash on the second."""
    from quanttitan.core.types import Position, TargetLevel
    p = Position("p", "X", Direction.LONG, "e", "s", 100.0, 1.0, 1.0, 98.0, 98.0,
                 [TargetLevel(102.0, 0.5), TargetLevel(104.0, 1.0)], 0, best_price=100.0)
    fills = simulate_bar(p, 100.5, 105.0, 100.2, 104.5)
    assert [f.reason for f in fills] == ["take_profit_1", "take_profit_2"] and p.qty == 0


def test_backtest_survives_wide_range_bars(data):
    """Bars with huge ranges make multi-target fills in one candle common; the run must account every fill."""
    big = {}
    for s, c in data.items():
        cc = Candles(s, c.timeframe, *(getattr(c, f).copy() for f in FIELDS))
        cc.high = cc.high + (cc.high - cc.low) * 3
        cc.low = cc.low - (cc.high - cc.low) * 0.1
        big[s] = cc
    r = Backtester(research_cfg(), big).run()
    for t in r.trades:
        assert sum(e["qty"] for e in t.meta["exits"]) == pytest.approx(t.qty)
    assert r.equity[-1] == pytest.approx(r.equity0 + sum(t.net_pnl for t in r.trades), rel=1e-6)
