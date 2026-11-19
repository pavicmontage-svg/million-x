import json
import sqlite3

import pytest

from millionx.org.llm import LLMResult
from millionx.org.organization import AGENTS, Organization
from millionx.security import (Vault, authenticate, can, create_pairing_code, load_api_keys, redeem_pairing_code,
                               store_api_keys)
from millionx.storage import Store


def test_audit_chain_and_immutability(tmp_path):
    st = Store(str(tmp_path / "a.db"))
    for i in range(5):
        st.audit("tester", f"act{i}", {"i": i})
    assert st.verify_audit() == {"ok": True, "checked": 5}
    with pytest.raises(sqlite3.DatabaseError):
        st.x("UPDATE audit SET actor='x' WHERE seq=1")
    with pytest.raises(sqlite3.DatabaseError):
        st.x("DELETE FROM audit")
    st.insert_trade({"id": "t1", "bot_id": "b", "symbol": "X", "side": "LONG", "qty": 1, "entry_price": 1,
                     "exit_price": 1, "opened_at": 0, "closed_at": 1, "gross_pnl": 0, "fees": 0, "funding": 0,
                     "net_pnl": 0, "r_multiple": 0, "risk_amount": 1, "exit_reason": "x", "regime": "",
                     "virtual": False, "mode": "paper"})
    with pytest.raises(sqlite3.DatabaseError):
        st.x("UPDATE trades SET net_pnl=100")


def test_vault_and_pairing(tmp_path):
    st = Store(str(tmp_path / "v.db"))
    v = Vault(Vault.generate())
    store_api_keys(st, v, "binance", "demo", "KEY", "SECRET")
    raw = st.one("SELECT key_enc FROM api_keys")["key_enc"]
    assert b"KEY" not in raw
    assert load_api_keys(st, v, "binance", "demo") == ("KEY", "SECRET")
    code = create_pairing_code(st, "viewer")
    r = redeem_pairing_code(st, code, "S25 Ultra")
    assert r and r["role"] == "viewer"
    assert redeem_pairing_code(st, code, "again") is None, "codes are single use"
    dev = authenticate(st, r["token"])
    assert dev["role"] == "viewer"
    assert can("viewer", "emergency_stop") and not can("viewer", "change_risk_limits")
    assert not can("viewer", "edit_prompts") and can("owner", "edit_prompts")


class FakeLLM:
    available = True

    def __init__(self, payload):
        self.payload = payload

    def complete(self, system, user, model, max_tokens, temperature):
        assert "OUTPUT CONTRACT" in system
        return LLMResult(json.dumps(self.payload), 100, 50, None, 5, model)


def test_org_prompts_versioned(tmp_path):
    st = Store(str(tmp_path / "o.db"))
    org = Organization(st)
    assert len(AGENTS) == 11
    assert org.prompt("brain_risk")["version"] == 1
    v = org.update_prompt("brain_risk", "new prompt", "owner:test", "tune")
    assert v == 2 and org.prompt("brain_risk")["prompt"] == "new prompt"
    org.activate_version("brain_risk", 1, "owner:test")
    assert org.prompt("brain_risk")["version"] == 1
    assert any(r["action"] == "prompt_updated" for r in st.q("SELECT action FROM audit"))


def test_org_deterministic_when_offline(tmp_path):
    st = Store(str(tmp_path / "o.db"))
    org = Organization(st, llm=type("Off", (), {"available": False})())
    out = org.run_agent("brain_quant", None)
    assert out["mode"] == "deterministic" and "LLM offline" in out["note"]


def test_untraceable_llm_output_is_flagged(tmp_path):
    st = Store(str(tmp_path / "o.db"))
    org = Organization(st, llm=FakeLLM({"summary": "s", "findings": [{"text": "made up", "evidence_ids": ["ev-999"]}],
                                        "recommendations": [], "stance": {"risk": "hold"}, "confidence": 0.5}))
    out = org.run_agent("brain_market", None)
    assert out["mode"] == "llm"
    assert out["findings"][0]["traceable"] is False
    assert out["untraceable_items"] == ["made up"]
    row = st.one("SELECT input_tokens, output_tokens FROM agent_runs")
    assert row["input_tokens"] == 100 and row["output_tokens"] == 50


def test_only_tightening_is_auto_applied(tmp_path, clock, monkeypatch):
    import millionx.engine.live as live
    from millionx.config import Settings
    from conftest import FakeMarket
    monkeypatch.setattr(live, "now_ms", clock)
    st = Store(str(tmp_path / "o.db"))
    eng = live.TradingEngine(Settings(), st, FakeMarket(clock))
    payload = {"summary": "s", "findings": [], "stance": {"risk": "tighten"}, "confidence": 0.7,
               "recommendations": [
                   {"action": "risk_scale", "target": "2.0", "direction": "loosen", "evidence_ids": ["ev-001-risk.state"]},
                   {"action": "risk_scale", "target": "0.5", "direction": "tighten", "evidence_ids": ["ev-001-risk.state"]},
               ]}
    org = Organization(st, llm=FakeLLM(payload))
    pk = org.packet("brain_risk", eng)
    payload["recommendations"][1]["evidence_ids"] = [pk["items"][0]["id"]]
    payload["recommendations"][0]["evidence_ids"] = [pk["items"][0]["id"]]
    org.run_agent("brain_risk", eng)
    assert eng.risk.tighten_factor == 0.5
    # executives without apply rights cannot change anything
    org2 = Organization(st, llm=FakeLLM({**payload, "recommendations": [
        {"action": "suspend_all", "target": "all", "direction": "tighten", "evidence_ids": [pk["items"][0]["id"]]}]}))
    org2.run_agent("exec_ceo", eng)
    assert eng.risk.tighten_factor == 0.5


def test_board_meeting_records_disagreements(tmp_path):
    st = Store(str(tmp_path / "m.db"))

    class Alt:
        available = True
        n = 0

        def complete(self, *a, **k):
            Alt.n += 1
            stance = "tighten" if Alt.n % 2 else "hold"
            return LLMResult(json.dumps({"summary": "x", "findings": [], "recommendations": [],
                                         "stance": {"risk": stance}, "confidence": 0.5}), 1, 1, None, 1, "m")

    org = Organization(st, llm=Alt())
    m = org.board_meeting(None)
    assert m["disagreements"] and m["disagreements"][0]["topic"] == "risk"
    assert st.one("SELECT COUNT(*) n FROM meetings")["n"] == 1
