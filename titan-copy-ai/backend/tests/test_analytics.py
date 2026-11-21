"""Analytics tests. All trade data below is SYNTHETIC test fixture data."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from titan.analytics.behaviour import analyse, detect_grid_and_averaging, detect_martingale
from titan.analytics.metrics import EquityRow, TradeRow, compute_metrics, max_drawdown_pct
from titan.analytics.quant import correlation_matrix, monte_carlo, walk_forward
from titan.analytics.ranking import score_trader
from titan.discovery.sources import parse_trade_csv

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=500)


def disciplined(n=400, seed=1):
    rng = np.random.default_rng(seed)
    trades, bal, curve = [], 10_000.0, []
    for i in range(n):
        ot = T0 + timedelta(days=i * 1.2, hours=8)
        win = rng.random() < 0.5
        p = 60.0 if win else -30.0
        bal += p
        trades.append(TradeRow("EURUSD", "buy" if i % 2 else "sell", 0.1, ot, ot + timedelta(hours=3),
                               1.1, 1.1, p, sl=1.09))
        curve.append(EquityRow(ot + timedelta(hours=3), balance=bal, equity=bal - abs(rng.normal(0, 15))))
    return trades, curve


def test_metrics_disciplined_trader():
    trades, curve = disciplined()
    m = compute_metrics(trades, curve, now=NOW)
    assert m["total_trades"] == 400
    assert m["profit_factor"] == pytest.approx(2 * m["win_rate"] / (100 - m["win_rate"]), rel=1e-6)
    assert m["avg_trade_duration_h"] == pytest.approx(3)
    assert m["max_equity_drawdown_pct"] is not None and m["sharpe"] is not None
    assert m["roi_365d"] is not None and m["roi_7d"] is not None
    assert m["sl_usage_rate"] == 100
    assert m["active_copiers"] is None and m["slippage"] is None   # never invented


def test_unknowns_stay_unknown():
    m = compute_metrics([], [], now=NOW)
    assert all(v is None for v in m.values())
    res = score_trader(m, analyse([], m), data_source="manual_import")
    assert res.score is None and res.rejected
    assert any(r["code"] == "no_verified_history" for r in res.rejections)


def test_short_history_roi_unknown():
    trades, curve = disciplined(30)
    m = compute_metrics(trades, curve, now=T0 + timedelta(days=40))
    assert m["roi_365d"] is None and m["roi_180d"] is None and m["roi_30d"] is not None


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130]) == pytest.approx(25)


def test_martingale_detected_and_rejected():
    trades, lot, t = [], 0.01, T0
    for i in range(60):
        loss = i % 3 != 2
        trades.append(TradeRow("EURUSD", "buy", lot, t, t + timedelta(hours=1), 1.1, 1.1,
                               -10 * lot * 100 if loss else 25 * lot * 100))
        lot = lot * 2 if loss else 0.01
        t += timedelta(hours=2)
    f = detect_martingale(trades)
    assert f and f.code == "martingale" and f.severity == "reject"
    m = compute_metrics(trades, [], now=t)
    res = score_trader(m, analyse(trades, m))
    assert res.rejected and not res.eligible


def test_grid_detected():
    trades, t = [], T0
    for k in range(10):          # 10 grids of 6 stacked buys, each added lower, no SL
        base = 1.10
        for j in range(6):
            ot = t + timedelta(minutes=10 * j)
            trades.append(TradeRow("EURUSD", "buy", 0.01, ot, t + timedelta(hours=5), base - 0.001 * j, base, 5.0))
        t += timedelta(days=1)
    codes = [f.code for f in detect_grid_and_averaging(trades)]
    assert "grid" in codes


def test_score_disciplined_trader_and_explanation():
    trades, curve = disciplined()
    m = compute_metrics(trades, curve, now=NOW)
    res = score_trader(m, analyse(trades, m), data_source="myfxbook_api")
    assert not res.rejected and res.score is not None and 0 <= res.score <= 100
    assert res.components["execution_quality"]["score"] is None   # unknown -> excluded
    assert "UNKNOWN components" in res.explanation and "Past performance" in res.explanation
    assert "Manual Day Trader" in res.categories


def test_hidden_floating_drawdown_rejected():
    m = {"max_equity_drawdown_pct": 35.0, "max_balance_drawdown_pct": 4.0}
    res = score_trader(m, analyse([TradeRow("EURUSD", "buy", 1, T0, T0, 1, 1, 1)], m))
    assert any(r["code"] == "hidden_floating_drawdown" for r in res.rejections)


def test_monte_carlo_and_walk_forward():
    rng = np.random.default_rng(3)
    r = list(rng.choice([1.0, -0.5], size=300, p=[0.5, 0.5]))
    mc = monte_carlo(r, n_sims=2000)
    assert mc["final_return_pct"]["median"] > 0 and 0 <= mc["prob_loss"] <= 1
    assert mc["max_drawdown_pct"]["p95"] >= mc["max_drawdown_pct"]["median"]
    wf = walk_forward(r, 5)
    assert len(wf["windows"]) == 5 and "stable" in wf
    corr = correlation_matrix({"a": {str(i): x for i, x in enumerate(r[:50])},
                               "b": {str(i): -x for i, x in enumerate(r[:50])}})
    assert corr["a"]["b"] == pytest.approx(-1)


def test_csv_import_myfxbook_format():
    csv_text = ("Open Date,Close Date,Symbol,Action,Units/Lots,SL,TP,Open Price,Close Price,Commission,Swap,Pips,Profit\n"
                "01/02/2025 10:00,01/02/2025 12:30,EURUSD,Buy,0.10,1.0800,1.0900,1.0850,1.0880,-0.70,0,30,30.00\n"
                "01/03/2025 09:00,01/03/2025 09:40,GBPUSD,Sell,0.05,1.2700,1.2500,1.2650,1.2680,-0.35,0,-30,-15.00\n"
                "01/03/2025 09:00,,,Deposit,,,,,,,,,1000\n")
    rows = parse_trade_csv(csv_text)
    assert len(rows) == 2 and rows[0].side == "buy" and rows[1].profit == -15.0
    assert rows[0].close_time.hour == 12


def test_ingest_pipeline_persists(db):
    from sqlalchemy import select

    from titan.discovery.service import ingest, ranking, seed_watchlist
    from titan.discovery.sources import TraderData
    from titan.models import Notification, Trader, TraderScore

    trades, curve = disciplined(120)
    with db.session_scope() as s:
        assert seed_watchlist(s) == 7
        assert seed_watchlist(s) == 0
        tr, sc = ingest(s, TraderData("myfxbook", "123", "Test Account", "manual_import", trades=trades, curve=curve))
        tid = tr.id
    with db.session_scope() as s:
        tr = s.get(Trader, tid)
        assert tr.instruments == ["EURUSD"] and tr.status.value == "monitoring"
        assert s.scalar(select(TraderScore).where(TraderScore.trader_id == tid)) is not None
        assert s.scalar(select(Notification).where(Notification.kind == "trader_discovered"))
        rows = ranking(s)
        assert len(rows) == 8
        unknown = [r for r in rows if r["trader"].platform == "zulutrade"]
        assert all(r["score"] is None for r in unknown)   # watchlist: no fabricated data
    with db.session_scope() as s:  # re-ingest is idempotent for trades
        ingest(s, TraderData("myfxbook", "123", "Test Account", "manual_import", trades=trades, curve=curve))
    with db.session_scope() as s:
        from titan.models import TraderTrade
        assert len(list(s.scalars(select(TraderTrade).where(TraderTrade.trader_id == tid)))) == 120
