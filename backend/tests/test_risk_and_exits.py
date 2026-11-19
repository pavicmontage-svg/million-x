import math

from millionx.config import FeeModel, RiskLimits
from millionx.engine.position import ExitPolicy, update_on_bar
from millionx.models import Position, Side, Signal
from millionx.risk import RiskManager, SymbolRules, feasibility_report

T0 = 1_750_000_000_000


def sig(entry=100.0, stop=99.6, tp1=100.4, tp2=101.6, side=Side.LONG, sym="ETHUSDT"):
    return Signal("botX", sym, side, entry, stop, tp1, tp2, 0.6, T0, 5)


def rm(equity=1000.0):
    r = RiskManager(RiskLimits(), FeeModel())
    r.reset(equity, T0)
    return r


def test_risk_amount_is_half_percent_including_costs():
    r = rm(1000)
    d = r.size(sig(), SymbolRules("ETHUSDT", step_size=0.0001, min_qty=0.0001, min_notional=5), [], 0.003, T0)
    assert d.approved, d.reasons
    assert d.risk_amount <= 1000 * 0.005 + 1e-6
    assert d.risk_amount > 1000 * 0.005 * 0.95


def test_four_eur_rejected_by_min_notional_with_explanation():
    r = rm(4.3)
    rules = SymbolRules("ETHUSDT", step_size=0.001, min_qty=0.001, min_notional=20.0)
    d = r.size(sig(entry=3000, stop=2988, tp1=3012, tp2=3030), rules, [], 0.003, T0)
    assert not d.approved
    assert "below_exchange_minimum" in d.reasons[0]


def test_feasibility_report_math():
    rep = feasibility_report(4.3, 3000, 0.004, SymbolRules("ETHUSDT", min_notional=20.0, min_qty=0.001),
                             RiskLimits(), FeeModel())
    assert not rep["feasible"]
    assert abs(rep["doublings_needed_wins_in_a_row_at_1R"] - math.log(2) / math.log(1.005)) < 1e-9


def test_leverage_is_capped_by_volatility_not_exchange_max():
    r = rm(100)
    rules = SymbolRules("X", step_size=0.0001, min_qty=0.0001, min_notional=5, max_leverage=125)
    d = r.size(sig(entry=100, stop=99.9, tp1=100.15, tp2=100.4), rules, [], atr_pct15=0.01, ts_ms=T0)
    if d.approved:
        assert d.leverage <= 2  # 0.02 / 0.01
    else:
        assert any("leverage" in x or "liquidation" in x or "fees" in x for x in d.reasons)


def test_daily_loss_and_drawdown_halt():
    r = rm(1000)
    r.update_equity(965, T0 + 1000)
    ok, why = r.can_open(T0 + 2000)
    assert not ok and why == "daily_loss_limit"
    r2 = rm(1000)
    r2.update_equity(790, T0)
    assert r2.can_open(T0)[1] == "max_drawdown_reached"


def test_loss_streak_cooldown_and_scaling():
    r = rm(1000)
    base = r.risk_fraction()
    for _ in range(4):
        r.on_trade_closed(-1, T0)
    assert r.can_open(T0 + 1000)[1] == "loss_streak_cooldown"
    assert r.risk_fraction() < base
    assert r.can_open(T0 + 61 * 60_000)[0]


def test_tighten_only():
    r = rm(1000)
    r.tighten(5.0)
    assert r.tighten_factor == 1.0
    r.tighten(0.5)
    assert abs(r.risk_fraction() - 0.0025) < 1e-12
    r.tighten(0.0)
    assert r.can_open(T0)[1] == "suspended_by_risk_oversight"


def test_emergency_and_stale_data_block():
    r = rm(1000)
    r.state.data_fresh = False
    assert r.can_open(T0)[1] == "stale_market_data"
    r.state.data_fresh = True
    r.state.connection_ok = False
    assert r.can_open(T0)[1] == "exchange_disconnected"
    r.state.connection_ok = True
    r.emergency(True)
    assert r.can_open(T0)[1] == "emergency_stop"


def _pos():
    return Position("p", "b", "X", Side.LONG, 1.0, 100.0, 99.0, 101.0, 102.0, 99.0, T0, qty_initial=1.0,
                    best_price=100.0, atr_at_entry=0.5)


def test_stop_first_when_both_hit():
    p = _pos()
    ev = update_on_bar(p, 100, 102.5, 98.5, 101, 0.5, T0 + 60_000, ExitPolicy(), 0.0005)
    assert ev[0].kind == "CLOSE" and ev[0].reason == "stop_loss" and ev[0].price == 99.0


def test_gap_fills_at_open():
    p = _pos()
    ev = update_on_bar(p, 98.0, 98.5, 97.0, 98.2, 0.5, T0 + 60_000, ExitPolicy(), 0.0005)
    assert ev[0].price == 98.0


def test_partial_then_breakeven_then_trailing():
    p = _pos()
    ev = update_on_bar(p, 100, 101.2, 99.8, 101.1, 0.5, T0 + 60_000, ExitPolicy(), 0.0005)
    kinds = [e.kind for e in ev]
    assert "PARTIAL" in kinds and p.tp1_done and p.stop > 100.0
    p.qty = 0.5
    update_on_bar(p, 101.1, 101.9, 101.0, 101.8, 0.5, T0 + 120_000, ExitPolicy(), 0.0005)
    assert p.stop >= 101.9 - 0.5 - 1e-9


def test_time_stop():
    p = _pos()
    ev = update_on_bar(p, 100, 100.2, 99.9, 100.1, 0.5, T0 + 181 * 60_000, ExitPolicy(), 0.0005)
    assert ev[-1].reason == "time_stop"
