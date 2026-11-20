"""Live-activity, chart and agent endpoints on a real paper engine (fake market)."""
import pytest
from starlette.testclient import TestClient

import millionx.engine.live as live
import millionx.exchange.paper as paper_mod
import millionx.engine.execution as exec_mod
from millionx.api import create_app
from millionx.config import Settings
from millionx.exchange.paper import PaperExchange
from millionx.models import MINUTE_MS
from millionx.runtime import Runtime
from millionx.security import Vault, create_pairing_code, redeem_pairing_code
from millionx.storage import Store

from conftest import FakeMarket


@pytest.fixture
def client(tmp_path, clock, monkeypatch):
    import millionx.api as api_mod
    for m in (live, paper_mod, exec_mod, api_mod):
        monkeypatch.setattr(m, "now_ms", clock)
    s = Settings(mode="paper", execution="auto", capital_mode="multi", db_path=str(tmp_path / "a.db"))
    s.min_depth_usdt = 1.0
    s.max_spread_bps = 5
    s.multi_capital_eur = 4000.0
    store = Store(s.db_path)
    market = FakeMarket(clock)
    eng = live.TradingEngine(s, store, PaperExchange(market, 4000 * s.eur_usdt, s.fees))
    for _ in range(900):
        clock.t += MINUTE_MS
        eng.tick()
    import threading
    eng.thread = threading.current_thread()  # ticks are driven by the test; the engine counts as running
    rt = Runtime(s, store, Vault(Vault.generate()))
    rt.engine = eng
    tok = redeem_pairing_code(store, create_pairing_code(store, "owner"), "t", "1.1.1.1")["token"]
    c = TestClient(create_app(rt))
    c.headers["authorization"] = f"Bearer {tok}"
    return c, eng, rt


def test_activity_explains_every_decision(client):
    c, eng, rt = client
    r = c.get("/api/activity")
    assert r.status_code == 200
    d = r.json()
    f = d["funnel"]
    assert f["signals"] > 0 and f["supervisor_accepted"] + f["supervisor_rejected"] == f["signals"]
    assert d["decisions"], "accepted signals produce logged decisions"
    for x in d["decisions"]:
        assert x["outcome"] and x["side"] in ("LONG", "SHORT")
        if x["outcome"] not in ("executed", "awaiting_owner_approval"):
            assert x["reason"], f"blocked decision without a reason: {x}"
    assert isinstance(d["shadow_open"], list)
    eg = d["engine"]
    assert eg["universe"] and eg["bars_processed"] > 0 and eg["verdict"] and eg["level"] in ("ok", "info", "warning")


def test_candles_and_competition(client):
    c, eng, rt = client
    sym = sorted(eng.bars)[0]
    d = c.get(f"/api/candles/{sym}?n=60").json()
    assert len(d["candles"]) == 60
    k = d["candles"][-1]
    assert k["l"] <= min(k["o"], k["c"]) and k["h"] >= max(k["o"], k["c"])
    assert all(a["t"] < b["t"] for a, b in zip(d["candles"], d["candles"][1:]))
    assert c.get("/api/candles/NOPEUSDT").json()["candles"] == []
    cur = c.get("/api/competition/curves?hours=1000").json()
    assert cur["bots"], "shadow trades give every competing bot a curve"
    for b in cur["bots"]:
        assert b["trades"] == sum(1 for _ in b["points"]) or len(b["points"]) <= 201
        assert abs(b["points"][-1][1] - round(b["sum_r"], 4)) < 1e-6


def test_agents_status_and_activity_without_ai_model(client):
    c, eng, rt = client
    rt.org.run_agent("brain_market", eng)
    agents = {a["id"]: a for a in c.get("/api/org/agents").json()}
    assert agents["brain_market"]["status"] == "rules" and agents["brain_market"]["active"]
    assert agents["brain_quant"]["status"] == "idle"  # never ran: not shown as working
    act = c.get("/api/org/activity").json()
    assert act["llm"]["connected"] is False and act["interval_min"] == 10
    assert act["runs"][0]["agent_id"] == "brain_market" and act["runs"][0]["mode"] == "deterministic"
    assert c.get("/api/settings/llm").json()["presets"]["groq"]["base_url"].startswith("https://api.groq.com")


