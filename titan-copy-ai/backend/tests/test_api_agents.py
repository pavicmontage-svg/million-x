"""API, agents and backtest tests (MemoryBus; no network). Data below is SYNTHETIC test fixture data."""
import hashlib
import hmac
import io
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient
from sqlalchemy import select

from tests.test_analytics import disciplined


@pytest.fixture(autouse=True)
def _reset_limits():
    from titan.api import app as appmod
    appmod._pair_limits.clear()


@pytest.fixture()
def client(db):
    from titan.api.app import app
    with TestClient(app) as c:
        r = c.post("/api/pair", json={"code": "test-code-1234", "device_name": "pytest"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        yield c


def test_auth_required_and_bootstrap_single_use(db):
    from titan.api.app import app
    with TestClient(app) as c:
        assert c.get("/api/dashboard").status_code == 401
        assert c.post("/api/pair", json={"code": "TEST-CODE-1234"}).status_code == 200
        assert c.post("/api/pair", json={"code": "TEST-CODE-1234"}).status_code == 401  # bootstrap only once


def test_dashboard_has_default_portfolios(client):
    d = client.get("/api/dashboard").json()
    names = {p["name"]: p for p in d["portfolios"]}
    assert names["Forex Paper"]["initial_capital"] == 500 and names["Forex Paper"]["currency"] == "EUR"
    assert names["Crypto Paper"]["currency"] == "USDT" and names["Crypto Paper"]["simulated"] is True
    assert d["copy_engine"]["running"] is False and "minutes_to_rollover" in d["market_status"]


def test_watchlist_traders_unknown_not_fabricated(client):
    ts = client.get("/api/traders").json()
    zulu = [t for t in ts if t["platform"] == "zulutrade"]
    assert {t["name"] for t in zulu} == {"Carmine Quaranta real1", "Money Pile", "FIRE GC"}
    assert all(t["score"] is None and t["roi_30d"] is None for t in zulu)
    det = client.get(f"/api/traders/{zulu[0]['id']}").json()
    assert all(v is None for v in det["metrics"].values())
    assert "no documented public API" in det["data_note"]


def test_import_score_approve_and_webhook_flow(client):
    trades, curve = disciplined(300)
    tcsv = "symbol,side,volume,open_time,close_time,open_price,close_price,sl,profit\n" + "\n".join(
        f"{t.symbol},{t.side},{t.volume},{t.open_time.isoformat()},{t.close_time.isoformat()},{t.open_price},"
        f"{t.close_price},{t.sl},{t.profit}" for t in trades)
    ccsv = "date,balance,equity\n" + "\n".join(f"{p.ts.isoformat()},{p.balance},{p.equity}" for p in curve)
    r = client.post("/api/traders/import", data={"name": "Imported A", "platform": "myfxbook", "market": "forex"},
                    files={"trades_csv": ("t.csv", tcsv), "curve_csv": ("c.csv", ccsv)})
    assert r.status_code == 200, r.text
    tid = r.json()["trader_id"]
    det = client.get(f"/api/traders/{tid}").json()
    assert det["metrics"]["total_trades"] == 300 and det["explanation"]
    # cannot allocate before approval + authorised source
    fx_pid = next(p["id"] for p in client.get("/api/portfolios").json() if p["market"] == "forex")
    assert client.post("/api/allocations", json={"portfolio_id": fx_pid, "trader_id": tid}).status_code == 409
    assert client.post(f"/api/traders/{tid}/status", json={"status": "approved"}).status_code == 200
    assert client.post("/api/allocations", json={"portfolio_id": fx_pid, "trader_id": tid}).status_code == 409
    secret = client.post(f"/api/traders/{tid}/signal-source").json()["secret"]
    assert client.post("/api/allocations", json={"portfolio_id": fx_pid, "trader_id": tid}).status_code == 200
    body = json.dumps({"kind": "open", "source_position_id": "S1", "symbol": "EURUSD", "side": "buy",
                       "entry_price": 1.08, "sl": 1.078, "tp": 1.085,
                       "source_ts": datetime.now(timezone.utc).isoformat(), "bid": 1.07998, "ask": 1.08}).encode()
    ts = str(int(time.time() * 1000))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    bad = client.post(f"/api/signals/webhook/{tid}", content=body, headers={"X-Titan-Timestamp": ts, "X-Titan-Signature": "0" * 64})
    assert bad.status_code == 401
    ok = client.post(f"/api/signals/webhook/{tid}", content=body, headers={"X-Titan-Timestamp": ts, "X-Titan-Signature": sig})
    assert ok.status_code == 200 and ok.json()["duplicate"] is False
    dup = client.post(f"/api/signals/webhook/{tid}", content=body, headers={"X-Titan-Timestamp": ts, "X-Titan-Signature": sig})
    assert dup.json()["duplicate"] is True
    from titan.events import SIGNALS, get_bus
    assert len(get_bus().consume(SIGNALS, "t", "c")) == 1   # published once
    # backtest + analytics on the imported history
    bt = client.post("/api/backtest", json={"trader_id": tid, "delay_ms": 2000, "failure_prob": 0.05,
                                            "fx_rates": None}).json()
    assert bt["simulated"] is True and bt["stats"]["executed"] > 200 and bt["metrics"]["max_drawdown_pct"] >= 0
    an = client.get(f"/api/analytics/{tid}").json()
    assert an["monte_carlo"]["simulations"] == 5000 and "walk_forward" in an


def test_risk_limits_tighten_only(client):
    pid = client.get("/api/portfolios").json()[0]["id"]
    r = client.patch(f"/api/portfolios/{pid}", json={"risk_limits": {"risk_per_trade_pct": 3, "daily_loss_pct": 1}})
    lim = r.json()["limits"]
    assert lim["risk_per_trade_pct"] == 0.5 and lim["daily_loss_pct"] == 1


def test_emergency_controls(client):
    assert client.post("/api/emergency/stop", json={"reason": "test"}).json()["kill_switch"] is True
    assert client.get("/api/emergency").json()["kill_switch"]["active"] is True
    assert client.post("/api/emergency/release", json={"confirmation": "yes"}).status_code == 403
    assert client.post("/api/emergency/release", json={"confirmation": "RELEASE EMERGENCY STOP"}).status_code == 200
    assert client.post("/api/emergency/flatten", json={"confirmation": "CLOSE ALL"}).json()["queued"] is True
    kinds = [n["kind"] for n in client.get("/api/notifications").json()]
    assert "emergency_stop" in kinds


def test_credentials_encrypted_and_never_returned(client, db):
    r = client.post("/api/connections", json={"venue": "binance_usdm", "environment": "demo", "label": "demo key",
                                              "fields": {"api_key": "AKIA-demo-123", "api_secret": "S3CR3T"}})
    assert r.status_code == 200
    listing = client.get("/api/connections").text
    assert "S3CR3T" not in listing and "AKIA-demo-123" not in listing
    from titan.models import Credential
    with db.session_scope() as s:
        c = s.scalar(select(Credential))
        assert "S3CR3T" not in c.ciphertext
        from titan.security import decrypt_secret
        assert decrypt_secret(c.ciphertext, "binance_usdm|demo|demo key")["api_secret"] == "S3CR3T"
    live = client.post("/api/connections", json={"venue": "binance_usdm", "environment": "live", "label": "x",
                                                 "fields": {"api_key": "a", "api_secret": "b"}})
    assert live.status_code == 409   # live disabled by default


def test_agents_run_and_versioned(client):
    agents = client.get("/api/agents").json()
    assert {a["name"] for a in agents} == {"trader_intelligence", "quant_research", "risk_management",
                                           "market_intelligence", "execution", "audit_performance"}
    pid = client.get("/api/portfolios").json()[0]["id"]
    out = client.post("/api/agents/risk_management/run", json={"portfolio_id": pid}).json()
    assert "llm_review" in out and "LLM offline" in out["llm_review"]["status"]
    out = client.post("/api/agents/audit_performance/run", json={}).json()
    assert out["audit_chain"]["valid"] is True
    a = next(x for x in client.get("/api/agents").json() if x["name"] == "risk_management")
    assert a["metrics"]["runs"] == 1 and a["prompt_versions"][0]["version"] == "1.0.0"


def test_market_agent_news_blackout(db):
    from titan.agents.agents import AGENTS
    from titan.services import state as st
    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with db.session_scope() as s:
        out = AGENTS["market_intelligence"].run(s, use_llm=False, candles={}, calendar=[
            {"time": soon, "currency": "USD", "impact": "High", "title": "FOMC Statement"}])
        assert out["news_blackouts"][0]["currency"] == "USD"
        assert "USD" in st.current_overlay(s).blocked_currencies


def test_llm_tighten_validation():
    from titan.agents.base import valid_tighten
    assert valid_tighten({"kind": "risk_multiplier", "key": "g", "value": 0.5, "minutes": 60, "reason": "x"})
    assert not valid_tighten({"kind": "risk_multiplier", "key": "g", "value": 1.5, "minutes": 60, "reason": "x"})
    assert not valid_tighten({"kind": "raise_limit", "key": "g", "value": 1, "minutes": 60, "reason": "x"})


def test_readiness_gate_blocks_without_evidence(client):
    r = client.get("/api/readiness").json()
    assert all(not x["ready"] for x in r)
    pid = r[0]["portfolio_id"]
    resp = client.post("/api/live/approve", json={"portfolio_id": pid, "confirmation": "x"})
    assert resp.status_code == 409


def test_audit_chain_detects_tampering(client, db):
    assert client.get("/api/audit/verify").json()["valid"] is True
    from titan.models import AuditLog
    with db.session_scope() as s:
        row = s.scalar(select(AuditLog).order_by(AuditLog.id).limit(1))
        row.detail = {"tampered": True}
    v = client.get("/api/audit/verify").json()
    assert v["valid"] is False
