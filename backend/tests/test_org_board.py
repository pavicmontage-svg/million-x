"""Board meeting agenda, inter-agent communication and CRO veto (Phase 4)."""
import json

from millionx.org.llm import LLMResult
from millionx.org.organization import Organization
from millionx.storage import Store


class ScriptedLLM:
    """Returns JSON based on the task line; records every call (to prove calls happened)."""
    available = True

    def __init__(self):
        self.calls = []

    def complete(self, system, user, model, max_tokens, temperature):
        self.calls.append(user[:120])
        task = user.split("\n")[0]
        evid = json.loads(user[user.index("EVIDENCE PACKET:\n") + 17:])["items"][0]["id"]
        rec, stance = [], {"risk": "hold", "activity": "hold"}
        if "CRO risk assessment" in task:
            stance = {"risk": "tighten", "activity": "reduce"}
        if "Final CEO decision" in task:
            rec = [{"action": "risk_scale", "target": "2.0", "direction": "loosen", "rationale": "more profit",
                    "evidence_ids": [evid]},
                   {"action": "suspend_bot", "target": "bot3_orderflow", "direction": "tighten",
                    "rationale": "degrading", "evidence_ids": [evid]},
                   {"action": "review_params", "target": "bot2_ema", "direction": "info", "rationale": "check",
                    "evidence_ids": [evid]}]
        if "CRO review" in task:
            rec = [{"action": "veto", "target": "2", "direction": "tighten", "rationale": "no evidence",
                    "evidence_ids": [evid]}]
        out = {"summary": task[:60], "findings": [{"text": "x", "evidence_ids": [evid]}], "recommendations": rec,
               "stance": stance, "confidence": 0.6}
        return LLMResult(json.dumps(out), 10, 5, None, 1, model)


def test_agenda_order_discussion_and_cro_veto(tmp_path):
    st = Store(str(tmp_path / "b.db"))
    llm = ScriptedLLM()
    org = Organization(st, llm=llm)
    m = org.board_meeting(None)
    titles = [i["title"] for i in m["agenda"]]
    assert titles[:5] == ["CEO opening report", "CIO capital report", "Quantitative research report",
                          "CRO risk assessment", "COO operational assessment"]
    assert titles[5:] == ["Discussion of disagreements", "Final CEO decision", "Independent CRO approval or veto"]
    assert m["disagreements"], "CRO stance differs from the others"
    assert m["agenda"][5]["discussion"], "an LLM discussion round must run when there are disagreements"
    v = {d["action"]: d for d in m["decisions"]}
    assert v["risk_scale"]["cro"] == "vetoed_by_cro" and "loosening" in v["risk_scale"]["cro_reason"]
    assert v["review_params"]["cro"] == "vetoed_by_cro" and "CRO agent veto" in v["review_params"]["cro_reason"]
    assert v["suspend_bot"]["status"] == "awaiting owner decision"  # no engine attached -> nothing applied
    msgs = st.q("SELECT sender, kind FROM agent_messages WHERE meeting_id=?", (m["id"],))
    assert len(msgs) >= 8
    # later speakers received earlier reports (inter-agent communication)
    run = st.one("SELECT input FROM agent_runs WHERE agent_id='exec_coo' AND kind='board:operational_assessment'")
    assert "board.opening_report.exec_ceo" in run["input"]
    assert len(llm.calls) == st.one("SELECT COUNT(*) n FROM agent_runs")["n"], "every run is a real call"


def test_offline_meeting_is_explicitly_deterministic(tmp_path):
    st = Store(str(tmp_path / "d.db"))
    org = Organization(st, llm=type("Off", (), {"available": False})())
    m = org.board_meeting(None)
    assert m["llm"] is False
    assert all(i.get("mode") in (None, "deterministic") for i in m["agenda"])
    assert "LLM offline" in (m["agenda"][5]["note"] or "LLM offline")
    assert st.one("SELECT SUM(input_tokens) t FROM agent_runs")["t"] == 0, "no tokens: no model was called"
