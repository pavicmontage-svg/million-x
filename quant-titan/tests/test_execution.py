import pytest

from quanttitan.config import AppConfig
from quanttitan.core.types import ContractSpec, Decision, Direction, EngineSignal, Outcome, TargetLevel, new_id
from quanttitan.data.synthetic import synthetic_book
from quanttitan.execution.exchange import ExchangeError, OrderRequest
from quanttitan.execution.order_manager import OrderManager
from quanttitan.execution.paper import PaperExchange
from quanttitan.execution.position_manager import PositionManager
from quanttitan.execution.reconciler import Reconciler
from quanttitan.risk.approval import ApprovalError, ApprovalSigner
from quanttitan.risk.breakers import CircuitBreakers

SPEC = ContractSpec("BTCUSDT", tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5)


def setup(price=100.0, balance=10_000.0):
    cfg = AppConfig()
    ex = PaperExchange(balance, cfg.costs, {"BTCUSDT": SPEC})
    ex.update_market("BTCUSDT", last=price, book=synthetic_book(price, tick=0.1, depth=50), ts=1)
    signer = ApprovalSigner()
    om = OrderManager(ex, signer, poll_interval=0.0, fill_timeout=0.05)
    alerts = []
    closed = []
    pm = PositionManager(cfg, ex, om, {"BTCUSDT": SPEC}, on_closed=closed.append, on_alert=lambda *a: alerts.append(a))
    return cfg, ex, signer, om, pm, alerts, closed


def approved_decision(signer, qty=1.0, entry=100.0, stop=98.0, direction=Direction.LONG, trail=0.0):
    d = Decision(new_id("dec_"), 1, "BTCUSDT", Outcome.APPROVE_LONG, engine="vwap", setup="T", direction=direction,
                 entry=entry, stop=stop, qty=qty,
                 targets=[TargetLevel(entry + 2 * direction.sign, 0.5), TargetLevel(entry + 4 * direction.sign, 1.0)])
    d.signal = EngineSignal("vwap", "BTCUSDT", direction, 80, entry=entry, stop=stop, targets=d.targets, trail_atr=trail, atr=1.0)
    d.approval = signer.issue(decision_id=d.id, symbol="BTCUSDT", side="BUY" if direction is Direction.LONG else "SELL",
                              max_qty=qty, ref_price=entry, max_deviation_pct=1.0)
    return d


async def test_order_without_approval_is_refused():
    _, ex, signer, om, *_ = setup()
    with pytest.raises(ApprovalError):
        await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtA"), ref_price=100)
    assert ex.fills == []
    # reduce-only orders do not need approval (they can only lower risk)
    info = await om.submit(OrderRequest("BTCUSDT", "SELL", "MARKET", 1.0, reduce_only=True, client_id="qtB"), ref_price=100)
    assert info.status == "EXPIRED" and ex.fills == []  # nothing to reduce


