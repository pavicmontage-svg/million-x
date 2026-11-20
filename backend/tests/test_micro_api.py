"""2.1 microstructure API endpoints (own server: the shared one hits the pairing rate limit)."""
from test_api import call, server  # noqa: F401  (fixture re-used, new module scope)
from millionx.security import create_pairing_code


def test_micro_endpoints(server):
    base, st = server
    owner = call(base, "/api/pair", method="POST", body={"code": create_pairing_code(st, "owner"), "device_name": "m"})[1]
    viewer = call(base, "/api/pair", method="POST", body={"code": create_pairing_code(st, "viewer"), "device_name": "v"})[1]
    assert "token" in owner, owner
    code, s = call(base, "/api/micro/status", owner["token"])
    assert code == 200 and s["running"] is False
    code, snap = call(base, "/api/micro/snapshot/BTCUSDT", owner["token"])
    assert code == 200 and snap["status"] == "INCOMPLETE" and snap["fields"]["order_book"]["missing"]
    code, ser = call(base, "/api/micro/series/BTCUSDT", owner["token"])
    assert code == 200 and ser["rows"] == []
    code, r = call(base, "/api/micro/research", owner["token"])
    assert code == 200 and "INCOMPLETE" in r["note"]
    assert call(base, "/api/micro/collector", viewer["token"], "POST", {"enabled": True})[0] == 403
