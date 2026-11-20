"""AI provider failover, quota exhaustion, output validation, Independent Trader and
AI Supervisor going through the deterministic risk engine (all HTTP mocked)."""
import json

import pytest

import millionx.engine.live as live
import millionx.exchange.paper as paper_mod
import millionx.engine.execution as exec_mod
from millionx.ai import providers as P
from millionx.ai.desk import AIDesk, check_trade_decision
from millionx.config import Settings
from millionx.exchange.paper import PaperExchange
from millionx.models import MINUTE_MS
from millionx.security import Vault
from millionx.storage import Store

from conftest import FakeMarket


class R:
    def __init__(self, code=200, body=None, headers=None, text=""):
        self.status_code, self._b, self.headers, self.text = code, body, headers or {}, text or json.dumps(body)

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


class FakeHTTP:
    """Scripted provider behaviour by host: list of responses consumed per call."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def _host(self, url):
        return next(h for h in ("groq", "googleapis", "openrouter") if h in url)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url))
        h = self._host(url)
        models = {"groq": [{"id": "openai/gpt-oss-120b"}, {"id": "whisper-large-v3"}],
                  "googleapis": [{"id": "models/gemini-x-flash"}, {"id": "models/gemini-x-pro"}],
                  "openrouter": [{"id": "vendor/big-model", "pricing": {"prompt": "0.001", "completion": "0.002"}},
                                 {"id": "vendor/small:free", "pricing": {"prompt": "0", "completion": "0"}}]}[h]
        return R(200, {"data": models})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json["model"]))
        h = self._host(url)
        seq = self.script.get(h, [])
        r = seq.pop(0) if seq else R(500, None, text="down")
        return r


def ok_reply(content, model="m"):
    return R(200, {"model": model, "choices": [{"message": {"content": content}}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


@pytest.fixture
def chain(tmp_path):
    st = Store(str(tmp_path / "a.db"))
    v = Vault(Vault.generate())

    def make(script):
        c = P.ProviderChain(st, v, http=FakeHTTP(script))
        for p in c.providers.values():
            p.spec = P.ProviderSpec(**{**p.spec.__dict__, "min_gap_s": 0.0})
        for pid in P.ORDER:
            c.configure(pid, key=f"key-{pid}-1234567890")
        return c
    return make, st


def test_model_discovery_prefers_free_and_skips_audio(chain):
    make, st = chain
    c = make({})
    for p in c.providers.values():
        assert p.health_check()["ok"]
    assert c.providers["groq"].choose_model() == "openai/gpt-oss-120b"
    assert c.providers["gemini"].choose_model() == "gemini-x-flash"
    assert c.providers["openrouter"].choose_model() == "vendor/small:free"  # paid model never chosen


def test_failover_on_quota_then_invalid_json(chain):
    make, st = chain
    c = make({"groq": [R(429, {"error": "rate"}, {"retry-after": "120"})],
              "googleapis": [ok_reply("not json at all")],
              "openrouter": [ok_reply('{"ok": true}')]})
    res = c.complete("s", "u", validate=P.parse_json_obj, purpose="t")
    assert res.ok and res.provider == "openrouter" and res.data == {"ok": True}
    assert [a.get("provider") for a in res.attempts] == ["groq", "gemini", "openrouter"]
    assert c.providers["groq"].cooldown_until > 0 and not c.providers["groq"].available()
    u = c.usage_today()
    assert u["groq"]["errors"] == 1 and u["gemini"]["errors"] == 1 and u["openrouter"]["ok"] == 1
    assert u["openrouter"]["input_tokens"] == 100
    errs = st.q("SELECT provider, error FROM ai_errors")
    assert {e["provider"] for e in errs} == {"groq", "gemini"}
    # quota exhausted provider is skipped next time without a call
    n_groq_posts = sum(1 for x in c.providers["groq"].http.calls if x[0] == "POST" and "groq" in x[1])
    c.complete("s", "u", validate=P.parse_json_obj)
    assert sum(1 for x in c.providers["groq"].http.calls if x[0] == "POST" and "groq" in x[1]) == n_groq_posts


def test_all_providers_down_is_safe(chain):
    make, st = chain
    c = make({"groq": [R(401, {"e": 1})], "googleapis": [R(500, None, text="x"), R(500, None, text="x")],
              "openrouter": [R(503, None, text="x"), R(503, None, text="x")]})
    res = c.complete("s", "u", validate=P.parse_json_obj)
    assert not res.ok and res.error == "all AI providers unavailable"
    assert c.providers["groq"].unhealthy_reason.startswith("key rejected")


def test_keys_encrypted_and_scrubbed(chain):
    make, st = chain
    make({})
    raw = json.dumps(st.kv_get("ai_providers"))
    assert "key-groq" not in raw and "key_enc" in raw
    assert "gsk_" not in P.scrub("error with key gsk_abcdefghijklmnop in it")
    assert P.scrub("Authorization: Bearer abc.def") == "Authorization: ***"


def test_trader_decision_validation():
    ok, why, n = check_trade_decision({"instrument": "BTCUSDT", "direction": "LONG", "entry": 100, "stop_loss": 99,
                                       "take_profit": 102, "confidence": 0.7, "valid_minutes": 15}, 100.1)
    assert ok and n["risk_reward"] == 2.0
    bad = [{"direction": "LONG", "entry": 100, "stop_loss": 101, "take_profit": 102},          # stop wrong side
           {"direction": "SHORT", "entry": 100, "stop_loss": 101, "take_profit": 99.5},       # RR 0.5
           {"direction": "LONG", "entry": 110, "stop_loss": 109, "take_profit": 113},         # far from price
           {"direction": "BUY", "entry": 100, "stop_loss": 99, "take_profit": 102}]
    for b in bad:
        assert not check_trade_decision({"instrument": "BTCUSDT", "confidence": 0.7, **b}, 100.0)[0], b
    assert check_trade_decision({"instrument": "BTCUSDT", "direction": "NO_TRADE"}, 100.0)[0]


# --------------------------------------------------------------- engine flow
class ScriptChain:
    """Stands in for ProviderChain: returns scripted JSON per purpose."""

    def __init__(self, replies, configured=True):
        self.replies = replies
        self.paused = False
        self.any_configured = configured
        self.calls = []

    def complete(self, system, user, validate=None, max_tokens=0, temperature=0, purpose=""):
        self.calls.append(purpose)
        r = self.replies.get(purpose)
        if callable(r):
            r = r(user)
        if r is None:
            return P.ChainResult(ok=False, error="all AI providers unavailable")
        return P.ChainResult(ok=True, text=r, data=validate(r) if validate else None, provider="groq", model="m")


@pytest.fixture
def engine(tmp_path, clock, monkeypatch):
    for m in (live, paper_mod, exec_mod):
        monkeypatch.setattr(m, "now_ms", clock)
    import millionx.ai.desk as desk_mod
    monkeypatch.setattr(desk_mod, "now_ms", clock)
    s = Settings(mode="paper", execution="auto", capital_mode="pro")
    s.risk.min_depth_usdt = 1.0
    s.risk.max_spread_bps = 5
    store = Store(str(tmp_path / "e.db"))
    eng = live.TradingEngine(s, store, PaperExchange(FakeMarket(clock), 540.0, s.fees))
    for _ in range(300):
        clock.t += MINUTE_MS
        eng.tick()
    return eng, clock, store


def _trader_reply(eng, sym, conf=0.8):
    px = float(eng.bars[sym].close[-1])

    def make(user):
        pid = json.loads(user.split("\n", 1)[1])["market_data_id"]
        return json.dumps({"market_data_id": pid, "decisions": [
            {"instrument": sym, "direction": "LONG", "entry": px, "stop_loss": px * 0.99, "take_profit": px * 1.03,
             "risk_reward": 3, "confidence": conf, "reasoning": "test", "invalidation": "close below stop",
             "valid_minutes": 15}]})
    return make


def test_v2_trader_is_advisory_only(engine):
    """2.0 rule: the LLM's trade ideas are recorded, never submitted, and the switch is not settable."""
    eng, clock, store = engine
    sym = eng.universe[0]
    ch = ScriptChain({"independent_trader": _trader_reply(eng, sym)})
    desk = AIDesk(store, ch)
    eng.ai = desk
    out = desk.run_trader(eng)
    assert out["ok"] and out["decisions"][0]["status"] == "advisory_recorded"
    assert ch.calls == ["independent_trader"] and not eng.pending and sym not in eng.positions
    assert desk.update_settings(advisory_only=False)["advisory_only"] is True


