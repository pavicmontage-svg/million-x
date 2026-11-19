"""API smoke test over real HTTP + WebSocket-less endpoints (engine not started)."""
import json
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn

from millionx.api import create_app
from millionx.config import Settings
from millionx.runtime import Runtime
from millionx.security import Vault, create_pairing_code
from millionx.storage import Store


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("api") / "api.db")
    st = Store(db)
    rt = Runtime(Settings(db_path=db), st, Vault(Vault.generate()))
    app = create_app(rt)
    port = _free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", st
    srv.should_exit = True


def call(base, path, token=None, method="GET", body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_auth_and_rbac(server):
    base, st = server
    assert call(base, "/api/health")[0] == 200
    assert call(base, "/api/dashboard")[0] == 401
    owner = call(base, "/api/pair", method="POST", body={"code": create_pairing_code(st, "owner"), "device_name": "S25U"})[1]
    viewer = call(base, "/api/pair", method="POST", body={"code": create_pairing_code(st, "viewer"), "device_name": "tab"})[1]
    for path in ("/api/dashboard", "/api/bots", "/api/risk", "/api/org/agents", "/api/audit", "/api/settings",
                 "/api/trades", "/api/performance", "/api/notifications", "/api/backtests", "/api/org/meetings"):
        code, data = call(base, path, owner["token"])
        assert code == 200, (path, data)
    code, bots = call(base, "/api/bots", owner["token"])
    assert len(bots) == 10
    code, agents = call(base, "/api/org/agents", owner["token"])
    assert len(agents) == 11
    # viewer cannot loosen limits or edit prompts, owner can tighten limits
    assert call(base, "/api/risk/limits", viewer["token"], "PUT", {"max_daily_loss": 0.5})[0] == 403
    assert call(base, "/api/org/agents/brain_risk/prompt", viewer["token"], "PUT", {"prompt": "x"})[0] == 403
    assert call(base, "/api/risk/limits", owner["token"], "PUT", {"max_leverage": 50})[0] == 400
    assert call(base, "/api/risk/limits", owner["token"], "PUT", {"max_daily_loss": 0.02})[0] == 200
    code, v = call(base, "/api/org/agents/brain_risk/prompt", owner["token"], "PUT", {"prompt": "v2", "note": "t"})
    assert code == 200 and v["version"] == 2
    assert call(base, "/api/settings", owner["token"], "PUT", {"mode": "live"})[0] == 400, "live locked"
    assert call(base, "/api/audit/verify", owner["token"])[1]["ok"]
