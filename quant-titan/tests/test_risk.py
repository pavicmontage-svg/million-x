import time

import pytest

from quanttitan.config import AppConfig, RiskConfig
from quanttitan.core.types import ContractSpec, Direction, TradeRecord
from quanttitan.risk.approval import ApprovalError, ApprovalSigner
from quanttitan.risk.breakers import CircuitBreakers
from quanttitan.risk.manager import MarketCheck, RiskManager, liquidation_price
from quanttitan.risk.portfolio import PortfolioState

from helpers import DAY0, SPEC, decision, position


def rm(**risk):
    cfg = AppConfig()
    for k, v in risk.items():
        setattr(cfg.risk, k, v)
    return RiskManager(cfg)


def pf(balance=10_000.0, now=DAY0):
    p = PortfolioState(balance)
    p.roll(now)
    return p


MKT = MarketCheck(spread_bps=1.0, atr=1.0, bar_quote_volume=1e9)


def test_position_size_is_risk_based():
    r = rm()
    p = pf()
    v = r.review(decision(entry=100, stop=98), p, SPEC, MKT)
    assert v.approved, v.reasons
    assert v.budget_usdt == pytest.approx(50.0)  # 0.5% of 10k
    assert v.risk_usdt <= 50.0 + 1e-9
    assert v.risk_usdt > 45.0
    # qty * stop distance alone is below budget because costs are included in the per-unit loss
    assert v.qty * 2.0 < 50.0
    # halving the stop distance roughly doubles the size
    v2 = r.review(decision(entry=100, stop=99), pf(), SPEC, MKT)
    assert v2.qty > 1.8 * v.qty


def test_size_respects_step_and_minimum():
    r = rm()
    v = r.review(decision(entry=60_000, stop=59_000), pf(balance=100.0), SPEC, MKT)
    assert not v.approved
    assert any("not feasible" in x for x in v.reasons)
    q, _, _ = r.size(equity=10_000, entry=60_000, stop=59_400, spec=SPEC, atr=100)
    assert abs(q / SPEC.step_size - round(q / SPEC.step_size)) < 1e-9


def test_veto_overrides_maximum_confidence_when_breaker_active():
    r = rm()
    p = pf()
    p.balance = 9_700.0  # 3% daily loss > 2% limit
    v = r.review(decision(conf=100.0), p, SPEC, MKT)
    assert not v.approved
    assert "DAILY_LOSS" in r.breakers.active
    assert v.approval is None


def test_max_open_risk_and_positions():
    r = rm(max_open_risk_pct=1.0)
    p = pf()
    p.positions = [position("ETHUSDT", entry=100, stop=98, qty=40)]  # 80 USDT risk = 0.8%
    p.prices["ETHUSDT"] = 100
    v = r.review(decision(), p, SPEC, MKT)
    assert not v.approved and any("open risk" in x for x in v.reasons)
    r2 = rm(max_concurrent_positions=1)
    p2 = pf(); p2.positions = [position("ETHUSDT", qty=1)]; p2.prices["ETHUSDT"] = 100
    v2 = r2.review(decision(), p2, SPEC, MKT)
    assert any("open positions" in x for x in v2.reasons)


def test_correlated_exposure_blocked():
    r = rm(max_correlated_risk_pct=0.8, max_open_risk_pct=5)
    p = pf()
    p.positions = [position("ETHUSDT", entry=100, stop=98, qty=20)]  # 40 USDT risk
    p.prices["ETHUSDT"] = 100
    p.correlations[("BTCUSDT", "ETHUSDT")] = 0.9
    v = r.review(decision(direction=Direction.LONG), p, SPEC, MKT)
    assert not v.approved and any("correlated" in x for x in v.reasons)
    # the opposite direction is a hedge, not concentration
    v2 = r.review(decision(direction=Direction.SHORT, entry=100, stop=102), p, SPEC, MKT)
    assert not any("correlated" in x for x in v2.reasons)


def test_liquidation_distance_check():
    r = rm(max_leverage=20, max_open_risk_pct=10, max_symbol_exposure_pct=1000)
    p = pf(balance=10_000)
    p.positions = [position("ETHUSDT", entry=100, stop=99, qty=99, leverage=1)]  # consumes 9,900 of margin
    p.prices["ETHUSDT"] = 100
    v = r.review(decision(entry=100, stop=95), p, SPEC, MarketCheck(spread_bps=1, atr=1))
    assert not v.approved
    assert any("liquidation" in x or "leverage" in x for x in v.reasons)
    assert liquidation_price(100, Direction.LONG, 10, 0.004) == pytest.approx(90.4)
    assert liquidation_price(100, Direction.SHORT, 10, 0.004) == pytest.approx(109.6)


def test_wrong_side_or_missing_stop_vetoed():
    r = rm()
    assert not r.review(decision(entry=100, stop=101), pf(), SPEC, MKT).approved
    assert not r.review(decision(entry=100, stop=0), pf(), SPEC, MKT).approved
    v = r.review(decision(entry=100, stop=80), pf(), SPEC, MKT)
    assert any("stop distance" in x for x in v.reasons)


def test_spread_funding_and_staleness_protection():
    r = rm()
    assert not r.review(decision(), pf(), SPEC, MarketCheck(spread_bps=20, atr=1)).approved
    v = r.review(decision(direction=Direction.LONG), pf(), SPEC, MarketCheck(spread_bps=1, atr=1, funding_rate=0.005))
    assert any("pays funding" in x for x in v.reasons)
    v2 = r.review(decision(direction=Direction.SHORT, entry=100, stop=102), pf(), SPEC, MarketCheck(spread_bps=1, atr=1, funding_rate=0.005))
    assert not any("funding" in x for x in v2.reasons)
    assert not r.review(decision(), pf(), SPEC, MarketCheck(spread_bps=1, atr=1, data_age_s=1000)).approved


