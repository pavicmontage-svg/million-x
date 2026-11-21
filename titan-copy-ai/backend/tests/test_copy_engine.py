"""Copy engine end-to-end tests on the paper broker. Prices here are TEST FIXTURES."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from titan.adapters.base import AdapterUnavailable
from titan.adapters.feeds import PushFeed
from titan.adapters.paper import PaperBroker, SimParams
from titan.copy.engine import CopyEngine
from titan.copy.monitor import flatten, monitor_portfolio, reactivate_portfolio, set_kill_switch
from titan.copy.signals import SignalIn, ingest_signal
from titan.models import (Allocation, Market, Notification, Order, OrderStatus, Portfolio, PortfolioStatus, Position,
                          PositionStatus, Signal, SignalKind, SignalStatus, Trader, TraderStatus)
from titan.services.venues import ensure_default_portfolios

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture()
def env(db):
    feed = PushFeed(max_age_s=3600)
    for sym, b, a in [("EURUSD", 1.08000, 1.08002), ("GBPUSD", 1.27, 1.27003), ("USDJPY", 150.0, 150.003),
                      ("XAUUSD", 2400.0, 2400.3), ("AUDUSD", 0.66, 0.66002), ("EURGBP", 0.85, 0.85002)]:
        feed.update(sym, b, a)
    with db.session_scope() as s:
        fxp, _ = ensure_default_portfolios(s)
        tr = Trader(platform="ctrader", name="Source A", market=Market.FOREX, status=TraderStatus.APPROVED)
        tr2 = Trader(platform="ctrader", name="Source B", market=Market.FOREX, status=TraderStatus.APPROVED)
        s.add_all([tr, tr2])
        s.flush()
        s.add_all([Allocation(portfolio_id=fxp.id, trader_id=tr.id), Allocation(portfolio_id=fxp.id, trader_id=tr2.id)])
        pid, tid, tid2 = fxp.id, tr.id, tr2.id
    broker = PaperBroker("paper_forex", "forex", "EUR", 500.0, feed, SimParams(slippage_bps=0, seed=1),
                         session_factory=db.session_scope)
    eng = CopyEngine({pid: broker})
    return dict(db=db, feed=feed, broker=broker, eng=eng, pid=pid, tid=tid, tid2=tid2)


def send(env, kind="open", pos="P1", sym="EURUSD", side="buy", entry=1.08002, sl=1.07800, tp=1.08500, ts=None,
         trader=None, **kw):
    sig = SignalIn(trader_id=trader or env["tid"], kind=SignalKind(kind), source_position_id=pos, symbol=sym,
                   side=side if kind == "open" else None, entry_price=entry if kind == "open" else None,
                   sl=sl, tp=tp, source_ts=ts or NOW(), **kw)
    with env["db"].session_scope() as s:
        row, created = ingest_signal(s, sig, "test")
        sid = row.id
    return sid, created, env["eng"].process(sid)


def positions(env, status=None):
    with env["db"].session_scope() as s:
        q = select(Position).where(Position.portfolio_id == env["pid"])
        if status:
            q = q.where(Position.status == status)
        return list(s.scalars(q))


def test_open_sizes_by_risk_and_persists(env):
    sid, created, out = send(env)
    assert created and out[0].action == "opened", out
    pos = positions(env)[0]
    assert pos.quantity == 0.01 and pos.sl == 1.078 and pos.is_simulated
    assert 0 < pos.initial_risk <= 2.5          # 0.5% of 500 EUR
    with env["db"].session_scope() as s:
        o = s.scalar(select(Order).where(Order.signal_id == sid))
        assert o.status == OrderStatus.FILLED and o.client_order_id.startswith("tc") and len(o.client_order_id) == 28
        assert s.get(Signal, sid).status == SignalStatus.EXECUTED
        assert s.scalar(select(Notification).where(Notification.kind == "position_opened"))


def test_duplicate_signal_is_idempotent(env):
    ts = NOW()
    _, c1, _ = send(env, ts=ts)
    sid2, c2, out2 = send(env, ts=ts)
    assert c1 and not c2 and out2 == []
    _, _, out3 = send(env, ts=ts + timedelta(seconds=1))   # redelivery with new ts, same source position
    assert out3[0].action == "skipped" and "already copied" in out3[0].reason
    assert len(positions(env)) == 1


def test_stale_signal_rejected(env):
    _, _, out = send(env, ts=NOW() - timedelta(seconds=120))
    assert out[0].action == "rejected" and "stale" in out[0].reason
    assert positions(env) == []


def test_missing_stop_loss_rejected(env):
    _, _, out = send(env, sl=None)
    assert out[0].action == "rejected" and "missing stop-loss" in out[0].reason


def test_late_entry_rejected(env):
    _, _, out = send(env, entry=1.0790, sl=1.0770)   # price already 0.5R above the source entry
    assert out[0].action == "rejected" and "late entry" in out[0].reason


def test_wide_stop_cannot_fit_risk(env):
    _, _, out = send(env, sl=1.07600)   # 40 pips: even 0.01 lots risks ~3.7 EUR > 2.5 EUR budget
    assert out[0].action == "rejected" and "minimum volume" in out[0].reason


def test_minimum_volume_cannot_fit_risk(env):
    _, _, out = send(env, sym="XAUUSD", entry=2400.3, sl=2395.0, tp=None)
    assert out[0].action == "rejected" and "minimum volume" in out[0].reason


def test_modify_tighten_applied_widen_ignored(env):
    send(env)
    _, _, out = send(env, kind="modify", sl=1.0790, tp=None)
    assert out[0].action == "modified"
    assert positions(env)[0].sl == 1.0790
    _, _, out = send(env, kind="modify", sl=1.0700, tp=None)
    assert out[0].action == "rejected" and "widening ignored" in out[0].reason
    assert positions(env)[0].sl == 1.0790


def test_partial_then_full_close(env):
    _, _, out = send(env, sl=1.07900)  # ~10 pip stop: 2.5 EUR budget fits 0.02 lots
    assert out[0].action == "opened", out
    q0 = positions(env)[0].quantity
    assert q0 == pytest.approx(0.02)
    env["feed"].update("EURUSD", 1.0830, 1.08302)
    send(env, kind="partial_close", sl=None, tp=None, close_fraction=0.5)
    p = positions(env)[0]
    assert p.quantity == pytest.approx(0.01) and p.realized_pnl > 0
    send(env, kind="close", sl=None, tp=None)
    p = positions(env)[0]
    assert p.status == PositionStatus.CLOSED and p.realized_pnl > 0 and p.r_multiple > 0


def test_paper_stop_loss_trigger(env):
    send(env)
    env["feed"].update("EURUSD", 1.0779, 1.07792)
    env["eng"].paper_triggers(env["pid"])
    p = positions(env)[0]
    assert p.status == PositionStatus.CLOSED and p.close_reason == "stop_loss"
    assert -2.6 < p.realized_pnl < 0


def test_daily_loss_halts_and_blocks(env):
    env["eng"].adapters[env["pid"]].state["balance"] = 500.0
    monitor_portfolio(env["eng"], env["pid"])            # sets day start equity = 500
    env["broker"].state["balance"] = 489.0               # -2.2% realised today
    r = monitor_portfolio(env["eng"], env["pid"])
    assert r["status"] == "halted_daily"
    _, _, out = send(env)
    assert out[0].action == "rejected" and "halted_daily" in out[0].reason
    with env["db"].session_scope() as s:
        assert s.scalar(select(Notification).where(Notification.kind == "daily_loss_limit"))


def test_max_drawdown_requires_manual_review(env):
    monitor_portfolio(env["eng"], env["pid"])
    env["broker"].state["balance"] = 449.0
    r = monitor_portfolio(env["eng"], env["pid"])
    assert r["status"] == "review_required"
    with env["db"].session_scope() as s:
        p = s.get(Portfolio, env["pid"])
        assert p.copy_enabled is False
    with pytest.raises(PermissionError):
        reactivate_portfolio(env["pid"], "owner", "yes")
    out = reactivate_portfolio(env["pid"], "owner", "REVIEWED Forex Paper")
    assert out["status"] == "active" and out["new_high_water_mark"] == pytest.approx(449.0)


def test_kill_switch_and_flatten(env):
    send(env)
    set_kill_switch(True, "test", "owner")
    _, _, out = send(env, pos="P2", sym="GBPUSD", entry=1.27003, sl=1.268, tp=None, trader=env["tid2"])
    assert out[0].action == "rejected" and "emergency stop" in out[0].reason
    res = flatten(env["eng"], env["pid"], "emergency", "owner")
    assert res[0]["action"] == "closed"
    assert positions(env, PositionStatus.OPEN) == []
    set_kill_switch(False, "test done", "owner")


def test_lost_response_is_reconciled(env):
    broker = env["broker"]
    real_open = broker.open_position

    def open_then_drop(req):
        real_open(req)                       # fills on the venue...
        raise AdapterUnavailable("connection reset")  # ...but the response is lost

    broker.open_position = open_then_drop
    _, _, out = send(env)
    assert out[0].action == "unknown" and positions(env) == []
    broker.open_position = real_open
    with env["db"].session_scope() as s:   # age the order past the in-flight window
        o = s.scalar(select(Order).where(Order.status == OrderStatus.UNKNOWN))
        o.created_at = NOW() - timedelta(minutes=1)
    rep = env["eng"].reconcile(env["pid"])
    assert len(rep["unknown_resolved"]) == 1
    assert positions(env)[0].status == PositionStatus.OPEN


def test_paper_state_survives_restart(env):
    send(env)
    b2 = PaperBroker("paper_forex", "forex", "EUR", 500.0, env["feed"], session_factory=env["db"].session_scope)
    assert len(b2.positions()) == 1 and b2.state["balance"] < 500   # commission was charged


def test_venue_side_close_reconciled(env):
    send(env)
    ref = positions(env)[0].venue_position_ref
    del env["broker"].state["positions"][ref]           # closed outside TITAN
    rep = env["eng"].reconcile(env["pid"])
    assert rep["closed_on_venue"]
    assert positions(env)[0].close_reason == "closed_on_venue"


def test_correlated_usd_exposure_blocks_third_trade(env):
    send(env)                                                   # long EURUSD (short USD)
    _, _, o2 = send(env, pos="P2", sym="GBPUSD", entry=1.27003, sl=1.268, tp=None, trader=env["tid2"])
    assert o2[0].action == "opened", o2
    with env["db"].session_scope() as s:
        t3 = Trader(platform="ctrader", name="Source C", market=Market.FOREX, status=TraderStatus.APPROVED)
        s.add(t3)
        s.flush()
        s.add(Allocation(portfolio_id=env["pid"], trader_id=t3.id))
        tid3 = t3.id
    _, _, o3 = send(env, pos="P3", sym="AUDUSD", entry=0.66002, sl=0.658, tp=None, trader=tid3)
    assert o3[0].action == "rejected" and ("USD" in o3[0].reason or "budget" in o3[0].reason)


def test_rejected_trader_not_copied(env):
    with env["db"].session_scope() as s:
        s.get(Trader, env["tid"]).status = TraderStatus.REJECTED
    _, _, out = send(env)
    assert out[0].action == "rejected" and "trader status" in out[0].reason


def test_concurrent_close_and_reconcile_record_once(env):
    """Regression (found in the live E2E run): reconciler raced a signal close and recorded it twice."""
    import threading
    send(env)
    pos_id = positions(env)[0].id
    errors = []

    def recon():
        for _ in range(20):
            try:
                env["eng"].reconcile(env["pid"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

    t = threading.Thread(target=recon)
    t.start()
    send(env, kind="close", sl=None, tp=None)
    t.join()
    assert not errors
    with env["db"].session_scope() as s:
        p = s.get(Position, pos_id)
        assert p.status == PositionStatus.CLOSED and p.close_reason == "source_closed"
        n = s.scalars(select(Notification).where(Notification.kind == "position_closed")).all()
        assert len(n) == 1


def test_audit_chain_survives_negative_zero_and_nan(db):
    """Regression: JSONB normalises -0.0 (and rejects NaN), which broke hash verification."""
    from titan.audit import audit, verify_chain
    with db.session_scope() as s:
        audit(s, "test", "x", pnl=-0.0, nested={"a": [float("nan"), -0.0, 1.5]}, inf=float("inf"))
    with db.session_scope() as s:
        assert verify_chain(s)["valid"] is True