@pytest.fixture
def executing(monkeypatch):
    """1.9.0 pipeline mechanics (kept for the supervisor gate), with advisory mode lifted in the test."""
    import millionx.ai.desk as desk_mod
    monkeypatch.setattr(desk_mod, "ADVISORY_ONLY", False)


def test_trader_signal_goes_through_supervisor_and_risk_engine(engine, executing):
    eng, clock, store = engine
    sym = eng.universe[0]
    ch = ScriptChain({"independent_trader": _trader_reply(eng, sym),
                      "supervisor": json.dumps({"decision": "APPROVE", "max_leverage": 5,
                                                "reasons": ["structure and liquidity agree"]})})
    desk = AIDesk(store, ch)
    desk.pool = type("Sync", (), {"submit": staticmethod(lambda fn, *a: fn(*a))})()
    eng.ai = desk
    out = desk.run_trader(eng)
    assert out["ok"] and out["decisions"][0]["status"] == "submitted"
    steps = [json.loads(r["evidence"]) for r in store.q("SELECT evidence FROM audit WHERE action='trade_decision'")]
    assert ch.calls == ["independent_trader", "supervisor"], steps[-1:]
    assert ch.calls == ["independent_trader", "supervisor"], "trader decides first, then the supervisor"
    rows = store.q("SELECT agent, decision, status FROM ai_decisions ORDER BY rowid")
    assert rows[0]["agent"] == "independent_trader" and rows[-1]["agent"] == "supervisor"
    assert ("ai_trader", sym) in eng.virtual, "AI trader is paper-traded for objective comparison"
    dec = store.q("SELECT approval FROM audit WHERE action='trade_decision' ORDER BY seq")
    outcomes = [d["approval"] for d in dec]
    assert "awaiting_ai_supervisor" in outcomes
    # approved -> re-sized by the risk engine with the supervisor's leverage cap -> executed
    assert sym in eng.positions and eng.positions[sym].leverage <= 5