def test_zero_regime_multiplier_blocks():
    v = rm().review(decision(), pf(), SPEC, MarketCheck(spread_bps=1, atr=1, regime_multiplier=0.0))
    assert not v.approved


def test_drawdown_multiplier_only_reduces():
    r = rm()
    p = pf()
    p.peak_equity = 10_000
    mults = []
    for bal in (10_000, 9_700, 9_500, 9_200, 9_000, 8_000):
        p.balance = bal
        mults.append(r.drawdown_multiplier(p))
    assert all(m <= 1.0 for m in mults)
    assert mults == sorted(mults, reverse=True)
    assert mults[-1] == pytest.approx(r.cfg.risk.drawdown_derisk_floor)


def _loss(ts):
    return TradeRecord("t", "BTCUSDT", "LONG", "vwap", "X", "RANGE", 100, 99, 1, ts - 1000, ts, -1, 0, 0, 0, -1.0, -1.0, 1.0, "stop")


def test_consecutive_loss_cooldown():
    b = CircuitBreakers(RiskConfig(max_consecutive_losses=3, consecutive_loss_cooldown_min=60))
    p = pf()
    for k in range(3):
        p.on_trade_closed(_loss(DAY0 + k))
    b.evaluate(p, DAY0 + 10)
    assert "CONSECUTIVE_LOSSES" in b.active
    b.evaluate(p, DAY0 + 30 * 60_000)
    assert "CONSECUTIVE_LOSSES" in b.active
    b.evaluate(p, DAY0 + 61 * 60_000)
    assert "CONSECUTIVE_LOSSES" not in b.active and p.consecutive_losses == 0


def test_daily_breaker_auto_resets_next_day_monthly_needs_confirmation():
    events = []
    b = CircuitBreakers(RiskConfig(), on_event=lambda e, br: events.append((e, br.name)))
    p = pf()
    p.balance = 9_750
    b.evaluate(p, DAY0 + 3_600_000)
    assert "DAILY_LOSS" in b.active
    p.roll(DAY0 + 86_400_000 + 60_000)  # next day: day-start equity resets to current
    b.evaluate(p, DAY0 + 86_400_000 + 60_000)
    assert "DAILY_LOSS" not in b.active
    assert ("trip", "DAILY_LOSS") in events and ("reset", "DAILY_LOSS") in events
    p.month_peak_equity = 12_000
    b.evaluate(p, DAY0 + 86_400_000 + 120_000)
    assert "MONTHLY_DRAWDOWN" in b.active
    with pytest.raises(PermissionError):
        b.manual_reset("MONTHLY_DRAWDOWN")
    assert b.manual_reset("MONTHLY_DRAWDOWN", confirm=True)


def test_emergency_stop_blocks_and_requires_confirmed_reset():
    r = rm()
    r.breakers.emergency_stop("test")
    assert not r.review(decision(), pf(), SPEC, MKT).approved
    with pytest.raises(PermissionError):
        r.breakers.manual_reset("EMERGENCY_STOP")
    r.breakers.manual_reset("EMERGENCY_STOP", confirm=True)
    assert r.review(decision(), pf(), SPEC, MKT).approved


def test_approval_token_is_enforced():
    s = ApprovalSigner()
    tok = s.issue(decision_id="d1", symbol="BTCUSDT", side="BUY", max_qty=1.0, ref_price=100, max_deviation_pct=1)
    s.verify(tok, symbol="BTCUSDT", side="BUY", qty=1.0, price=100.5, consume=False)
    with pytest.raises(ApprovalError):
        s.verify(None, symbol="BTCUSDT", side="BUY", qty=1, price=100)
    with pytest.raises(ApprovalError):
        s.verify({**tok, "max_qty": 100.0}, symbol="BTCUSDT", side="BUY", qty=50, price=100)  # tampered
    with pytest.raises(ApprovalError):
        s.verify(tok, symbol="BTCUSDT", side="BUY", qty=1.5, price=100)
    with pytest.raises(ApprovalError):
        s.verify(tok, symbol="BTCUSDT", side="SELL", qty=1, price=100)
    with pytest.raises(ApprovalError):
        s.verify(tok, symbol="BTCUSDT", side="BUY", qty=1, price=102)  # price drifted beyond band
    with pytest.raises(ApprovalError):
        ApprovalSigner().verify(tok, symbol="BTCUSDT", side="BUY", qty=1, price=100)  # other key
    s.verify(tok, symbol="BTCUSDT", side="BUY", qty=1, price=100)
    with pytest.raises(ApprovalError, match="already used"):
        s.verify(tok, symbol="BTCUSDT", side="BUY", qty=1, price=100)
    old = s.issue(decision_id="d2", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1, ttl_ms=-1)
    with pytest.raises(ApprovalError, match="expired"):
        s.verify(old, symbol="BTCUSDT", side="BUY", qty=1, price=100)


def test_direction_mode_and_paused():
    r = rm()
    r.cfg.system.direction = r.cfg.system.direction.LONG
    assert not r.review(decision(direction=Direction.SHORT, entry=100, stop=102), pf(), SPEC, MKT).approved
    assert not r.review(decision(), pf(), SPEC, MKT, trading_paused=True).approved


def test_config_validation_rejects_inconsistent_limits():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=1.0, max_open_risk_pct=0.5)
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=5.0)
