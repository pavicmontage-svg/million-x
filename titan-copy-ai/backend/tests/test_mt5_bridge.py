"""MT5 adapter <-> bridge protocol test with a FAKE MetaTrader5 module (the real one runs only on Windows)."""
import importlib
import sys
import types
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("fastapi")   # the bridge runs on the owner's Windows PC (server-side dependency)


def fake_mt5():
    m = types.ModuleType("MetaTrader5")
    m.POSITION_TYPE_BUY, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 0, 1
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_SLTP, m.TRADE_RETCODE_DONE = 1, 6, 10009
    m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN, m.ORDER_TIME_GTC = 0, 1, 2, 0
    st = {"positions": {}, "deals": [], "ticket": 1000}
    syms = {"EURUSD.r": NS(name="EURUSD.r", digits=5, point=1e-5, trade_contract_size=100000.0, volume_min=0.01,
                           volume_step=0.01, volume_max=50.0, currency_profit="USD", currency_base="EUR", filling_mode=1)}
    m.initialize = lambda: True
    m.terminal_info = lambda: NS(connected=True, trade_allowed=True)
    m.account_info = lambda: NS(currency="EUR", balance=500.0, equity=500.0, margin=0.0, margin_free=500.0,
                                leverage=30, server="Demo", login=123, trade_mode=0)
    m.symbol_info = lambda s: syms.get(s)
    m.symbols_get = lambda *a: list(syms.values())
    m.symbol_select = lambda s, on: True
    m.symbol_info_tick = lambda s: NS(bid=1.08, ask=1.08002, time_msc=0) if s in syms else None

    def positions_get(ticket=None):
        ps = list(st["positions"].values())
        return [p for p in ps if p.ticket == ticket] if ticket else ps
    m.positions_get = positions_get

    def order_send(req):
        st["ticket"] += 1
        if req["action"] == m.TRADE_ACTION_SLTP:
            p = st["positions"][req["position"]]
            p.sl, p.tp = req["sl"], req["tp"]
            return NS(retcode=10009, comment="done", deal=0, order=0, volume=0, price=0)
        if "position" in req:   # closing deal
            p = st["positions"][req["position"]]
            p.volume = round(p.volume - req["volume"], 2)
            if p.volume <= 0:
                del st["positions"][req["position"]]
            return NS(retcode=10009, comment="done", deal=st["ticket"], order=st["ticket"], volume=req["volume"], price=1.081)
        assert req["symbol"] == "EURUSD.r" and req["sl"] > 0          # bridge resolved the broker symbol
        t = st["ticket"]
        st["positions"][t] = NS(ticket=t, symbol=req["symbol"], type=req["type"], volume=req["volume"],
                                price_open=req["price"], sl=req["sl"], tp=req["tp"], profit=0.0, comment=req["comment"], magic=1, time=0)
        st["deals"].append(NS(ticket=t, position_id=t, symbol=req["symbol"], type=req["type"], entry=0, volume=req["volume"],
                              price=req["price"], profit=0.0, commission=-0.35, swap=0.0, time_msc=0, comment=req["comment"]))
        return NS(retcode=10009, comment="done", deal=t, order=t, volume=req["volume"], price=req["price"])
    m.order_send = order_send
    m.history_deals_get = lambda a, b: st["deals"]
    m.last_error = lambda: (0, "ok")
    return m


@pytest.fixture()
def bridge(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5())
    monkeypatch.setenv("TITAN_BRIDGE_SECRET", "s3cret-for-test")
    sys.path.insert(0, "bridges")
    mod = importlib.import_module("mt5_bridge")
    mod = importlib.reload(mod)
    from fastapi.testclient import TestClient
    yield TestClient(mod.app)
    sys.path.remove("bridges")


def test_adapter_talks_to_bridge(bridge):
    from titan.adapters.base import AdapterError, OpenRequest
    from titan.adapters.forex.mt5 import MT5BridgeAdapter
    from titan.models import OrderStatus
    ad = MT5BridgeAdapter("http://testserver", "s3cret-for-test", http=bridge)
    ad.sync_specs(["EURUSD"])
    acct = ad.account()
    assert acct.currency == "EUR" and acct.equity == 500.0
    assert ad.quote("EURUSD") == (1.08, 1.08002)
    res = ad.open_position(OpenRequest("tcabc123", "EURUSD", "buy", 0.02, sl=1.078, tp=1.085))
    assert res.status == OrderStatus.FILLED and res.position_ref
    pos = ad.positions()[0]
    assert pos.symbol == "EURUSD" and pos.sl == 1.078 and pos.client_id == "tcabc123"
    assert ad.modify_protection(pos, 1.079, None, "tcmod").status == OrderStatus.ACKED
    assert ad.positions()[0].sl == 1.079
    part = ad.close_position(ad.positions()[0], 0.01, "tcclose1")
    assert part.status == OrderStatus.FILLED and ad.positions()[0].quantity == pytest.approx(0.01)
    assert ad.get_order("tcabc123", "EURUSD").filled_qty == 0.02
    bad = MT5BridgeAdapter("http://testserver", "wrong-secret", http=bridge)
    with pytest.raises(AdapterError, match="401"):
        bad.account()


