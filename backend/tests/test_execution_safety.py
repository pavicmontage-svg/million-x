"""Exchange-simulation tests for execution safety (Phase 1)."""
import pytest

import millionx.engine.execution as exec_mod
import millionx.engine.live as live
import millionx.exchange.paper as paper_mod
from millionx.config import Settings
from millionx.exchange.base import ExchangeError
from millionx.exchange.paper import PaperExchange
from millionx.models import MINUTE_MS, Side, Signal
from millionx.risk import SizingDecision
from millionx.storage import Store

from conftest import FakeMarket


class FaultyExchange(PaperExchange):
    """Paper broker with injectable exchange faults."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.partial_ratio = None
        self.unknown_fill = False
        self.unknown_nofill = False
        self.query_fail = False
        self.fail_stops = False
        self.unverifiable_stops = False
        self.fail_reduce = False
        self.slip_extra = 0.0

    def market_order(self, symbol, side, qty, client_id, reduce_only=False):
        if reduce_only and self.fail_reduce:
            raise ExchangeError("reduce-only rejected (simulated)")
        if not reduce_only and self.unknown_nofill:
            raise ExchangeError("timeout (simulated)", retriable=True)
        if not reduce_only and self.slip_extra:
            ob = self.books.get(symbol)
            if ob:
                ob.asks = [(p * (1 + self.slip_extra), q) for p, q in ob.asks]
                ob.bids = [(p * (1 - self.slip_extra), q) for p, q in ob.bids]
        if not reduce_only and self.partial_ratio:
            r = super().market_order(symbol, side, round(qty * self.partial_ratio, 4), client_id)
            r.status, r.qty = "PARTIALLY_FILLED", qty
            return r
        r = super().market_order(symbol, side, qty, client_id, reduce_only)
        if not reduce_only and self.unknown_fill:
            raise ExchangeError("timeout after send (simulated)", retriable=True)
        return r

    def query_order(self, symbol, client_id):
        if self.unknown_nofill:
            raise ExchangeError("order does not exist", code=-2013)
        if self.query_fail:
            raise ExchangeError("timeout (simulated)", retriable=True)
        return super().query_order(symbol, client_id)

    def conditional_order(self, symbol, side, type_, trigger, client_id, qty=None, close_position=False):
        if self.fail_stops and type_ == "STOP_MARKET":
            raise ExchangeError("stop rejected (simulated)")
        return super().conditional_order(symbol, side, type_, trigger, client_id, qty, close_position)

    def query_conditional(self, symbol, client_id):
        if self.unverifiable_stops:
            raise ExchangeError("unknown (simulated)", retriable=True)
        return super().query_conditional(symbol, client_id)

    def open_conditionals(self, symbol):
        return [] if self.unverifiable_stops else super().open_conditionals(symbol)


@pytest.fixture
def env(tmp_path, clock, monkeypatch):
    for m in (live, paper_mod, exec_mod):
        monkeypatch.setattr(m, "now_ms", clock)
    s = Settings(mode="paper", execution="auto", capital_mode="multi")
    s.multi_capital_eur = 4000.0
    store = Store(str(tmp_path / "x.db"))
    ex = FaultyExchange(FakeMarket(clock), 4000 * s.eur_usdt, s.fees)
    eng = live.TradingEngine(s, store, ex)
    eng.execm.sleep = lambda *_: None
    for _ in range(3):
        clock.t += MINUTE_MS
        eng.tick()
    return eng, ex, store, clock


def make_signal(eng, sym=None, side=Side.LONG, sid="sig1", stop_pct=0.01):
    sym = sym or eng.universe[0]
    px = eng.ex.book_price(sym, "BUY" if side == Side.LONG else "SELL")
    s = int(side)
    return Signal("bot2_ema", sym, side, px, px * (1 - s * stop_pct), px * (1 + s * stop_pct), px * (1 + s * 2 * stop_pct),
                  0.7, 0, 5, id=sid, features={"atr": px * 0.002})


def decision(eng, sig, qty=None):
    q = qty or round(eng.risk.state.equity * 0.004 / abs(sig.entry - sig.stop), 4)
    return SizingDecision(True, qty=q, leverage=1, notional=q * sig.entry, risk_amount=q * abs(sig.entry - sig.stop),
                          risk_fraction=0.005)


def stops_on_exchange(ex, sym):
    return [o for o in ex.open_conditionals(sym) if o["type"] == "STOP_MARKET"]


def test_normal_fill_has_verified_stop(env):
    eng, ex, store, _ = env
    sig = make_signal(eng)
    r = eng._execute(sig, decision(eng, sig), [])
    assert r["ok"], r
    p = eng.positions[sig.symbol]
    assert stops_on_exchange(ex, sig.symbol) and p.exchange_ids["sl_verified_at"]


def test_partial_fill_protects_actual_quantity(env):
    eng, ex, store, _ = env
    ex.partial_ratio = 0.4
    sig = make_signal(eng)
    dec = decision(eng, sig)
    r = eng._execute(sig, dec, [])
    assert r["ok"], r
    p = eng.positions[sig.symbol]
    assert abs(p.qty - round(dec.qty * 0.4, 4)) < 1e-9
    assert abs(ex.position_amount(sig.symbol) - p.qty) < 1e-9
    stops = stops_on_exchange(ex, sig.symbol)
    assert stops and stops[0]["close_position"], "closePosition stop covers the actual filled size"
    assert store.one("SELECT COUNT(*) n FROM incidents WHERE kind='partial_fill'")["n"] == 1


def test_unknown_status_resolved_from_exchange_position(env):
    eng, ex, store, _ = env
    ex.unknown_fill = True
    ex.query_fail = True
    sig = make_signal(eng)
    r = eng._execute(sig, decision(eng, sig), [])
    assert r["ok"], r
    o = store.one("SELECT status FROM orders WHERE purpose='entry'")
    assert o["status"] == "FILLED_VIA_POSITION"
    assert stops_on_exchange(ex, sig.symbol)


def test_unknown_status_not_placed_is_never_resent(env):
    eng, ex, store, _ = env
    ex.unknown_nofill = True
    sig = make_signal(eng)
    r = eng._execute(sig, decision(eng, sig), [])
    assert not r["ok"]
    assert sig.symbol not in eng.positions and ex.position_amount(sig.symbol) == 0
    assert store.one("SELECT status FROM orders WHERE purpose='entry'")["status"] == "NOT_PLACED"
    ex.unknown_nofill = False
    r2 = eng._execute(sig, decision(eng, sig), [])
    assert not r2["ok"] and "duplicate" in r2["error"], "same signal must never be sent twice"


def test_stop_rejected_triggers_verified_close(env):
    eng, ex, store, _ = env
    ex.fail_stops = True
    sig = make_signal(eng)
    r = eng._execute(sig, decision(eng, sig), [])
    assert not r["ok"]
    assert ex.position_amount(sig.symbol) == 0, "unprotectable position must be closed"
    assert sig.symbol not in eng.positions
    assert store.one("SELECT COUNT(*) n FROM incidents WHERE kind='stop_not_verified'")["n"] == 1


def test_stop_unverifiable_triggers_close(env):
    eng, ex, store, _ = env
    ex.unverifiable_stops = True
    sig = make_signal(eng)
    r = eng._execute(sig, decision(eng, sig), [])
    assert not r["ok"] and ex.position_amount(sig.symbol) == 0


def test_close_failure_escalates_and_blocks_entries(env):
    eng, ex, store, clock = env
    ex.fail_stops = True
    ex.fail_reduce = True
    sig = make_signal(eng)
    eng._execute(sig, decision(eng, sig), [])
    assert ex.position_amount(sig.symbol) != 0
    assert eng.risk.state.emergency_stop, "all new entries must be blocked"
    assert store.one("SELECT COUNT(*) n FROM incidents WHERE kind='close_not_verified' AND severity='critical'")["n"] == 1


def test_size_reduced_when_actual_fill_risk_too_high(env):
    eng, ex, store, _ = env
    sig = make_signal(eng, stop_pct=0.004)
    dec = decision(eng, sig, qty=round(eng.risk.state.equity * 0.009 / abs(sig.entry - sig.stop), 4))
    eng.risk.limits.max_price_drift_bps = 50
    r = eng._execute(sig, dec, [])
    if r["ok"]:
        p = eng.positions[sig.symbol]
        assert p.risk_amount <= eng.risk.state.equity * eng.risk.risk_fraction() * 1.3
    else:
        assert "risk at current price" in r["error"], r


def test_reconcile_adopts_orphan_and_restores_missing_stop(env):
    eng, ex, store, clock = env
    sym = eng.universe[1]
    ex.market_order(sym, "SELL", 1.0, "manual-outside-engine")  # opened outside the engine
    rep = eng.execm.reconcile()
    assert sym in rep["adopted"] and eng.positions[sym].bot_id == "orphan"
    assert stops_on_exchange(ex, sym)
    # someone cancels the stop on the exchange -> next reconciliation restores it
    for o in stops_on_exchange(ex, sym):
        ex.cancel_conditional(sym, o["client_id"])
    rep = eng.execm.reconcile()
    assert sym in rep["stop_restored"] and stops_on_exchange(ex, sym)
    # position closed outside -> finalized internally
    ex.market_order(sym, "BUY", 1.0, "manual-close", reduce_only=True)
    rep = eng.execm.reconcile()
    assert sym in rep["finalized"] and sym not in eng.positions


def test_startup_reconciliation_after_restart(env, tmp_path):
    eng, ex, store, clock = env
    sig = make_signal(eng)
    assert eng._execute(sig, decision(eng, sig), [])["ok"]
    # simulate a crash: new engine instance on the same DB and exchange
    eng2 = live.TradingEngine(eng.s, store, ex)
    eng2.execm.sleep = lambda *_: None
    assert sig.symbol in eng2.positions, "open position restored from the database"
    clock.t += MINUTE_MS
    eng2.tick()
    assert eng2.started_reconcile and sig.symbol in eng2.positions
    assert stops_on_exchange(ex, sig.symbol)


def test_emergency_stop_closes_everything_on_exchange(env):
    eng, ex, store, _ = env
    sig = make_signal(eng)
    assert eng._execute(sig, decision(eng, sig), [])["ok"]
    other = eng.universe[1]
    ex.market_order(other, "BUY", 0.5, "outside")
    r = eng.emergency_stop("test")
    assert r["ok"] and not r["not_flat"]
    assert ex.positions() == {}
    assert store.kv_get("emergency_stop") is True


def test_manual_approval_expires(env):
    eng, ex, store, clock = env
    eng.s.execution = "manual"
    from millionx.engine.live import PendingApproval
    sig = make_signal(eng, sid="appr1")
    eng.pending[sig.id] = PendingApproval(sig, decision(eng, sig), clock.t, clock.t + 90_000)
    clock.t += 91_000
    r = eng.approve(sig.id, True, "owner:test")
    assert not r["ok"] and "expired" in r["error"]
    assert ex.position_amount(sig.symbol) == 0


def test_approval_rechecks_price_drift(env):
    eng, ex, store, clock = env
    from millionx.engine.live import PendingApproval
    sig = make_signal(eng, sid="appr2")
    sig.entry *= 0.995  # market moved 50 bps against us since the signal
    eng.pending[sig.id] = PendingApproval(sig, decision(eng, sig), clock.t, clock.t + 90_000)
    r = eng.approve(sig.id, True, "owner:test")
    assert not r["ok"] and ("drift" in r["error"] or "reward/risk" in r["error"]), r
    assert ex.position_amount(sig.symbol) == 0
    # small move within limits: re-sized at the current price and executed
    sig2 = make_signal(eng, sym=eng.universe[1], sid="appr3", stop_pct=0.02)
    eng.pending[sig2.id] = PendingApproval(sig2, decision(eng, sig2), clock.t, clock.t + 90_000)
    r2 = eng.approve(sig2.id, True, "owner:test")
    assert r2["ok"], r2


def test_governance_blocks_order_frequency(env):
    eng, ex, store, _ = env
    eng.risk.limits.max_orders_per_minute = 1
    s1 = make_signal(eng, sym=eng.universe[0], sid="f1")
    s2 = make_signal(eng, sym=eng.universe[1], sid="f2")
    assert eng._execute(s1, decision(eng, s1), [])["ok"]
    r = eng._execute(s2, decision(eng, s2), [])
    assert not r["ok"] and "order frequency" in r["error"]


def test_live_mode_blocked_without_gate(env):
    eng, ex, store, _ = env
    eng.s.mode = "live"
    sig = make_signal(eng, sid="live1")
    r = eng._execute(sig, decision(eng, sig), [])
    assert not r["ok"] and "live authorisation" in r["error"]
    eng.s.mode = "paper"


def test_real_balance_checked_before_every_order(tmp_path, clock, monkeypatch):
    """Demo/live: the order is blocked when Binance's available balance cannot carry the margin."""
    import millionx.engine.live as live
    import millionx.engine.execution as exec_mod
    import millionx.exchange.paper as paper_mod
    from millionx.config import Settings
    from millionx.exchange.paper import PaperExchange
    from millionx.models import MINUTE_MS, Side, Signal
    from millionx.risk import SizingDecision
    from millionx.storage import Store
    from conftest import FakeMarket
    for m in (live, exec_mod, paper_mod):
        monkeypatch.setattr(m, "now_ms", clock)
    s = Settings(mode="demo", execution="auto")
    s.min_depth_usdt = 1.0
    s.risk.min_depth_usdt = 1.0
    s.risk.max_spread_bps = 5
    px = PaperExchange(FakeMarket(clock), 540.0, s.fees)
    eng = live.TradingEngine(s, Store(str(tmp_path / "b.db")), px)
    for _ in range(5):
        clock.t += MINUTE_MS
        eng.tick()

    class Real:  # behaves like Binance: not paper, small available balance
        is_paper = False

        def __getattr__(self, k):
            return getattr(px, k)

        def balance(self):
            return {"equity": 540.0, "wallet": 540.0, "available": 3.0, "unrealized": 0.0}
    eng.ex = Real()
    sym = eng.universe[0]
    p = float(eng.bars[sym].close[-1])
    sig = Signal("bot2_ema", sym, Side.LONG, p, p * 0.99, p * 1.02, p * 1.03, 0.7, clock.t, 5)
    dec = SizingDecision(True, qty=round(400 / p, 3), leverage=5, notional=400.0, risk_amount=2.7,
                         risk_fraction=0.005, expected_cost=0.6)
    v, _ = eng.execm.pretrade(sig, dec)
    assert any("available balance" in x for x in v), v