def test_llm_connect_rejects_bad_key_and_never_stores_it(client, monkeypatch):
    c, eng, rt = client
    from millionx.org import llm as llm_mod

    class R:
        status_code = 401
        text = '{"error":"invalid api key"}'

    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: R())
    r = c.put("/api/settings/llm", json={"provider": "groq", "api_key": "gsk_wrongwrongwrong"})
    assert r.status_code == 400 and "401" in r.json()["error"]
    assert not rt.org.llm.available and not rt.store.kv_get("llm_config")


def test_llm_connect_openai_compatible_and_persist_encrypted(client, monkeypatch):
    c, eng, rt = client
    from millionx.org import llm as llm_mod
    seen = {}

    class R:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}

    def post(url, **k):
        seen["url"], seen["auth"], seen["model"] = url, k["headers"]["Authorization"], k["json"]["model"]
        return R()

    monkeypatch.setattr(llm_mod.requests, "post", post)
    r = c.put("/api/settings/llm", json={"provider": "groq", "api_key": "gsk_testkey1234567890"})
    assert r.status_code == 200, r.text
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_testkey1234567890" and seen["model"] == "openai/gpt-oss-120b"
    cfg = rt.store.kv_get("llm_config")
    assert "gsk_testkey" not in str(cfg), "key must be stored encrypted"
    rt.org.llm.disconnect()
    rt.load_llm_config()
    assert rt.org.llm.available and rt.org.llm.model_override == "openai/gpt-oss-120b"
    rt.org.llm.legacy.min_gap_s = 0
    out = rt.org.run_agent("brain_market", eng)
    assert out["mode"] in ("llm", "deterministic")  # "OK" is not JSON -> honest deterministic fallback
    agents = {a["id"]: a for a in c.get("/api/org/agents").json()}
    assert agents["brain_market"]["llm_connected"] is True


