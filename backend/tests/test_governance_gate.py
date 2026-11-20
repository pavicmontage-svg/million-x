"""Risk governance bounds and the live trading gate (Phases 2-3)."""
import pytest

import millionx.live_gate as lg
from millionx.config import RiskLimits, Settings, load_settings
from millionx.governance import BOUNDS, PreTradeContext, apply_limit_changes, pre_trade, validate_limits
from millionx.security import Vault, store_api_keys
from millionx.storage import Store


def test_every_limit_has_bounds_and_defaults_are_valid():
    L = RiskLimits()
    for k, v in L.__dict__.items():
        from millionx.governance import STR_FIELDS
        assert k in BOUNDS or isinstance(v, bool) or k in STR_FIELDS, f"{k} has no hard bounds"
    assert validate_limits(L.__dict__) == []


@pytest.mark.parametrize("key,bad", [("risk_per_trade", 0.05), ("max_leverage", 21), ("max_daily_loss", 0.5),
                                     ("max_total_drawdown", 0.9), ("max_orders_per_minute", 100),
                                     ("max_slippage_bps", 500), ("min_depth_usdt", 0), ("max_unlock_hours", 100),
                                     ("risk_per_trade", float("nan"))])
def test_out_of_bounds_values_are_refused(key, bad):
    L = RiskLimits()
    before = dict(L.__dict__)
    with pytest.raises(ValueError):
        apply_limit_changes(L, {key: bad})
    assert L.__dict__ == before, "rejected change must not partially apply"


def test_cross_field_rules():
    assert validate_limits({"drawdown_risk_scale_start": 0.2, "max_total_drawdown": 0.1})
    assert validate_limits({"max_orders_per_minute": 10, "max_orders_per_hour": 5})


def test_config_file_cannot_bypass_bounds(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("risk:\n  max_leverage: 50\n")
    with pytest.raises(ValueError):
        load_settings(str(p))


def _ctx(**kw):
    base = dict(symbol="ETHUSDT", side=1, qty=0.01, leverage=2, signal_price=100.0, stop=99.0, tp1=101.0,
                exec_price=100.0, equity=100.0, risk_fraction=0.005, taker_fee=0.0005, exchange_positions={},
                spread_bps=1.0, depth_usdt=1e6, entries_last_minute=0, entries_last_hour=0, data_fresh=True,
                connection_ok=True, emergency=False, live_mode=False, live_gate_ok=False)
    base.update(kw)
    return PreTradeContext(**base)


def test_pre_trade_checks():
    L = RiskLimits()
    assert pre_trade(L, _ctx()) == []
    assert any("drift" in v for v in pre_trade(L, _ctx(exec_price=100.2)))
    assert any("spread" in v for v in pre_trade(L, _ctx(spread_bps=10)))
    assert any("depth" in v for v in pre_trade(L, _ctx(depth_usdt=10)))
    assert any("stale" in v for v in pre_trade(L, _ctx(data_fresh=False)))
    assert any("live authorisation" in v for v in pre_trade(L, _ctx(live_mode=True)))
    assert any("aggregate exposure" in v for v in pre_trade(L, _ctx(qty=4.0, stop=99.99)))
    assert any("already holds" in v for v in pre_trade(L, _ctx(exchange_positions={"ETHUSDT": (1.0, 100.0)})))
    many = {f"S{i}USDT": (0.001, 100.0) for i in range(3)}
    assert any("open positions" in v for v in pre_trade(L, _ctx(exchange_positions=many)))
    corr = {"BTCUSDT": (1.6, 100.0)}
    assert any("correlated" in v for v in pre_trade(L, _ctx(exchange_positions=corr, corr=lambda a, b: 0.9)))
    assert any("leverage" in v for v in pre_trade(L, _ctx(leverage=50)))
    assert any("frequency" in v for v in pre_trade(L, _ctx(entries_last_minute=3)))
    assert any("beyond stop" in v for v in pre_trade(L, _ctx(exec_price=98.9, signal_price=98.9)))


class FakeLive:
    def __init__(self, **perms):
        self.perms = {"checked": True, "futures": True, "withdrawals": False, "ip_restrict": True, **perms}

    def api_permissions(self):
        return dict(self.perms)

    def balance(self):
        return {"equity": 100.0, "available": 90.0, "wallet": 100.0, "unrealized": 0.0}

    def server_time(self):
        return lg._now()

    def commission(self, s):
        return (0.0002, 0.0005)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    t = {"now": 1_800_000_000_000}
    monkeypatch.setattr(lg, "_now", lambda: t["now"])
    st = Store(str(tmp_path / "g.db"))
    s = Settings()
    v = Vault(Vault.generate())
    store_api_keys(st, v, "binance", "live", "K", "S")
    return lg.LiveGate(st, s), st, s, v, t


def test_unlock_requires_demo_live_check_and_phrase(gate):
    g, st, s, v, t = gate
    assert not g.unlock("owner", lg.CONFIRM_PHRASE)["ok"]
    g.record_demo_preflight({"ok": True, "ts": t["now"]})
    assert "live key/account check not passed" in g.unlock("owner", lg.CONFIRM_PHRASE)["problems"]
    assert g.live_check(FakeLive(), 40.0)["ok"]
    assert not g.unlock("owner", "yes")["ok"]
    assert g.unlock("owner", lg.CONFIRM_PHRASE)["ok"] and g.is_valid()


@pytest.mark.parametrize("perms", [{"withdrawals": True}, {"ip_restrict": False}, {"futures": False}])
def test_live_check_rejects_unsafe_keys(gate, perms):
    g, *_ = gate
    assert not g.live_check(FakeLive(**perms), 40.0)["ok"]


def test_live_check_requires_equity(gate):
    g, *_ = gate
    assert not g.live_check(FakeLive(), 1_000.0)["ok"]


def _unlocked(gate):
    g, st, s, v, t = gate
    g.record_demo_preflight({"ok": True, "ts": t["now"]})
    g.live_check(FakeLive(), 40.0)
    assert g.unlock("owner", lg.CONFIRM_PHRASE)["ok"]
    return g, st, s, v, t


def test_unlock_expires(gate):
    g, st, s, v, t = _unlocked(gate)
    t["now"] += s.risk.max_unlock_hours * 3_600_000 + 1
    assert not g.is_valid() and "expired" in g.status()["reason"]


def test_unlock_invalidated_by_config_change(gate):
    g, st, s, v, t = _unlocked(gate)
    apply_limit_changes(s.risk, {"max_leverage": 5})
    assert not g.is_valid() and "changed" in g.status()["reason"]


def test_unlock_invalidated_by_key_rotation(gate):
    g, st, s, v, t = _unlocked(gate)
    t["now"] += 1000
    store_api_keys(st, v, "binance", "live", "K2", "S2")
    assert not g.is_valid()


def test_stale_demo_preflight_refused(gate):
    g, st, s, v, t = gate
    g.record_demo_preflight({"ok": True, "ts": t["now"] - 25 * 3_600_000})
    g.live_check(FakeLive(), 40.0)
    assert "demo preflight older than 24 h" in g.unlock("owner", lg.CONFIRM_PHRASE)["problems"]


def test_leverage_tiers_validated():
    assert validate_limits({"leverage_tiers": "5,10,15,20"}) == []
    assert validate_limits({"leverage_tiers": "5,10,15,25"})  # above the live 20x bound
    assert validate_limits({"leverage_tiers": "10,5"})        # not ascending
    assert validate_limits({"leverage_tiers": "x"})
