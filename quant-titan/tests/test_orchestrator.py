import pytest

from quanttitan.agents import ExecutionSpecialist, PortfolioManager
from quanttitan.config import AppConfig, ExchangeEnv
from quanttitan.core.types import Direction, Outcome, Regime
from quanttitan.engines import build_engines
from quanttitan.orchestrator.decision import Orchestrator
from quanttitan.orchestrator.edge import EdgeBook
from quanttitan.orchestrator.pipeline import DecisionPipeline
from quanttitan.risk.manager import RiskManager
from quanttitan.risk.portfolio import PortfolioState

from helpers import DAY0, SPEC, regime, signal, view


def orch(**oc):
    cfg = AppConfig()
    for k, v in oc.items():
        setattr(cfg.orchestrator, k, v)
    return Orchestrator(cfg, EdgeBook(cfg.orchestrator.edge_prior_strength)), cfg


def test_unsafe_regime_blocks_new_risk():
    o, _ = orch()
    for reg in (Regime.UNSAFE_MARKET, Regime.MARKET_DISLOCATION, Regime.UNKNOWN):
        d = o.propose(view({"vwap": signal("vwap", conf=99)}, regime(reg)))
        assert d.outcome is Outcome.WAIT


def test_ineligible_engine_cannot_drive_a_trade():
    o, _ = orch()
    # VWAP mean reversion is not eligible in a STRONG_TREND regime
    d = o.propose(view({"vwap": signal("vwap", conf=95)}, regime(Regime.STRONG_TREND)))
    assert d.outcome is Outcome.WAIT
    assert "not eligible" in d.engine_views["vwap"]
    d2 = o.propose(view({"vwap": signal("vwap", conf=95)}, regime(Regime.RANGE)))
    assert d2.outcome is Outcome.APPROVE_LONG and d2.engine == "vwap"


def test_no_signal_means_wait_not_forced_trade():
    o, _ = orch()
    d = o.propose(view({}, regime(Regime.RANGE)))
    assert d.outcome is Outcome.WAIT and "no forced trades" in d.reasons[0]


def test_conflict_waits_unless_margin():
    o, _ = orch(conflict_margin=25)
    reg = regime(Regime.WEAK_TREND)  # trend + vwap eligible
    long_v = signal("vwap", Direction.LONG, conf=70)
    short_t = signal("trend", Direction.SHORT, conf=75, entry=100, stop=102, tp1=96)
    assert o.propose(view({"vwap": long_v, "trend": short_t}, reg)).outcome is Outcome.WAIT
    short_t.confidence = 99
    long_v.confidence = 60
    d = o.propose(view({"vwap": long_v, "trend": short_t}, reg))
    assert d.outcome is Outcome.APPROVE_SHORT and d.engine == "trend"


def test_order_flow_opposition_rejects_and_support_boosts():
    o, cfg = orch()
    oppose = lambda d: (10.0, 90.0)
    d = o.propose(view({"vwap": signal("vwap", conf=80)}, regime(Regime.RANGE), assess=oppose))
    assert d.outcome is Outcome.REJECT and any("order flow opposes" in r for r in d.reasons)
    support = lambda d: (100.0, 0.0)
    d2 = o.propose(view({"vwap": signal("vwap", conf=80)}, regime(Regime.RANGE), assess=support))
    assert d2.outcome is Outcome.APPROVE_LONG and d2.confidence == pytest.approx(80 + cfg.engines.order_flow.confirm_boost)
    o2, _ = orch(orderflow_confirmation="required")
    d3 = o2.propose(view({"vwap": signal("vwap", conf=80)}, regime(Regime.RANGE), assess=lambda d: (30.0, 20.0)))
    assert d3.outcome is Outcome.WAIT


def test_costs_too_high_relative_to_stop_rejected():
    o, _ = orch()
    tight = signal("vwap", entry=100, stop=99.95, tp1=100.2)  # 0.05% stop: fees alone ~2R
    d = o.propose(view({"vwap": tight}, regime(Regime.RANGE)))
    assert d.outcome is Outcome.REJECT and any("costs" in r for r in d.reasons)


def test_live_requires_validated_positive_edge():
    o, cfg = orch(min_validated_trades=30)
    cfg.execution.exchange_env = ExchangeEnv.LIVE
    v = view({"vwap": signal("vwap", setup="VWAP_REVERSION")}, regime(Regime.RANGE))
    d = o.propose(v)
    assert d.outcome is Outcome.WAIT and any("insufficient validated evidence" in r for r in d.reasons)
    for _ in range(60):
        o.edge.record("vwap", "VWAP_REVERSION", "LONG", -0.2, "backtest_oos")
    d = o.propose(view({"vwap": signal("vwap", setup="VWAP_REVERSION")}, regime(Regime.RANGE)))
    assert d.outcome is Outcome.REJECT
    o.edge._r.clear()
    for _ in range(60):
        o.edge.record("vwap", "VWAP_REVERSION", "LONG", 0.4, "paper")
    d = o.propose(view({"vwap": signal("vwap", setup="VWAP_REVERSION")}, regime(Regime.RANGE)))
    assert d.outcome is Outcome.APPROVE_LONG and d.net_edge_r == pytest.approx(60 * 0.4 / 90)


