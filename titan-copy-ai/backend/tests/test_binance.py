"""Binance adapter contract tests against a mock HTTP transport (no network).

The mock enforces the documented request contract: X-MBX-APIKEY header, HMAC-SHA256 signature
over the exact query/body, timestamp + recvWindow, and endpoint paths per product.
"""
import hashlib
import hmac
from urllib.parse import parse_qsl

import httpx
import pytest

from titan.adapters.base import AdapterUnavailable, OpenRequest, VenuePosition
from titan.adapters.binance.adapter import BinanceAdapter
from titan.adapters.binance.client import BinanceClient
from titan.models import OrderStatus

KEY, SECRET = "demo-key", "demo-secret"
FILTERS = {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT", "status": "TRADING",
           "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                       {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                       {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
                       {"filterType": "MIN_NOTIONAL", "notional": "100"}]}


class FakeBinance:
    def __init__(self, fail_algo=False, drop_order=False):
        self.calls = []
        self.fail_algo, self.drop_order = fail_algo, drop_order

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        raw = request.content.decode() if request.method in ("POST", "PUT") else request.url.query.decode()
        params = dict(parse_qsl(raw))
        self.calls.append((request.method, request.url.host, path, params))
        if "signature" in params:
            assert request.headers["X-MBX-APIKEY"] == KEY
            unsigned = raw.rsplit("&signature=", 1)[0]
            good = hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
            if good != params["signature"]:
                return httpx.Response(400, json={"code": -1022, "msg": "Signature for this request is not valid."})
            assert "timestamp" in params and params["recvWindow"] == "5000"
        if path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": [FILTERS]})
        if path == "/fapi/v1/ticker/bookTicker":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "bidPrice": "90000.0", "askPrice": "90000.1"})
        if path in ("/fapi/v1/leverage", "/fapi/v1/marginType"):
            if path == "/fapi/v1/marginType":
                return httpx.Response(400, json={"code": -4046, "msg": "No need to change margin type."})
            return httpx.Response(200, json={"leverage": int(params["leverage"])})
        if path == "/fapi/v1/order" and request.method == "POST":
            if self.drop_order:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200, json={"orderId": 1, "clientOrderId": params["newClientOrderId"],
                                             "status": "FILLED", "executedQty": params["quantity"], "avgPrice": "90001.0"},
                                  headers={"X-MBX-USED-WEIGHT-1M": "12"})
        if path == "/fapi/v1/algoOrder" and request.method == "POST":
            if self.fail_algo:
                return httpx.Response(400, json={"code": -2021, "msg": "Order would immediately trigger."})
            assert params["algoType"] == "CONDITIONAL" and params["reduceOnly"] == "true"
            assert params["workingType"] == "MARK_PRICE" and "triggerPrice" in params
            return httpx.Response(200, json={"algoId": 9, "clientAlgoId": params["clientAlgoId"], "algoStatus": "NEW"})
        if path == "/fapi/v1/algoOrder" and request.method == "DELETE":
            return httpx.Response(200, json={"code": "200"})
        if path == "/fapi/v1/positionSide/dual":
            return httpx.Response(200, json={"dualSidePosition": False})
        if path == "/fapi/v2/account":
            return httpx.Response(200, json={"totalWalletBalance": "500", "totalMarginBalance": "500",
                                             "totalInitialMargin": "0", "availableBalance": "500"})
        if path == "/fapi/v2/positionRisk":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"code": -1, "msg": f"unexpected {path}"})


def make(fake) -> BinanceAdapter:
    c = BinanceClient("usdm", "demo", KEY, SECRET, http=httpx.Client(transport=httpx.MockTransport(fake)))
    ad = BinanceAdapter(c)
    ad.load_filters()
    return ad


def test_demo_hosts_are_default():
    assert BinanceClient("spot", "demo").base == "https://demo-api.binance.com"
    assert BinanceClient("usdm", "demo").base == "https://demo-fapi.binance.com"
    assert BinanceClient("coinm", "demo").base == "https://demo-dapi.binance.com"


def test_open_places_entry_and_reduce_only_protection():
    fake = FakeBinance()
    ad = make(fake)
    res = ad.open_position(OpenRequest("tcabc", "BTCUSDT", "buy", 0.0027, sl=89100.04, tp=91800))
    assert res.status == OrderStatus.FILLED and res.filled_qty == 0.002   # quantised to stepSize
    assert res.protection == {"sl": "tcabc-sl", "tp": "tcabc-tp"}
    hosts = {c[1] for c in fake.calls}
    assert hosts == {"demo-fapi.binance.com"}
    entry = next(c for c in fake.calls if c[2] == "/fapi/v1/order")
    assert entry[3]["type"] == "MARKET" and entry[3]["quantity"] == "0.002"
    sl = next(c for c in fake.calls if c[2] == "/fapi/v1/algoOrder" and c[3]["type"] == "STOP_MARKET")
    assert sl[3]["triggerPrice"] == "89100" and sl[3]["side"] == "SELL"   # tick 0.10 rounding
    lev = next(c for c in fake.calls if c[2] == "/fapi/v1/leverage")
    assert int(lev[3]["leverage"]) <= 3


def test_stop_loss_failure_closes_position_immediately():
    fake = FakeBinance(fail_algo=True)
    ad = make(fake)
    res = ad.open_position(OpenRequest("tcdef", "BTCUSDT", "buy", 0.002, sl=89100, tp=None))
    assert res.status == OrderStatus.REJECTED and "position closed" in res.error
    closes = [c for c in fake.calls if c[2] == "/fapi/v1/order" and c[3].get("reduceOnly") == "true"]
    assert len(closes) == 1 and closes[0][3]["side"] == "SELL"


def test_order_post_is_not_blindly_retried():
    fake = FakeBinance(drop_order=True)
    ad = make(fake)
    with pytest.raises(AdapterUnavailable):
        ad.open_position(OpenRequest("tcghi", "BTCUSDT", "buy", 0.002, sl=89100))
    posts = [c for c in fake.calls if c[2] == "/fapi/v1/order"]
    assert len(posts) == 1


def test_preflight_rejects_hedge_mode_and_checks_time():
    fake = FakeBinance()
    ad = make(fake)
    out = ad.preflight()
    assert out["hedge_mode"] is False and "time_offset_ms" in out and out["equity"] == 500


def test_close_partial_replaces_protection():
    fake = FakeBinance()
    ad = make(fake)
    pos = VenuePosition("BTCUSDT", "BTCUSDT", "buy", 0.004, 90000, sl=89000, tp=92000,
                        protection={"sl": "tcx-sl", "tp": "tcx-tp"})
    res = ad.close_position(pos, 0.002, "tcclose")
    assert res.status == OrderStatus.FILLED
    cancels = [c for c in fake.calls if c[0] == "DELETE"]
    assert {c[3]["clientAlgoId"] for c in cancels} == {"tcx-sl", "tcx-tp"}
    assert res.protection["sl"] == "tcclose-r-sl"


def test_geo_block_is_reported_as_unavailable():
    c = BinanceClient("usdm", "demo", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(451, json={"code": 0, "msg": "Service unavailable from a restricted location"}))))
    with pytest.raises(AdapterUnavailable, match="451"):
        c.ping()