async def test_duplicate_submission_is_idempotent():
    _, ex, signer, om, *_ = setup()
    tok = signer.issue(decision_id="d1", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    a = await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtD1E"), approval=tok, ref_price=100)
    assert a.status == "FILLED"
    # the same approval cannot open a second position
    with pytest.raises(ApprovalError):
        await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtD1E2"), approval=tok, ref_price=100)
    assert len(ex.fills) == 1


async def test_ambiguous_timeout_is_resolved_by_query_not_resend():
    _, ex, signer, om, *_ = setup()
    ex.inject_failures("timeout_applied")  # order reached the exchange, response lost
    tok = signer.issue(decision_id="d2", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    info = await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtD2E"), approval=tok, ref_price=100)
    assert info.status == "FILLED"
    assert len(ex.fills) == 1  # not duplicated
    assert any(e["event"] == "recovered_after_ambiguous_failure" for e in om.log)


async def test_lost_request_is_retried_once_safely():
    _, ex, signer, om, *_ = setup()
    ex.inject_failures("timeout_lost")
    tok = signer.issue(decision_id="d3", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    info = await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtD3E"), approval=tok, ref_price=100)
    assert info.status == "FILLED" and len(ex.fills) == 1


async def test_rejection_is_not_retried():
    _, ex, signer, om, *_ = setup()
    ex.inject_failures("reject")
    tok = signer.issue(decision_id="d4", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    with pytest.raises(ExchangeError):
        await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtD4E"), approval=tok, ref_price=100)
    assert ex.fills == []


async def test_open_places_protective_stop_and_tp_then_stop_closes():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    pos = await pm.open(approved_decision(signer), ref_price=100.0)
    assert pos is not None and pos.qty == pytest.approx(1.0)
    m = pm.managed[pos.id]
    stop = ex.orders[m.stop_id]
    assert stop.type == "STOP_MARKET" and stop.status == "NEW" and stop.reduce_only
    assert len(m.tp_ids) == 2
    ex.update_market("BTCUSDT", last=97.5, book=synthetic_book(97.5, tick=0.1, depth=50), ts=2)
    trades = await pm.sync()
    assert len(trades) == 1 and trades[0].exit_reason == "stop_loss"
    assert trades[0].net_pnl < 0 and trades[0].r_multiple == pytest.approx(-1.0, abs=0.35)
    assert (await ex.positions()) == []
    assert all(ex.orders[c].is_terminal for c in m.tp_ids)  # leftover TPs cancelled


async def test_partial_tp_moves_stop_to_breakeven_then_target():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    pos = await pm.open(approved_decision(signer), ref_price=100.0)
    ex.update_market("BTCUSDT", last=102.2, book=synthetic_book(102.2, tick=0.1), ts=2)
    await pm.sync()
    assert pos.tp_hits == 1 and pos.qty == pytest.approx(0.5)
    assert pos.stop == pytest.approx(SPEC.round_price(pos.entry_price))
    ex.update_market("BTCUSDT", last=104.5, book=synthetic_book(104.5, tick=0.1), ts=3)
    trades = await pm.sync()
    assert trades and trades[0].exit_reason.startswith("take_profit") and trades[0].net_pnl > 0


async def test_stop_placement_failure_flattens_immediately():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    d = approved_decision(signer)
    real = ex.place_order

    async def fail_stops(req):
        if req.type == "STOP_MARKET":
            raise ExchangeError("simulated stop rejection", code=-2021)
        return await real(req)

    ex.place_order = fail_stops
    pos = await pm.open(d, ref_price=100.0)
    assert pos is None
    assert (await ex.positions()) == []
    assert any(a[0] == "risk_violation" and a[1] == "critical" for a in alerts)


async def test_trailing_stop_ratchets_and_time_stop():
    cfg, ex, signer, om, pm, *_ = setup()
    d = approved_decision(signer, trail=2.0)
    d.signal.time_stop_bars = 5
    pos = await pm.open(d, ref_price=100.0)
    ex.update_market("BTCUSDT", last=102.5, book=synthetic_book(102.5, tick=0.1), ts=2)
    await pm.sync()  # TP1 hit
    old = pos.stop
    await pm.on_bar("BTCUSDT", 106.0, 102.0, 105.5, atr=1.0)
    assert pos.stop > old and pos.stop == pytest.approx(104.0)
    for _ in range(5):
        trades = await pm.on_bar("BTCUSDT", 105.6, 105.0, 105.4, atr=1.0)
        if trades:
            break
    assert trades and trades[0].exit_reason == "time_stop"


async def test_reconciler_detects_unexpected_position_and_blocks():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    ex._execute("BTCUSDT", "BUY", 0.5, 100.0, taker=True, cid="manual", reduce=False, otype="MARKET")  # opened outside the bot
    br = CircuitBreakers(cfg.risk)
    rec = Reconciler(cfg, pm, om, br, on_alert=lambda *a: alerts.append(a))
    rep = await rec.run()
    assert not rep.ok and "SAFE_STATE" in br.active and om.blocked_reason
    assert any("unexpected position" in i for i in rep.issues)
    assert any(o.type == "STOP_MARKET" for o in await ex.open_orders("BTCUSDT"))  # protect policy
    tok = signer.issue(decision_id="dX", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    with pytest.raises(ExchangeError, match="blocked"):
        await om.submit(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtX"), approval=tok, ref_price=100)


async def test_reconciler_replaces_missing_stop_and_clears_orphans():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    pos = await pm.open(approved_decision(signer), ref_price=100.0)
    m = pm.managed[pos.id]
    await ex.cancel_order("BTCUSDT", m.stop_id, True)  # stop vanished on the exchange
    await ex.place_order(OrderRequest("BTCUSDT", "SELL", "TAKE_PROFIT_MARKET", 0.1, stop_price=150, reduce_only=True, client_id="qtorphan"))
    rec = Reconciler(cfg, pm, om, CircuitBreakers(cfg.risk))
    rep = await rec.run()
    assert rep.ok, rep.issues
    assert ex.orders[m.stop_id].status == "NEW"
    assert ex.orders["qtorphan"].status == "CANCELED"


async def test_exchange_outage_blocks_after_repeated_failures():
    cfg, ex, signer, om, pm, alerts, closed = setup()
    br = CircuitBreakers(cfg.risk)
    rec = Reconciler(cfg, pm, om, br, on_alert=lambda *a: alerts.append(a))
    ex.outage = True
    for _ in range(3):
        rep = await rec.run()
    assert not rep.ok and "SAFE_STATE" in br.active
    ex.outage = False
    rep = await rec.run()
    assert rep.ok and "SAFE_STATE" not in br.active and om.blocked_reason is None


async def test_paper_partial_fill_limit_ioc_and_fees():
    cfg, ex, signer, om, pm, *_ = setup()
    book = {"bids": [[99.9, 1.0]], "asks": [[100.1, 0.3], [100.2, 0.3], [101.0, 5.0]], "ts": 1}
    ex.update_market("BTCUSDT", last=100.0, book=book, ts=5)
    tok = signer.issue(decision_id="d9", symbol="BTCUSDT", side="BUY", max_qty=1, ref_price=100, max_deviation_pct=1)
    info = await om.submit(OrderRequest("BTCUSDT", "BUY", "LIMIT", 1.0, price=100.2, tif="IOC", client_id="qtD9E"), approval=tok, ref_price=100)
    assert info.filled_qty == pytest.approx(0.6) and info.status == "EXPIRED"
    assert info.avg_price == pytest.approx(100.15)
    assert info.fee == pytest.approx(0.6 * 100.15 * cfg.costs.taker_fee)


async def test_paper_funding_and_gap_through_stop():
    cfg, ex, signer, om, pm, *_ = setup()
    pos = await pm.open(approved_decision(signer), ref_price=100.0)
    w0 = ex.wallet
    ex.update_market("BTCUSDT", last=100.0, ts=10, funding_rate=0.001, next_funding_ms=20)
    ex.update_market("BTCUSDT", last=100.0, ts=21, next_funding_ms=28_800_021)
    assert ex.wallet == pytest.approx(w0 - 1.0 * 100.0 * 0.001)
    ex.update_market("BTCUSDT", last=95.0, book=synthetic_book(95.0, tick=0.1), ts=30)  # gap through 98 stop
    trades = await pm.sync()
    assert trades and trades[0].exit_price < 98.0
