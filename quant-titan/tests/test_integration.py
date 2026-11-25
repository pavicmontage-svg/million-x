"""End-to-end: HTTP API → runtime → replay market data → engines → pipeline → paper exchange."""
import time

import pytest
from starlette.testclient import TestClient

from quanttitan.config import AppConfig, dump, merge_patch
from quanttitan.data.synthetic import generate
from quanttitan.runtime.trader import Trader
from quanttitan.security.auth import Auth
from quanttitan.security.secrets import SecretStore
from quanttitan.storage.db import Database
from quanttitan.api.app import create_app
from cryptography.fernet import Fernet

TOKEN = "device-token-for-tests-0123456789abcdef"


def build(tmp_path, n=3200, bars=None):
    db = Database(f"sqlite:///{tmp_path}/qt.db")
    cfg = merge_patch(AppConfig(), {"system": {"market_data_source": "replay"},
                                    "orchestrator": {"max_cost_r": 1.0, "min_rr_after_costs": 0.3}})
    db.kv_set("config", dump(cfg))
    secrets = SecretStore(db, Fernet.generate_key())
    data = {}
    for k, s in enumerate(["AAAUSDT", "BBBUSDT"]):
        c, _ = generate(n, seed=40 + k, symbol=s, start_price=100 / (k + 1), base_vol=0.004)
        c.volume *= 500; c.quote_volume *= 500; c.taker_buy *= 500
        data[s] = c
    trader = Trader(db, secrets, replay_data=data)
    auth = Auth(db, TOKEN)
    app = create_app(trader, auth, autostart=True, history_dir=str(tmp_path / "h"))
    return app, trader, db


H = {"Authorization": f"Bearer {TOKEN}"}


def wait_done(trader, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        if trader.feed is not None and trader.feed.done:
            return
        time.sleep(0.2)
    raise AssertionError("replay did not finish")


def test_full_stack_replay_paper_trading(tmp_path):
    app, trader, db = build(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401  # auth enforced
        assert client.get("/api/health").status_code == 200
        wait_done(trader)
        st = client.get("/api/status", headers=H).json()
        assert st["mode"] == "PAPER" and st["venue"] == "PAPER"
        assert set(st["regimes"]) == {"AAAUSDT", "BBBUSDT"}
        decisions = client.get("/api/decisions?limit=500", headers=H).json()
        assert decisions and all("explanation" in d for d in decisions)
        assert any("FINAL DECISION" in d["explanation"] for d in decisions)
        trades = client.get("/api/trades", headers=H).json()
        assert trades, "replay produced no paper trades"
        for t in trades:
            assert t["mode"] == "paper" and t["fees"] > 0
        perf = client.get("/api/performance", headers=H).json()
        assert "metrics" in perf, perf
        assert perf["metrics"]["trades"] == len(trades)
        agents = client.get("/api/agents", headers=H).json()
        assert set(agents) == {"quant_researcher", "market_analyst", "execution_specialist", "portfolio_manager",
                               "risk_manager", "performance_auditor"}
        eng = client.get("/api/engines/vwap", headers=H).json()
        assert eng["name"] == "vwap" and "VWAP" in eng["doc"]
        assert len(client.get("/api/engines", headers=H).json()) == 6
        # paper trades were recorded as evidence in the edge book
        assert sum(e.n for e in trader.edge.all_estimates()) == len([t for t in trades if not t["group_id"]])


def test_controls_confirmations_and_audit(tmp_path):
    app, trader, db = build(tmp_path, n=2600)
    with TestClient(app) as client:
        wait_done(trader)
        r = client.post("/api/positions/close_all", headers=H, json={"confirm": "yes"})
        assert r.status_code == 400 and "CLOSE ALL" in r.json()["error"]
        assert client.post("/api/positions/close_all", headers=H, json={"confirm": "CLOSE ALL"}).status_code == 200
        assert client.post("/api/control/emergency_stop", headers=H, json={}).status_code == 400
        r = client.post("/api/control/emergency_stop", headers=H, json={"confirm": True, "flatten": True})
        assert r.status_code == 200
        st = client.get("/api/status", headers=H).json()
        assert "EMERGENCY_STOP" in st["breakers"] and st["paused"]
        assert client.post("/api/control/resume", headers=H, json={}).status_code == 403
        assert client.post("/api/risk/reset", headers=H, json={"name": "EMERGENCY_STOP", "confirm": "RESET"}).status_code == 200
        assert client.post("/api/control/resume", headers=H, json={}).status_code == 200
        # config validation and versioning
        bad = client.patch("/api/config", headers=H, json={"patch": {"risk": {"risk_per_trade_pct": 50}}})
        assert bad.status_code == 400
        good = client.patch("/api/config", headers=H, json={"patch": {"risk": {"risk_per_trade_pct": 0.25}}, "note": "test"})
        assert good.status_code == 200 and good.json()["config"]["risk"]["risk_per_trade_pct"] == 0.25
        live = client.patch("/api/config", headers=H, json={"patch": {"execution": {"exchange_env": "LIVE"}}})
        assert live.status_code == 403
        r = client.post("/api/live/enable", headers=H, json={"confirm": "ENABLE LIVE TRADING"})
        assert r.status_code == 403  # deployment flag not set
        actions = {a["action"] for a in db.audit_log()}
        assert {"close_all", "emergency_stop", "breaker_reset", "config_update"} <= actions


def test_secrets_never_returned_and_login_rate_limit(tmp_path):
    app, trader, db = build(tmp_path, n=400)
    with TestClient(app) as client:
        k, s = "A" * 40 + "key1", "B" * 40 + "sec1"
        assert client.post("/api/keys", headers=H, json={"env": "testnet", "key": k, "secret": s}).status_code == 200
        body = client.get("/api/keys", headers=H).text
        assert k not in body and s not in body
        row = db.query("SELECT ciphertext FROM secrets WHERE name='binance_testnet_secret'")[0]["ciphertext"]
        assert s not in row
        assert client.post("/api/keys", headers=H, json={"env": "testnet", "key": "short", "secret": "x"}).status_code == 400
        # admin bootstrap + login with lockout
        assert client.post("/api/auth/setup", json={"username": "admin", "password": "correct horse battery"}).status_code == 200
        assert client.post("/api/auth/setup", json={"username": "x", "password": "another password 1"}).status_code == 403
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"}).json()["token"]
        assert client.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        codes = [client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code for _ in range(7)]
        assert codes[-1] == 403
        r = client.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
        assert r.status_code == 403 and "too many" in r.json()["error"]


def test_websocket_requires_token_and_streams(tmp_path):
    app, trader, db = build(tmp_path, n=400)
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=bad") as ws:
                ws.receive_json()
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status" and "portfolio" in msg["data"]