def test_binance_account_view_read_only(client, monkeypatch):
    c, eng, rt = client
    from millionx.exchange import binance as bmod
    from millionx.security import store_api_keys
    d = c.get("/api/exchange/account").json()
    assert d["connected"] is False and d["stored"] == {"demo": False, "live": False}
    store_api_keys(rt.store, rt.vault, "binance", "demo", "k" * 20, "s" * 20)
    import millionx.api as api_mod
    now = api_mod.now_ms()
    calls = []
    monkeypatch.setattr(bmod.BinanceFutures, "balance",
                        lambda self: calls.append(self.base) or {"equity": 5010.0, "wallet": 5000.0,
                                                                 "available": 4900.0, "unrealized": 10.0})
    monkeypatch.setattr(bmod.BinanceFutures, "position_details", lambda self: [
        {"symbol": "ETHUSDT", "side": "SHORT", "qty": 0.01, "entry": 3000.0, "mark": 2990.0, "unrealized": 0.1,
         "leverage": 3, "liquidation": 4000.0}])
    monkeypatch.setattr(bmod.BinanceFutures, "income_history", lambda self, start, end=None, max_pages=5: [
        {"time": now - 3_600_000, "symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "2.5"},
        {"time": now - 3_600_000, "symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-0.4"},
        {"time": now - 1_800_000, "symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": "-1.0"},
        {"time": now - 1_000_000, "symbol": "", "incomeType": "TRANSFER", "income": "1000"},
    ])
    d = c.get("/api/exchange/account").json()
    assert calls == ["https://demo-fapi.binance.com"]
    assert d["connected"] and d["account"] == "demo" and d["bots_trade_here"] is False  # engine is in paper
    p = d["pnl"]
    assert abs(p["net"] - 1.1) < 1e-9 and p["closing_fills"] == 2 and p["winning_fills"] == 1
    assert abs(p["curve"][-1][1] - 1.1) < 1e-6, "transfers are not profit"
    assert d["positions"][0]["symbol"] == "ETHUSDT" and d["balance"]["wallet"] == 5000.0
    c.get("/api/exchange/account")
    assert len(calls) == 1, "cached for 15 s to respect Binance rate limits"


def test_engine_diagnosis_names_liquidity_filter(client):
    c, eng, rt = client
    eng.universe = []
    for v in eng.scanner.values():
        v.update(ok=False, reason="spread/depth below threshold")
    d = c.get("/api/activity").json()["engine"]
    assert d["level"] == "warning" and "liquidity filter" in d["verdict"] and d["liquidity_failed"]
    rt.engine = None
    rt.engine_error = "no API keys stored for mode 'demo'"
    d = c.get("/api/activity").json()["engine"]
    assert d["level"] == "critical" and "no API keys" in d["verdict"]


def test_risk_profile_and_pause_reset(client):
    c, eng, rt = client
    r = c.post("/api/risk/profile", json={"profile": "aggressive"})
    assert r.status_code == 200 and rt.s.risk.risk_per_trade == 0.02 and rt.s.risk.max_consecutive_losses == 10
    assert c.get("/api/risk").json()["profile"] == "aggressive"
    assert c.post("/api/risk/profile", json={"profile": "yolo"}).status_code == 400
    import millionx.api as api_mod
    eng.risk.state.cooldown_until = api_mod.now_ms() + 3_600_000
    eng.risk.state.consecutive_losses = 4
    d = c.get("/api/activity").json()["engine"]
    assert d["level"] == "warning" and "paused" in d["verdict"] and "not the AI" in d["verdict"]
    assert c.post("/api/risk/cooldown-reset", json={}).json()["ok"]
    assert eng.risk.state.cooldown_until == 0 and eng.risk.can_open(api_mod.now_ms())[1] != "loss_streak_cooldown"
    rt.s.mode = "live"
    assert c.post("/api/risk/profile", json={"profile": "aggressive"}).status_code == 400  # needs explicit phrase
    rt.s.mode = "paper"
    c.post("/api/risk/profile", json={"profile": "standard"})
    assert rt.s.risk.risk_per_trade == 0.005


def test_max_profile_only_with_virtual_money(client):
    c, eng, rt = client
    r = c.post("/api/risk/profile", json={"profile": "max"})
    assert r.status_code == 200, r.text
    assert rt.s.risk.risk_per_trade == 0.05 and rt.s.risk.max_leverage == 50
    # wider values are refused for live, and the engine cannot be switched to live with them
    rt.gate.is_valid = lambda: True
    r = c.put("/api/settings", json={"mode": "live"})
    assert r.status_code == 400 and "virtual money" in r.json()["error"] and rt.s.mode == "paper"
    rt.s.mode = "live"
    assert c.post("/api/risk/profile", json={"profile": "max"}).status_code == 400
    assert c.put("/api/risk/limits", json={"risk_per_trade": 0.05}).status_code == 400
    rt.s.mode = "paper"
    c.post("/api/risk/profile", json={"profile": "standard"})
    assert rt.s.risk.max_leverage == 20 and rt.s.risk.leverage_tiers == "5,10,15,20"


def test_max_profile_sizes_with_high_leverage_on_4_eur():
    """4 EUR experimental account: MAX makes the minimum order affordable through leverage."""
    from millionx.config import FeeModel, RiskLimits
    from millionx.governance import RISK_PROFILES, apply_limit_changes
    from millionx.models import Side, Signal
    from millionx.risk import RiskManager, SymbolRules
    lim = RiskLimits()
    apply_limit_changes(lim, RISK_PROFILES["max"], "demo")
    rm = RiskManager(lim, FeeModel())
    rm.state.equity = 4.3
    e = 3000.0
    sig = Signal("b", "ETHUSDT", Side.LONG, e, e * (1 - 0.0042), e * 1.0084, e * 1.0126, 0.6, 0, 1)
    dec = rm.size(sig, SymbolRules("ETHUSDT", min_notional=20.0, step_size=0.001, min_qty=0.001,
                                   max_leverage=100), [], 0.003, 0)
    assert dec.approved, dec.reasons
    assert dec.leverage >= 5 and dec.notional >= 20.0


def test_owner_timeframe_applies_to_all_base_bots(client, monkeypatch):
    c, eng, rt = client
    restarted = []
    monkeypatch.setattr(rt, "start_engine", lambda: restarted.append(True))
    assert c.put("/api/settings", json={"timeframe": 45}).status_code == 400
    r = c.put("/api/settings", json={"timeframe": 240})
    assert r.status_code == 200 and restarted and rt.s.strategy_timeframe == 240
    assert c.get("/api/settings").json()["timeframe"] == 240
    assert rt.store.kv_get("runtime_settings")["strategy_timeframe"] == 240
    p = eng._bot_params()
    assert all(p[b]["timeframe"] == 240 for b in p if b != "bot10_adaptive") and "bot10_adaptive" not in p
    assert eng.window() == 21600  # 240 min x (60 warm-up + 30) one-minute bars
    rt.s.strategy_timeframe = 0


def test_time_stop_scales_with_signal_timeframe():
    from millionx.engine.position import ExitPolicy, update_on_bar
    from millionx.models import Position, Side
    pol = ExitPolicy()
    for tf, expect_closed in ((5, True), (240, False)):
        p = Position(id="x", bot_id="b", symbol="S", side=Side.LONG, qty=1.0, entry_price=100.0, stop=90.0,
                     tp1=120.0, tp2=130.0, initial_stop=90.0, opened_at=0, risk_amount=10.0, qty_initial=1.0,
                     best_price=100.0, timeframe=tf)
        ev = update_on_bar(p, 100.0, 100.5, 99.5, 100.0, 1.0, 4 * 3_600_000, pol, 0.0005)  # 4 h later
        assert any(e.reason == "time_stop" for e in ev) == expect_closed


def test_tradingview_webhook_security_and_pipeline(client, monkeypatch):
    import hashlib
    import hmac
    import json as _j
    import millionx.tradingview as tvm
    c, eng, rt = client
    monkeypatch.setattr(tvm, "now_ms", lambda: 1_800_000_000_000)
    setup = c.get("/api/tradingview/setup").json()
    sec = setup["secret"]
    sym = eng.universe[0]
    px = float(eng.bars[sym].close[-1])
    submitted = []
    monkeypatch.setattr(eng, "submit_external", lambda sig, origin: submitted.append((sig, origin)) or
                        {"ok": True, "signal_id": sig.id})
    base = {"id": "a1", "symbol": sym, "side": "LONG", "timeframe": "5", "time": 1_800_000_000_000,
            "price": px, "stop": px * 0.99, "take_profit": px * 1.03}
    no_auth = c.post("/api/tradingview/webhook", content=_j.dumps({**base, "secret": "wrong"}))
    assert no_auth.status_code == 401 and not submitted
    ok = c.post("/api/tradingview/webhook", content=_j.dumps({**base, "secret": sec}))
    assert ok.status_code == 202 and submitted[0][1] == "tradingview"
    assert c.post("/api/tradingview/webhook", content=_j.dumps({**base, "secret": sec})).status_code == 409  # dedup
    old = c.post("/api/tradingview/webhook", content=_j.dumps({**base, "id": "a2", "secret": sec,
                                                               "time": 1_800_000_000_000 - 120_000}))
    assert old.status_code == 400 and "replay" in old.json()["reason"]
    bad_side = c.post("/api/tradingview/webhook", content=_j.dumps({**base, "id": "a3", "secret": sec,
                                                                    "stop": px * 1.01}))
    assert bad_side.status_code == 400 and "wrong side" in bad_side.json()["reason"]
    unknown = c.post("/api/tradingview/webhook", content=_j.dumps({**base, "id": "a4", "secret": sec,
                                                                   "symbol": "DOGEUSDT"}))
    assert unknown.status_code == 400
    raw = _j.dumps({**base, "id": "a5"}).encode()
    mac = hmac.new(sec.encode(), raw, hashlib.sha256).hexdigest()
    signed = c.post("/api/tradingview/webhook", content=raw, headers={"X-MX-Signature": mac})
    assert signed.status_code == 202 and len(submitted) == 2
    stored = rt.store.q("SELECT payload FROM tv_signals")
    assert stored and all(sec not in r["payload"] for r in stored), "secret never stored"
    assert c.get("/api/strategies/moneyline").json()["implemented"] is False
