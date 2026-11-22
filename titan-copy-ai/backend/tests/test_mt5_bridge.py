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
