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
    rt.org.llm.min_gap_s = 0
    out = rt.org.run_agent("brain_market", eng)
    assert out["mode"] in ("llm", "deterministic")  # "OK" is not JSON -> honest deterministic fallback
    agents = {a["id"]: a for a in c.get("/api/org/agents").json()}
    assert agents["brain_market"]["llm_connected"] is True
