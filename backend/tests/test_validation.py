"""Live eligibility needs evidence; degradation detection (Phase 5)."""
import time

from millionx.config import Settings
from millionx.models import BotStatus
from millionx.storage import Store
from millionx.validation import degradation, eligibility


def _trade(st, i, bot, r, mode="paper", virtual=0):
    st.insert_trade({"id": f"{bot}-{mode}-{virtual}-{i}", "bot_id": bot, "symbol": "X", "side": "LONG", "qty": 1,
                     "entry_price": 1, "exit_price": 1, "opened_at": i, "closed_at": int(time.time() * 1000) - 1000 + i,
                     "gross_pnl": r, "fees": 0, "funding": 0, "net_pnl": r, "r_multiple": r, "risk_amount": 1,
                     "exit_reason": "x", "regime": "BULL|NORMAL_VOL", "virtual": virtual, "mode": mode})


def test_no_eligibility_without_walk_forward(tmp_path):
    st, s = Store(str(tmp_path / "v.db")), Settings()
    for i in range(80):
        _trade(st, i, "bot2_ema", 1.0 if i % 2 else -0.5)
    el = eligibility(st, s, "bot2_ema")
    assert not el["eligible"] and not el["checks"]["walk_forward_passed"]["pass"]


def test_eligibility_with_evidence_and_shadow_trades_do_not_count(tmp_path):
    st, s = Store(str(tmp_path / "v.db")), Settings()
    st.kv_set("validation:bot2_ema", {"status": "BACKTEST_PASSED", "job": "j1", "ts": int(time.time() * 1000)})
    for i in range(80):
        _trade(st, i, "bot2_ema", 1.0 if i % 2 else -0.5, virtual=1)
    assert not eligibility(st, s, "bot2_ema")["eligible"], "shadow trades are not paper evidence"
    for i in range(60):
        _trade(st, i, "bot2_ema", 1.0 if i % 2 else -0.5)
    assert eligibility(st, s, "bot2_ema")["eligible"]


def test_stale_validation_expires(tmp_path):
    st, s = Store(str(tmp_path / "v.db")), Settings()
    st.kv_set("validation:b", {"status": "BACKTEST_PASSED", "job": "j", "ts": int(time.time() * 1000) - 91 * 86_400_000})
    for i in range(60):
        _trade(st, i, "b", 1.0 if i % 2 else -0.5)
    assert not eligibility(st, s, "b")["checks"]["validation_fresh"]["pass"]


def test_degradation_detected_against_validation(tmp_path):
    st = Store(str(tmp_path / "v.db"))
    st.kv_set("validation:b", {"status": "BACKTEST_PASSED", "oos_expectancy_r": 0.3, "ts": 0})
    for i in range(30):
        _trade(st, i, "b", -0.4 if i % 3 else 0.2, virtual=1)
    d = degradation(st, "b", "paper")
    assert d["degraded"] and d["n"] == 30


def test_live_statuses_demoted_without_evidence(tmp_path, clock, monkeypatch):
    import millionx.engine.live as live
    from conftest import FakeMarket
    monkeypatch.setattr(live, "now_ms", clock)
    st = Store(str(tmp_path / "e.db"))
    st.x("INSERT INTO bot_state(bot_id,status,reason,params,weight,updated_at) VALUES('bot2_ema','LIVE_ELIGIBLE','',"
         "'{}',1.0,0)")
    eng = live.TradingEngine(Settings(mode="live"), st, FakeMarket(clock))
    assert eng.sup.status["bot2_ema"] == BotStatus.PAPER