def test_edge_book_rejects_in_sample_evidence_and_shrinks():
    e = EdgeBook(prior_strength=30)
    with pytest.raises(ValueError):
        e.record("vwap", "X", "LONG", 1.0, "backtest_in_sample")
    for _ in range(3):
        e.record("vwap", "X", "LONG", 2.0, "paper")
    est = e.estimate("vwap", "X", "LONG")
    assert est.mean_r == 2.0 and est.shrunk_r == pytest.approx(6 / 33)


def test_explanation_structure():
    o, _ = orch()
    d = o.propose(view({"vwap": signal("vwap", conf=82)}, regime(Regime.RANGE), assess=lambda d: (80.0, 10.0)))
    for line in ("INSTRUMENT: BTCUSDT", "REGIME: RANGE", "VWAP ENGINE: LONG", "TREND ENGINE: NEUTRAL",
                 "BREAKOUT ENGINE: NEUTRAL", "FINAL DECISION: LONG", "REASON:"):
        assert line in d.explanation, d.explanation


def test_alternative_policies_are_selectable():
    o, cfg = orch(policy="majority_vote")
    reg = regime(Regime.STRONG_TREND)
    d = o.propose(view({"vwap": signal("vwap", conf=80), "breakout": signal("breakout", conf=70)}, reg))
    assert d.outcome is Outcome.APPROVE_LONG  # majority vote ignores regime eligibility of VWAP
    o2, _ = orch(policy="confidence_average")
    d2 = o2.propose(view({"vwap": signal("vwap", conf=40)}, regime(Regime.RANGE)))
    assert d2.outcome is Outcome.WAIT  # avg 10 < 15


def _pipeline(cfg=None):
    cfg = cfg or AppConfig()
    edge = EdgeBook()
    risk = RiskManager(cfg)
    return DecisionPipeline(cfg, build_engines(cfg), Orchestrator(cfg, edge), risk, PortfolioManager(cfg), ExecutionSpecialist(cfg))


def test_pipeline_risk_veto_cannot_be_overridden():
    p = _pipeline()
    pf = PortfolioState(10_000.0); pf.roll(DAY0)
    v = view({"vwap": signal("vwap", conf=100), "trend": signal("trend", conf=100)}, regime(Regime.WEAK_TREND), assess=lambda d: (100.0, 0.0))
    p.risk.breakers.emergency_stop("test")
    out = p.decide([v], pf, {"BTCUSDT": SPEC})
    assert len(out) == 1 and out[0].outcome is Outcome.BLOCK_ALL_TRADING
    p.risk.breakers.manual_reset("EMERGENCY_STOP", confirm=True)
    pf.balance = 9_000  # daily loss breaker
    out = p.decide([v], pf, {"BTCUSDT": SPEC})
    d = out[0]
    assert d.outcome is Outcome.REJECT and d.risk_verdict == "VETO" and d.approval is None


def test_pipeline_approves_with_signed_approval_and_dedups_correlated():
    p = _pipeline()
    pf = PortfolioState(10_000.0); pf.roll(DAY0)
    pf.correlations[("BTCUSDT", "ETHUSDT")] = 0.95
    v1 = view({"vwap": signal("vwap", conf=90, symbol="BTCUSDT")}, regime(Regime.RANGE, symbol="BTCUSDT"), symbol="BTCUSDT")
    v2 = view({"vwap": signal("vwap", conf=70, symbol="ETHUSDT")}, regime(Regime.RANGE, symbol="ETHUSDT"), symbol="ETHUSDT")
    out = p.decide([v1, v2], pf, {"BTCUSDT": SPEC, "ETHUSDT": SPEC})
    by = {d.symbol: d for d in out}
    assert by["BTCUSDT"].outcome is Outcome.APPROVE_LONG and by["BTCUSDT"].approval["sig"]
    assert by["BTCUSDT"].qty > 0 and "RISK MANAGER: APPROVED" in by["BTCUSDT"].explanation
    assert by["ETHUSDT"].outcome is Outcome.REJECT and any("duplicates" in r for r in by["ETHUSDT"].reasons)
    assert pf.positions == []  # provisional positions are removed after the batch


def test_pipeline_batch_respects_open_risk_across_simultaneous_signals():
    cfg = AppConfig()
    cfg.risk.max_open_risk_pct = 0.9  # room for one 0.5% trade only
    p = _pipeline(cfg)
    pf = PortfolioState(10_000.0); pf.roll(DAY0)
    views = [view({"vwap": signal("vwap", conf=90 - k, symbol=s)}, regime(Regime.RANGE, symbol=s), symbol=s)
             for k, s in enumerate(["AUSDT", "BUSDT"])]
    out = p.decide(views, pf, {})
    outcomes = sorted(d.outcome.value for d in out)
    assert outcomes == ["APPROVE_LONG", "REJECT"]