class DownExceptTestserver(httpx.BaseTransport if (httpx := __import__("httpx")) else object):
    """Hosts other than 'testserver' behave like an unreachable PC address."""
    def __init__(self, client):
        self.inner = client._transport

    def handle_request(self, request):
        if request.url.host != "testserver":
            raise httpx.ConnectError("unreachable", request=request)
        resp = self.inner.handle_request(request)
        resp.read()
        return httpx.Response(resp.status_code, headers=resp.headers, content=resp.content, request=request)


def test_pairing_page_only_local_and_qr_payload(bridge, monkeypatch):
    mod = sys.modules["mt5_bridge"]
    assert bridge.get("/pair").status_code == 403                   # from the network: refused
    monkeypatch.setattr(mod, "LOCAL_HOSTS", mod.LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(mod, "_tailscale_ip", lambda: "100.64.0.9")
    monkeypatch.setattr(mod, "_lan_ip", lambda: "192.168.1.20")
    page = bridge.get("/pair")
    assert page.status_code == 200 and "<svg" in page.text and "Scan QR from PC" in page.text
    payload = mod.pair_payload(8765)
    from titan.api.routes import decode_mt5_payload
    d = decode_mt5_payload(payload)
    assert d["urls"] == ["http://100.64.0.9:8765", "http://192.168.1.20:8765"] and d["secret"] == "s3cret-for-test"


def test_adapter_falls_back_to_second_address(bridge):
    import httpx

    from titan.adapters.base import AdapterUnavailable, OpenRequest
    from titan.adapters.forex.mt5 import MT5BridgeAdapter
    http = httpx.Client(transport=DownExceptTestserver(bridge))
    ad = MT5BridgeAdapter("http://100.64.0.9:8765", "s3cret-for-test", http=http, alt_urls=["http://testserver"])
    assert ad.account().equity == 500.0 and ad.url == "http://testserver"      # GET switched address
    ad2 = MT5BridgeAdapter("http://100.64.0.9:8765", "s3cret-for-test", http=http, alt_urls=["http://testserver"])
    with pytest.raises(AdapterUnavailable):                                     # an order is NOT re-sent elsewhere
        ad2.open_position(OpenRequest("tcx", "EURUSD", "buy", 0.01, sl=1.07))
    assert ad2.url == "http://testserver"                                       # next attempt uses the other one


def test_mt5_pair_api_orders_reachable_address_first(bridge, db, monkeypatch):
    import httpx
    from starlette.testclient import TestClient

    from titan.adapters.forex import mt5 as mt5mod
    from titan.api import app as appmod
    mod = sys.modules["mt5_bridge"]
    monkeypatch.setattr(mod, "_tailscale_ip", lambda: "100.64.0.9")
    monkeypatch.setattr(mod, "_lan_ip", lambda: None)
    real = mt5mod.MT5BridgeAdapter
    http = httpx.Client(transport=DownExceptTestserver(bridge))
    monkeypatch.setattr(mt5mod, "MT5BridgeAdapter", lambda u, sec, *a, **k: real(u, sec, http=http, **k))
    import base64
    import json
    payload = "titan-mt5:" + base64.urlsafe_b64encode(json.dumps(
        {"v": 1, "urls": ["http://100.64.0.9:8765", "http://testserver"], "secret": "s3cret-for-test",
         "name": "HOME-PC"}).encode()).decode().rstrip("=")
    appmod._pair_limits.clear()
    with TestClient(appmod.app) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/api/pair", json={"code": "TEST-CODE-1234"}).json()["token"]
        assert c.post("/api/connections/mt5-pair", json={"payload": "hello-not-a-titan-code"}).status_code == 400
        r = c.post("/api/connections/mt5-pair", json={"payload": payload}).json()
        assert r["reachable"] == ["http://testserver"] and r["account"]["equity"] == 500.0 and r["label"] == "MT5 HOME-PC"
        conns = c.get("/api/connections").json()
        assert conns[0]["venue"] == "mt5" and conns[0]["verify_result"]["ok"] is True
        assert "s3cret-for-test" not in str(conns)
    from sqlalchemy import select

    from titan.models import Credential
    from titan.security import decrypt_secret
    with db.session_scope() as s:
        cr = s.scalar(select(Credential).where(Credential.venue == "mt5"))
        f = decrypt_secret(cr.ciphertext, f"mt5|demo|{cr.label}")
        assert f["url"] == "http://testserver" and f["alt_urls"] == ["http://100.64.0.9:8765"]
