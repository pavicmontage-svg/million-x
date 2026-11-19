"""Server hardening and recovery (Phase 7)."""
import sqlite3
import time

import pytest

import millionx.security as sec
from millionx.storage import SCHEMA, Store


def test_migration_from_pre_migration_database(tmp_path):
    p = str(tmp_path / "old.db")
    db = sqlite3.connect(p)
    db.executescript(SCHEMA)          # a v1 database created by the first release
    db.execute("INSERT INTO trades(id,bot_id,symbol,side,qty,entry_price,exit_price,opened_at,closed_at,gross_pnl,fees,"
               "funding,net_pnl,r_multiple,risk_amount,exit_reason,regime,virtual,mode) "
               "VALUES('t','b','X','LONG',1,1,1,0,1,0,0,0,0,0,1,'x','',0,'paper')")
    db.commit()
    db.close()
    st = Store(p)
    assert st.schema_version() == 2
    assert st.one("SELECT COUNT(*) n FROM trades")["n"] == 1, "existing data preserved"
    cols = {r["name"] for r in st.q("PRAGMA table_info(orders)")}
    assert {"filled_qty", "avg_price"} <= cols
    assert Store(p).schema_version() == 2, "re-running migrations is a no-op"


def test_backup_is_consistent_and_rotated(tmp_path):
    st = Store(str(tmp_path / "a.db"))
    st.audit("x", "y")
    for _ in range(3):
        path = st.backup(str(tmp_path / "bk"), keep=2)
        time.sleep(1.1)
    import os
    files = os.listdir(tmp_path / "bk")
    assert len(files) == 2
    assert Store(path).verify_audit()["ok"]


def test_pairing_throttled_after_failures(tmp_path):
    st = Store(str(tmp_path / "p.db"))
    for _ in range(sec.PAIR_FAILS_PER_IP):
        assert sec.redeem_pairing_code(st, "WRONG-CODE-0000", "x", ip="1.2.3.4") is None
    good = sec.create_pairing_code(st, "owner")
    assert sec.redeem_pairing_code(st, good, "x", ip="1.2.3.4") is None, "throttled even with a valid code"
    assert sec.redeem_pairing_code(st, good, "x", ip="5.6.7.8") is not None, "other sources unaffected"


def test_device_token_expires(tmp_path, monkeypatch):
    st = Store(str(tmp_path / "t.db"))
    r = sec.redeem_pairing_code(st, sec.create_pairing_code(st, "viewer"), "phone", ip="1.1.1.1")
    assert sec.authenticate(st, r["token"])
    st.x("UPDATE devices SET expires_at=?", (int(time.time() * 1000) - 1,))
    assert sec.authenticate(st, r["token"]) is None


def test_master_key_file_permissions(tmp_path, monkeypatch):
    f = tmp_path / "mk"
    f.write_text(sec.Vault.generate())
    f.chmod(0o644)
    monkeypatch.delenv("MX_MASTER_KEY", raising=False)
    monkeypatch.setenv("MX_MASTER_KEY_FILE", str(f))
    with pytest.raises(RuntimeError):
        sec.Vault()
    f.chmod(0o600)
    assert sec.Vault().dec(sec.Vault().enc("x")) == "x"


def test_watchdog_restarts_dead_engine(tmp_path, monkeypatch):
    from millionx.config import Settings
    from millionx.runtime import Runtime
    st = Store(str(tmp_path / "w.db"))
    rt = Runtime(Settings(db_path=str(tmp_path / "w.db")), st, None)
    starts = []
    monkeypatch.setattr(rt, "start_engine", lambda: starts.append(1))

    class Dead:
        thread = None
        last_tick_ms = 0
    rt.engine = Dead()
    rt.start_watchdog(interval_s=0.05, backup_dir=str(tmp_path / "bk"))
    time.sleep(0.3)
    rt._wd_stop = True
    assert starts, "watchdog must restart a dead engine"
    assert st.kv_get("last_backup") and st.kv_get("audit_verify")["ok"]


def test_live_mode_not_resumed_after_restart_without_valid_unlock(tmp_path):
    from millionx.config import Settings
    from millionx.runtime import Runtime
    st = Store(str(tmp_path / "r.db"))
    st.kv_set("runtime_settings", {"mode": "live", "execution": "auto", "capital_mode": "multi"})
    rt = Runtime(Settings(db_path=str(tmp_path / "r.db")), st, None)
    assert rt.s.mode == "paper"
    assert st.one("SELECT COUNT(*) n FROM audit WHERE action='live_not_resumed'")["n"] == 1
