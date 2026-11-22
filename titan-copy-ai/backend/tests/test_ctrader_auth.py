"""cTrader login flow (OAuth) with mocked cTrader endpoints. Accounts/tokens are TEST FIXTURES."""
import time

import httpx
import pytest
from sqlalchemy import select

from titan.adapters.forex import ctrader_auth as ca

ACCOUNTS = [{"account_id": 4440001, "is_live": False, "trader_login": 5123456, "broker": "DemoBroker"},
            {"account_id": 4440002, "is_live": True, "trader_login": 9000001, "broker": "DemoBroker"}]


def test_authorize_url_and_code_parsing():
    u = ca.authorize_url("cid", "http://127.0.0.1:8765/api/ctrader/callback", "st1")
    assert u.startswith("https://id.ctrader.com/my/settings/openapi/grantingaccess/?client_id=cid")
    assert "scope=trading" in u and "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765" in u
    assert ca.code_from("http://127.0.0.1:8765/api/ctrader/callback?code=ABC123&state=st1") == "ABC123"
    assert ca.code_from("  ABC123 ") == "ABC123"


def test_token_exchange_parsing():
    def handler(r: httpx.Request):
        q = dict(r.url.params)
        assert r.url.path == "/apps/token" and q["grant_type"] == "authorization_code" and q["code"] == "C1"
        return httpx.Response(200, json={"accessToken": "AT", "tokenType": "bearer", "expiresIn": 2628000,
                                         "refreshToken": "RT"})
    t = ca.exchange_code("cid", "sec", "C1", "http://x", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert t["access_token"] == "AT" and t["refresh_token"] == "RT" and t["expires_at"] > time.time() + 29 * 86400
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"errorCode": "ACCESS_DENIED", "description": "code expired"})))
    with pytest.raises(Exception, match="code expired"):
        ca.exchange_code("cid", "sec", "C1", "http://x", http=bad)


def test_resolve_account_by_login_or_single(monkeypatch):
    monkeypatch.setattr(ca, "list_accounts", lambda *a: ACCOUNTS)
    cred = {"client_id": "c", "client_secret": "s", "access_token": "t"}
    assert ca.resolve_account_id({**cred, "account_id": "5123456"}, "demo") == 4440001   # login number works
    assert ca.resolve_account_id({**cred, "account_id": 4440001}, "demo") == 4440001     # internal id works
    assert ca.resolve_account_id(cred, "demo") == 4440001                                # empty: only demo account
    with pytest.raises(Exception, match="available: 5123456"):
        ca.resolve_account_id({**cred, "account_id": "777"}, "demo")


def test_oauth_flow_end_to_end(monkeypatch, db):
    from starlette.testclient import TestClient

    from titan.api import app as appmod
    appmod._pair_limits.clear()
    monkeypatch.setattr(ca, "exchange_code", lambda cid, sec, code, redir: {
        "access_token": "AT-" + code, "refresh_token": "RT", "expires_at": time.time() + 2_600_000})
    monkeypatch.setattr(ca, "list_accounts", lambda *a: ACCOUNTS)
    with TestClient(appmod.app) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/api/pair", json={"code": "TEST-CODE-1234"}).json()["token"]
        r = c.post("/api/ctrader/oauth/start", json={"client_id": "my-client-id", "client_secret": "my-secret",
                                                     "label": "Pepperstone"}).json()
        assert r["redirect_uri"].endswith("/api/ctrader/callback") and "state=" + r["state"] in r["authorize_url"]
        page = c.get("/api/ctrader/callback", params={"code": "XYZ", "state": r["state"]})   # browser returns
        assert page.status_code == 200 and "Connected" in page.text
        stt = c.get(f"/api/ctrader/oauth/{r['state']}").json()
        assert stt["status"] == "choose_account" and [a["trader_login"] for a in stt["accounts"]] == [5123456, 9000001]
        assert "my-secret" not in str(stt)
        live = c.post(f"/api/ctrader/oauth/{r['state']}/finish", json={"account_id": 4440002})
        assert live.status_code == 409                                    # live account refused while live is off
        done = c.post(f"/api/ctrader/oauth/{r['state']}/finish", json={"account_id": 4440001}).json()
        assert done["environment"] == "demo" and done["label"] == "Pepperstone 5123456"
        assert c.get(f"/api/ctrader/oauth/{r['state']}").status_code == 404   # one-time session
        # paste fallback on a fresh session
        r2 = c.post("/api/ctrader/oauth/start", json={"client_id": "my-client-id", "client_secret": "my-secret"}).json()
        out = c.post(f"/api/ctrader/oauth/{r2['state']}/code",
                     json={"code_or_url": "https://openapi.ctrader.com/?code=PASTED&state=x"}).json()
        assert out["status"] == "choose_account"
        assert c.get("/api/ctrader/callback", params={"code": "x", "state": "bogus"}).status_code == 404
    from titan.models import Credential
    from titan.security import decrypt_secret
    with db.session_scope() as s:
        cr = s.scalar(select(Credential).where(Credential.venue == "ctrader"))
        data = decrypt_secret(cr.ciphertext, f"ctrader|demo|{cr.label}")
        assert data["access_token"] == "AT-XYZ" and data["account_id"] == 4440001 and "my-secret" not in cr.ciphertext


def test_token_refresh_job(monkeypatch, db):
    from titan import jobs
    from titan.models import Credential
    from titan.security import decrypt_secret, encrypt_secret
    with db.session_scope() as s:
        s.add(Credential(venue="ctrader", environment="demo", label="x", fingerprint="f", ciphertext=encrypt_secret(
            {"client_id": "c", "client_secret": "s", "access_token": "old", "refresh_token": "R",
             "expires_at": time.time() + 86400}, "ctrader|demo|x")))
    monkeypatch.setattr(ca, "refresh", lambda cid, sec, rt: {"access_token": "new", "refresh_token": "R2",
                                                             "expires_at": time.time() + 2_600_000})
    assert jobs.refresh_ctrader_tokens() == 1
    assert jobs.refresh_ctrader_tokens() == 0          # fresh token: nothing to do
    with db.session_scope() as s:
        cr = s.scalar(select(Credential))
        assert decrypt_secret(cr.ciphertext, "ctrader|demo|x")["access_token"] == "new"
