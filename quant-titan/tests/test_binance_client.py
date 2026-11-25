import hashlib
import hmac
import json
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from quanttitan.core.logging import redact
from quanttitan.execution.binance_futures import BinanceFutures
from quanttitan.execution.exchange import ExchangeError, OrderRequest

KEY, SECRET = "testkey_ABCDEFGH12345678", "testsecret_ZYXWVUT987654321"


def make(handler):
    return BinanceFutures(KEY, SECRET, "TESTNET", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_signature_and_algo_routing():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        q = urlsplit(str(req.url)).query
        body, sig = q.rsplit("&signature=", 1)
        assert hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest() == sig
        assert req.headers["X-MBX-APIKEY"] == KEY
        p = dict(parse_qsl(body))
        if req.url.path == "/fapi/v1/algoOrder":
            assert p["algoType"] == "CONDITIONAL" and p["triggerPrice"] == "98.5" and p["reduceOnly"] == "true"
            return httpx.Response(200, json={"algoId": 777, "clientAlgoId": p["clientAlgoId"], "algoStatus": "NEW"})
        assert req.url.path == "/fapi/v1/order"
        return httpx.Response(200, json={"clientOrderId": p["newClientOrderId"], "symbol": "BTCUSDT", "side": "BUY",
                                         "type": "MARKET", "status": "FILLED", "origQty": "1", "executedQty": "1",
                                         "avgPrice": "100.1", "orderId": 5})

    c = make(handler)
    m = await c.place_order(OrderRequest("BTCUSDT", "BUY", "MARKET", 1.0, client_id="qtabcE"))
    assert m.status == "FILLED" and m.avg_price == 100.1
    s = await c.place_order(OrderRequest("BTCUSDT", "SELL", "STOP_MARKET", 1.0, stop_price=98.5, reduce_only=True, client_id="qtabcS1"))
    assert s.status == "NEW" and s.exchange_id == "777" and s.client_id == "qtabcS1"
    assert KEY not in repr(c) and SECRET not in repr(c)


async def test_timeouts_are_ambiguous_and_errors_mapped():
    def timeout(req):
        raise httpx.ReadTimeout("t", request=req)

    with pytest.raises(ExchangeError) as e:
        await make(timeout).place_order(OrderRequest("BTCUSDT", "BUY", "MARKET", 1, client_id="x"))
    assert e.value.unknown_state and e.value.retriable

    def reject(req):
        return httpx.Response(400, json={"code": -2019, "msg": "Margin is insufficient."})

    with pytest.raises(ExchangeError) as e2:
        await make(reject).place_order(OrderRequest("BTCUSDT", "BUY", "MARKET", 1, client_id="y"))
    assert e2.value.code == -2019 and not e2.value.retriable and not e2.value.unknown_state


async def test_hedge_mode_refused():
    def h(req):
        return httpx.Response(200, json={"dualSidePosition": True})

    with pytest.raises(ExchangeError, match="hedge"):
        await make(h).check_account_setup()


def test_secrets_redacted_in_logs():
    BinanceFutures(KEY, SECRET, "TESTNET")
    line = f"request failed apiKey={KEY} body secret={SECRET} sig signature=abcdef1234"
    out = redact(line)
    assert KEY not in out and SECRET not in out and "abcdef1234" not in out
