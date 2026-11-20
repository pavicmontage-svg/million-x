"""End-to-end paper trading engine on a deterministic fake market."""
import os

import pytest

import millionx.engine.live as live
import millionx.exchange.paper as paper_mod
import millionx.engine.execution as exec_mod
from millionx.config import Settings
from millionx.exchange.paper import PaperExchange
from millionx.models import BotStatus, MINUTE_MS
from millionx.storage import Store

from conftest import FakeMarket


@pytest.fixture
def engine(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(live, "now_ms", clock)
    monkeypatch.setattr(paper_mod, "now_ms", clock)
    monkeypatch.setattr(exec_mod, "now_ms", clock)
    s = Settings(mode="paper", execution="auto", capital_mode="multi")
    s.min_depth_usdt = 1.0
    s.max_spread_bps = 5
    s.multi_capital_eur = 4000.0  # large enough to pass minimums in tests
    store = Store(str(tmp_path / "t.db"))
    market = FakeMarket(clock)
    ex = PaperExchange(market, 4000 * s.eur_usdt, s.fees)
    eng = live.TradingEngine(s, store, ex)
    return eng, clock, market, store


def run_minutes(eng, clock, n):
    for _ in range(n):
        clock.t += MINUTE_MS
        eng.tick()


def test_engine_trades_with_protection(engine):
    eng, clock, market, store = engine
    run_minutes(eng, clock, 1200)
    assert eng.universe
    orders = store.q("SELECT * FROM orders")
    entries = [o for o in orders if o["purpose"] == "entry"]
    virt = store.q("SELECT COUNT(*) n FROM trades WHERE virtual=1")[0]["n"]
    assert virt > 0, "shadow competition must record trades"
    for p in eng.positions.values():
        assert "sl" in p.exchange_ids, "every open position has an exchange-side stop"
    # every filled entry got a stop order in the paper broker or was already closed
    audit = store.q("SELECT action FROM audit WHERE action IN ('trade_decision','position_opened')")
    assert audit, "decisions are logged"
    assert store.verify_audit()["ok"]
    assert entries, "the fake market must produce at least one real (paper) entry"
    assert all(o["status"] in ("FILLED", "REJECTED_PRETRADE") for o in entries), [o["status"] for o in entries]


def test_stale_data_blocks_entries(engine):
    eng, clock, market, store = engine
    run_minutes(eng, clock, 5)
    market.fail = True
    run_minutes(eng, clock, 5)
    assert not eng.risk.state.connection_ok or not eng.risk.state.data_fresh
    ok, why = eng.risk.can_open(clock.t)
    assert not ok


def test_emergency_stop_flattens_and_persists(engine):
    eng, clock, market, store = engine
    run_minutes(eng, clock, 200)
    eng.emergency_stop("test")
    assert not eng.positions
    assert store.kv_get("emergency_stop") is True
    before = store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry'")["n"]
    run_minutes(eng, clock, 60)
    after = store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry'")["n"]
    assert before == after, "no entries after emergency stop"


def test_duplicate_entry_prevented(engine):
    eng, clock, market, store = engine
    run_minutes(eng, clock, 3)
    from millionx.models import Side, Signal
    from millionx.risk import SizingDecision
    sym = eng.universe[0]
    px = float(eng.bars[sym].close[-1])
    s = Signal("bot2_ema", sym, Side.LONG, px, px * 0.99, px * 1.01, px * 1.02, 0.7, clock.t, 5)
    d = SizingDecision(True, qty=0.1, leverage=1, notional=0.1 * px, risk_amount=1.0)
    r1 = eng._execute(s, d, [])
    eng.positions.clear()
    r2 = eng._execute(s, d, [])
    assert r1["ok"] and not r2["ok"] and "duplicate" in r2["error"]


def test_manual_mode_waits_for_owner(engine):
    eng, clock, market, store = engine
    eng.s.execution = "manual"
    run_minutes(eng, clock, 300)
    entries = store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry'")["n"]
    assert entries == 0, "manual mode must not place orders without approval"


def test_live_mode_requires_unlock(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(live, "now_ms", clock)
    s = Settings(mode="live", execution="auto")
    eng = live.TradingEngine(s, Store(str(tmp_path / "l.db")), FakeMarket(clock))
    assert eng.sup.tradable == {BotStatus.LIVE_ELIGIBLE}
    assert not eng._decide.__self__.gate  # no gate object -> live orders impossible


def test_universe_ranked_by_real_market_volume(engine):
    eng, clock, market, store = engine
    from millionx.exchange.base import ExchangeError

    class Real:
        def tickers_24h(self):  # real market: BBB trades far more than AAA; ZZZ is not listed on the venue
            return [{"symbol": "ZZZUSDT", "quoteVolume": "9e12"}, {"symbol": "AAAUSDT", "quoteVolume": "1"},
                    {"symbol": "BBBUSDT", "quoteVolume": "5e9"}]

    eng.volume_source = Real()
    eng.refresh_universe()
    assert eng.universe[:2] == ["BBBUSDT", "AAAUSDT"] and "ZZZUSDT" not in eng.universe
    assert eng.volume_source_used == "binance_real_market"

    class Down:
        def tickers_24h(self):
            raise ExchangeError("access denied from this location/IP (451)", code=451)

    eng.volume_source = Down()
    eng.refresh_universe()
    assert eng.universe[0] == "AAAUSDT" and eng.volume_source_used == "venue"  # falls back, never blocks
