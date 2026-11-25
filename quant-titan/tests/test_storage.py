import os
import uuid

import pytest
from cryptography.fernet import Fernet

from quanttitan.core.types import TradeRecord
from quanttitan.security.secrets import SecretStore
from quanttitan.storage.db import Database

URLS = ["sqlite:///:memory:"] + ([os.environ["QT_TEST_POSTGRES"]] if os.environ.get("QT_TEST_POSTGRES") else [])


@pytest.fixture(params=URLS, ids=lambda u: u.split(":")[0])
def db(request):
    d = Database(request.param)
    if d.kind == "postgres":
        for t in ("kv", "trades", "alerts", "audit", "secrets", "config_versions", "equity"):
            d.execute(f"DELETE FROM {t}")
    return d


def test_kv_and_upsert(db):
    db.kv_set("a", {"x": 1})
    db.kv_set("a", {"x": 2})
    assert db.kv_get("a") == {"x": 2} and db.kv_get("missing", 5) == 5


def test_trades_roundtrip(db):
    t = TradeRecord(f"t{uuid.uuid4().hex[:6]}", "BTCUSDT", "LONG", "vwap", "S", "RANGE", 100, 101, 1, 1, 2, 1, 0.1, 0, 0, 0.9, 0.9, 1, "take_profit_1")
    db.save_trade(t)
    db.save_trade(t)  # idempotent
    rows = db.trades(10)
    assert len(rows) == 1 and rows[0]["net_pnl"] == 0.9


def test_alerts_audit_versions_equity(db):
    i1 = db.add_alert("k", "info", "m1")
    i2 = db.add_alert("k", "critical", "m2")
    assert i2 > i1 and [a["message"] for a in db.alerts(after_id=i1)] == ["m2"]
    db.audit("admin", "x", {"a": 1}, "1.2.3.4")
    assert db.audit_log()[0]["action"] == "x"
    v = db.save_config_version({"a": 1}, "admin", "n")
    assert db.config_version(v) == {"a": 1}
    db.save_equity(1, 100, 100, 0, 0)
    db.save_equity(1, 101, 100, 1, 0)
    assert db.equity_curve()[0]["equity"] == 101


def test_secrets_encrypted_at_rest(db):
    s = SecretStore(db, Fernet.generate_key())
    s.set("k", "super-secret-value-123")
    raw = db.query("SELECT ciphertext FROM secrets WHERE name='k'")[0]["ciphertext"]
    assert "super-secret" not in raw
    assert s.get("k") == "super-secret-value-123"
    assert s.preview("k")["preview"] != "super-secret-value-123"
    other = SecretStore(db, Fernet.generate_key())
    with pytest.raises(RuntimeError):
        other.get("k")