def test_supervisor_rejects_and_ai_down_means_no_trade(engine, executing):
    eng, clock, store = engine
    sym = eng.universe[0]
    ch = ScriptChain({"independent_trader": _trader_reply(eng, sym),
                      "supervisor": json.dumps({"decision": "REJECT", "reasons": ["late entry"]})})
    desk = AIDesk(store, ch)
    desk.pool = type("Sync", (), {"submit": staticmethod(lambda fn, *a: fn(*a))})()
    eng.ai = desk
    desk.run_trader(eng)
    assert sym not in eng.positions and not eng.pending
    ch.replies["supervisor"] = None  # every provider down
    eng.risk.state.consecutive_losses = 0
    clock.t += 20 * MINUTE_MS
    eng.tick()
    ch.replies["independent_trader"] = _trader_reply(eng, eng.universe[-1])
    desk.run_trader(eng)
    last = store.q("SELECT decision, status FROM ai_decisions WHERE agent='supervisor' ORDER BY ts DESC LIMIT 1")[0]
    assert last["decision"] == "NO_TRADE" and last["status"] == "ai_unavailable"
    assert not eng.positions


def test_supervisor_cannot_approve_without_reasons(engine, executing):
    eng, clock, store = engine
    from millionx.ai.desk import SUPERVISOR_PROMPT  # noqa: F401
    sym = eng.universe[0]
    ch = ScriptChain({"independent_trader": _trader_reply(eng, sym),
                      "supervisor": json.dumps({"decision": "APPROVE", "reasons": []})})
    desk = AIDesk(store, ch)
    desk.pool = type("Sync", (), {"submit": staticmethod(lambda fn, *a: fn(*a))})()
    eng.ai = desk
    desk.run_trader(eng)  # validator raises -> the real chain fails over; here the review errors -> rejected
    assert sym not in eng.positions and not eng.pending
    assert store.q("SELECT 1 FROM audit WHERE action='ai_supervisor_error'")
