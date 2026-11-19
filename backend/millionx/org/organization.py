"""MILLION-X AI organisation: six brains + five executives.

Design rules (enforced in code, not only in prompts):
  * Agents are advisory. They never place orders and have no API token.
  * Each agent has its own versioned system prompt, model config, logs,
    decision history, confidence, token/cost tracking and error monitoring.
  * Every agent input is an EVIDENCE PACKET built from real system data, with
    an id per item. Outputs citing unknown ids are flagged as untraceable.
  * Only *tightening* recommendations from the Risk brain / CRO can be applied
    automatically (risk scale down, suspend a bot, suspend all trading). Any
    loosening needs the owner and is never automatic.
  * Without an LLM key, agents run deterministic rules and say so ("mode":
    "deterministic"); no activity is simulated or invented.
  * LLM calls run in a worker thread; the trading loop never waits for them.
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..backtest import metrics
from ..models import BotStatus, now_ms
from ..storage import Store
from .llm import DEFAULT_MODEL, LLMClient, parse_json
from .prompts import BRAINS, EXECUTIVES, OUTPUT_CONTRACT

AGENTS: dict[str, dict[str, Any]] = {
    "brain_market": {"kind": "brain", "title": "Market Intelligence", "name": "MX Market",
                     "inputs": ["scanner", "regimes", "funding", "open_interest", "btc_correlation"],
                     "outputs": "market intelligence report", "can_apply": []},
    "brain_quant": {"kind": "brain", "title": "Quantitative Research", "name": "MX Quant",
                    "inputs": ["bot_metrics", "walk_forward_jobs", "regime_stats"], "outputs": "validation report",
                    "can_apply": []},
    "brain_strategy": {"kind": "brain", "title": "Strategy Intelligence", "name": "MX Strategy",
                       "inputs": ["leaderboard", "signal_correlation", "recent_verdicts", "best_by_regime"],
                       "outputs": "strategy suitability report", "can_apply": []},
    "brain_capital": {"kind": "brain", "title": "Capital Intelligence", "name": "MX Capital",
                      "inputs": ["equity", "positions", "weights", "risk_fraction"], "outputs": "allocation report",
                      "can_apply": []},
    "brain_execution": {"kind": "brain", "title": "Execution Intelligence", "name": "MX Execution",
                        "inputs": ["orders", "latency", "slippage", "spreads"], "outputs": "execution quality report",
                        "can_apply": []},
    "brain_risk": {"kind": "brain", "title": "Risk & Compliance Intelligence", "name": "MX Risk",
                   "inputs": ["risk_state", "limits", "exposure", "api_permissions", "activity"],
                   "outputs": "risk report", "can_apply": ["risk_scale", "suspend_bot", "suspend_all"]},
    "exec_ceo": {"kind": "executive", "title": "CEO / Head of Trading", "name": "Atlas",
                 "responsibility": "Coordination of all departments, daily objectives, strategic decisions",
                 "can_apply": []},
    "exec_cio": {"kind": "executive", "title": "CIO / Portfolio Manager", "name": "Vega",
                 "responsibility": "Capital allocation, strategy weighting, risk-adjusted growth", "can_apply": []},
    "exec_cro": {"kind": "executive", "title": "CRO / Chief Risk Officer", "name": "Sentinel",
                 "responsibility": "Independent risk oversight, veto, emergency shutdown",
                 "can_apply": ["risk_scale", "suspend_bot", "suspend_all"]},
    "exec_hqr": {"kind": "executive", "title": "Head of Quantitative Research", "name": "Euler",
                 "responsibility": "Validation, backtesting, degradation detection", "can_apply": []},
    "exec_coo": {"kind": "executive", "title": "COO / Operations & Compliance", "name": "Forge",
                 "responsibility": "Exchange connectivity, infrastructure, audit trail, security", "can_apply": []},
}
DEFAULT_PROMPTS = {**{k: v[1] for k, v in BRAINS.items()}, **{k: v[1] for k, v in EXECUTIVES.items()}}


class Organization:
    def __init__(self, store: Store, llm: Optional[LLMClient] = None, brain_interval_min: int = 60,
                 auto_apply_tightening: bool = True):
        self.store = store
        self.llm = llm or LLMClient()
        self.interval_ms = brain_interval_min * 60_000
        self.auto_tighten = auto_apply_tightening
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="org")
        self.last_cycle = 0
        self._running = False
        self.suspended_by_cro: set[str] = set()
        self.ensure_defaults()

    # ------------------------------------------------------------ config
    def ensure_defaults(self) -> None:
        for aid, prompt in DEFAULT_PROMPTS.items():
            if not self.store.one("SELECT 1 FROM prompt_versions WHERE agent_id=?", (aid,)):
                self.store.x("INSERT INTO prompt_versions VALUES(?,?,?,?,?,?,?)",
                             (aid, 1, prompt, "specification", "owner", "initial version", now_ms()))
            if not self.store.one("SELECT 1 FROM agent_config WHERE agent_id=?", (aid,)):
                self.store.x("INSERT INTO agent_config VALUES(?,?,?,?,?,?,?)",
                             (aid, 1, DEFAULT_MODEL, 1, 1500, 0.2, now_ms()))

    def config(self, aid: str) -> dict[str, Any]:
        return self.store.one("SELECT * FROM agent_config WHERE agent_id=?", (aid,)) or {}

    def prompt(self, aid: str) -> dict[str, Any]:
        c = self.config(aid)
        return self.store.one("SELECT * FROM prompt_versions WHERE agent_id=? AND version=?",
                              (aid, c.get("active_version", 1))) or {}

    def versions(self, aid: str) -> list[dict[str, Any]]:
        return self.store.q("SELECT agent_id, version, author, approved_by, note, created_at, prompt "
                            "FROM prompt_versions WHERE agent_id=? ORDER BY version DESC", (aid,))

    def update_prompt(self, aid: str, text: str, actor: str, note: str = "") -> int:
        if aid not in AGENTS:
            raise KeyError(aid)
        if not text.strip():
            raise ValueError("empty prompt")
        v = (self.store.one("SELECT MAX(version) v FROM prompt_versions WHERE agent_id=?", (aid,)) or {}).get("v") or 0
        v += 1
        self.store.x("INSERT INTO prompt_versions VALUES(?,?,?,?,?,?,?)", (aid, v, text, actor, actor, note, now_ms()))
        self.store.x("UPDATE agent_config SET active_version=?, updated_at=? WHERE agent_id=?", (v, now_ms(), aid))
        self.store.audit(actor, "prompt_updated", {"agent": aid, "version": v, "note": note}, "owner")
        return v

    def activate_version(self, aid: str, version: int, actor: str) -> None:
        if not self.store.one("SELECT 1 FROM prompt_versions WHERE agent_id=? AND version=?", (aid, version)):
            raise KeyError("version")
        self.store.x("UPDATE agent_config SET active_version=?, updated_at=? WHERE agent_id=?", (version, now_ms(), aid))
        self.store.audit(actor, "prompt_version_activated", {"agent": aid, "version": version}, "owner")

    def set_config(self, aid: str, actor: str, enabled: Optional[bool] = None, model: Optional[str] = None,
                   max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> dict[str, Any]:
        c = self.config(aid)
        if not c:
            raise KeyError(aid)
        new = {"enabled": int(enabled) if enabled is not None else c["enabled"],
               "model": model or c["model"], "max_tokens": max_tokens or c["max_tokens"],
               "temperature": c["temperature"] if temperature is None else max(0.0, min(1.0, temperature))}
        self.store.x("UPDATE agent_config SET enabled=?, model=?, max_tokens=?, temperature=?, updated_at=? "
                     "WHERE agent_id=?", (new["enabled"], new["model"], new["max_tokens"], new["temperature"],
                                          now_ms(), aid))
        self.store.audit(actor, "agent_config_changed", {"agent": aid, **new}, "owner")
        return new

    # --------------------------------------------------------- evidence
    def packet(self, aid: str, engine, extra_items: Optional[list[tuple[str, Any]]] = None) -> dict[str, Any]:
        items: list[dict[str, Any]] = []

        def add(source: str, data: Any, ts: Optional[int] = None) -> None:
            items.append({"id": f"ev-{len(items) + 1:03d}-{source}", "source": source, "ts": ts or now_ms(),
                          "data": metrics.json_safe(data)})

        for src, data in extra_items or []:
            add(src, data)
        if engine is None:
            add("system", {"note": "trading engine not running"})
            return {"agent": aid, "generated_at": now_ms(), "items": items}
        st = engine.status_summary()
        if aid in ("brain_market", "exec_ceo", "exec_coo"):
            add("binance.scanner", list(engine.scanner.values())[:20])
            for sym in engine.universe[:10]:
                try:
                    pi = engine.ex.premium_index(sym)
                    add(f"binance.premiumIndex.{sym}", {"funding": pi.get("lastFundingRate"),
                                                        "mark": pi.get("markPrice")}, pi.get("time"))
                except Exception as e:
                    add(f"binance.premiumIndex.{sym}", {"error": str(e)})
                try:
                    oi = engine.ex.open_interest(sym)
                    add(f"binance.openInterest.{sym}", oi, oi.get("time"))
                except Exception as e:
                    add(f"binance.openInterest.{sym}", {"error": str(e)})
            if "BTCUSDT" in engine.universe:
                add("computed.btc_correlation", {s: engine._price_corr("BTCUSDT", s) for s in engine.universe})
            add("news", {"available": False, "note": "no news source configured"})
            add("liquidations", {"available": False, "note": "liquidation stream not connected"})
        if aid in ("brain_quant", "exec_hqr", "exec_ceo", "exec_cio"):
            for b in engine.comp.books:
                rows = self.store.q("SELECT * FROM trades WHERE bot_id=? AND mode=? ORDER BY closed_at DESC LIMIT 500",
                                    (b, engine.s.mode))
                from ..models import Trade
                real = [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in rows if not r["virtual"]]
                virt = [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in rows if r["virtual"]]
                add(f"db.trades.{b}", {"status": engine.sup.status.get(b).value,
                                       "real": metrics.compute(real, 100.0), "virtual": metrics.compute(virt, 100.0)})
            jobs = self.store.q("SELECT id, kind, status, result, finished_at FROM jobs WHERE status='done' "
                                "ORDER BY finished_at DESC LIMIT 5")
            for j in jobs:
                add(f"db.jobs.{j['id']}", {"kind": j["kind"], "result": json.loads(j["result"] or "{}")},
                    j["finished_at"])
            add("competition.regime_stats", engine.comp.regimes.table()[:60])
        if aid in ("brain_strategy", "exec_ceo", "exec_cio", "exec_hqr"):
            add("competition.leaderboard", engine.comp.leaderboard())
            add("supervisor.signal_correlation", engine.sup.correlation_matrix())
            add("supervisor.recent_verdicts", [v.to_dict() for v in list(engine.sup.decisions)[-30:]])
            add("competition.best_by_regime", engine.comp.best_by_regime())
        if aid in ("brain_capital", "exec_cio", "exec_cro"):
            add("engine.equity", {k: st[k] for k in ("equity_usdt", "equity_eur", "pnl_usdt", "daily_pnl_pct",
                                                      "drawdown_pct", "start_capital_eur")})
            add("engine.positions", st["open_positions"])
            add("competition.weights", {b: engine.comp.weight(b) for b in engine.comp.books})
            add("risk.fraction", {"current_risk_fraction": engine.risk.risk_fraction(),
                                  "base": engine.risk.limits.risk_per_trade})
        if aid in ("brain_execution", "exec_coo"):
            orders = self.store.q("SELECT client_id, symbol, side, type, qty, price, status, purpose, created_at "
                                  "FROM orders ORDER BY created_at DESC LIMIT 50")
            add("db.orders", orders)
            add("exchange.latency_ms", {"recent": getattr(engine.ex, "latency_ms", [])[-20:],
                                        "used_weight_1m": getattr(engine.ex, "used_weight", None)})
            add("engine.spreads", {s: v.get("spread_bps") for s, v in engine.scanner.items()})
            add("engine.errors", engine.errors[-20:])
        if aid in ("brain_risk", "exec_cro", "exec_coo", "exec_ceo"):
            add("risk.state", {**engine.risk.state.__dict__, "drawdown": engine.risk.drawdown,
                               "daily_pnl_frac": engine.risk.daily_pnl_frac, "tighten_factor": engine.risk.tighten_factor})
            add("risk.limits", engine.risk.limits.__dict__)
            add("engine.status", {k: st[k] for k in ("mode", "execution", "data_fresh", "connection_ok",
                                                      "emergency_stop", "live_unlocked")})
            n10 = self.store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry' AND created_at>?",
                                 (now_ms() - 600_000,))
            add("activity.entries_last_10min", n10)
            perms = self.store.kv_get("api_permissions", {"checked": False})
            add("security.api_permissions", perms)
            add("bots.status", {b: engine.sup.status.get(b).value for b in engine.comp.books})
        if aid.startswith("exec_"):
            for bid in BRAINS:
                r = self.store.one("SELECT id, ts, output, confidence, error FROM agent_runs WHERE agent_id=? "
                                   "ORDER BY ts DESC LIMIT 1", (bid,))
                if r:
                    add(f"agent_runs.{bid}", {"run_id": r["id"], "output": json.loads(r["output"] or "{}"),
                                              "confidence": r["confidence"], "error": r["error"]}, r["ts"])
            day = self.store.q("SELECT bot_id, COUNT(*) n, SUM(net_pnl) pnl FROM trades WHERE virtual=0 AND "
                               "closed_at>? GROUP BY bot_id", (now_ms() - 86_400_000,))
            add("db.trades.last_24h", day)
        return {"agent": aid, "generated_at": now_ms(), "items": items}

    # ------------------------------------------------------------- run
    def run_agent(self, aid: str, engine, kind: str = "cycle", extra_items: Optional[list[tuple[str, Any]]] = None,
                  task: Optional[str] = None, meeting_id: Optional[str] = None) -> dict[str, Any]:
        cfg = self.config(aid)
        pk = self.packet(aid, engine, extra_items)
        ids = {i["id"] for i in pk["items"]}
        rid = uuid.uuid4().hex[:12]
        pr = self.prompt(aid)
        out: Optional[dict[str, Any]] = None
        res = None
        error = None
        if cfg.get("enabled") and self.llm.available:
            system = pr.get("prompt", "") + "\n\n" + OUTPUT_CONTRACT
            user = (f"YOUR TASK NOW: {task}\n\n" if task else "") + "EVIDENCE PACKET:\n" + \
                json.dumps(pk, default=str)[:150_000]
            res = self.llm.complete(system, user,
                                    cfg.get("model", DEFAULT_MODEL), int(cfg.get("max_tokens", 1500)),
                                    float(cfg.get("temperature", 0.2)))
            error = res.error
            if not error:
                out = parse_json(res.text)
                if out is None:
                    error = "output was not valid JSON"
            if out is not None:
                out["mode"] = "llm"
                untraceable = []
                for sec in ("findings", "recommendations"):
                    for it in out.get(sec, []) or []:
                        cited = set(it.get("evidence_ids") or [])
                        if not cited or not cited <= ids:
                            it["traceable"] = False
                            untraceable.append(it.get("text") or it.get("action"))
                        else:
                            it["traceable"] = True
                out["untraceable_items"] = untraceable
        if out is None:
            out = self._deterministic(aid, pk, engine)
            if not cfg.get("enabled"):
                out["note"] = "agent disabled: deterministic rules only"
            elif not self.llm.available:
                out["note"] = "LLM offline: deterministic rules only"
            elif error:
                out["note"] = f"LLM error ({error}); deterministic rules used"
        conf = out.get("confidence")
        self.store.x("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, now_ms(), aid, kind, res.model if res else "deterministic", pr.get("version"),
                      json.dumps(pk, default=str)[:200_000], json.dumps(out, default=str), conf,
                      res.input_tokens if res else 0, res.output_tokens if res else 0,
                      res.cost_usd if res else 0.0, res.latency_ms if res else 0, error,
                      json.dumps(sorted(ids))))
        out["run_id"] = rid
        out["agent"] = aid
        out["kind"] = kind
        if meeting_id:
            self.message(meeting_id, aid, "board", kind, rid, out.get("summary", ""))
        if engine is not None and not kind.startswith("board:"):
            self._apply(aid, out, engine, rid)
        return out

    def message(self, meeting_id: Optional[str], sender: str, recipient: str, kind: str, run_id: Optional[str],
                content: str) -> None:
        self.store.x("INSERT INTO agent_messages(ts,meeting_id,sender,recipient,kind,run_id,content) "
                     "VALUES(?,?,?,?,?,?,?)", (now_ms(), meeting_id, sender, recipient, kind, run_id, content[:2000]))

    def _deterministic(self, aid: str, pk: dict[str, Any], engine) -> dict[str, Any]:
        items = {i["source"]: i for i in pk["items"]}
        f, recs = [], []
        stance = {"risk": "hold", "activity": "hold"}
        if engine is not None and aid in ("brain_risk", "exec_cro"):
            rs = items.get("risk.state", {}).get("data", {})
            lim = items.get("risk.limits", {}).get("data", {})
            ev = [items["risk.state"]["id"], items["risk.limits"]["id"]] if "risk.state" in items else []
            dd, mdd = rs.get("drawdown", 0) or 0, lim.get("max_total_drawdown", 0.2)
            f.append({"text": f"drawdown {dd * 100:.2f}% of limit {mdd * 100:.0f}%", "evidence_ids": ev})
            if dd > 0.5 * mdd:
                recs.append({"action": "risk_scale", "target": "0.5", "direction": "tighten",
                             "rationale": "drawdown above half of the hard limit", "evidence_ids": ev})
                stance["risk"] = "tighten"
            perms = items.get("security.api_permissions", {}).get("data", {})
            if perms.get("withdrawals"):
                recs.append({"action": "suspend_all", "target": "all", "direction": "tighten",
                             "rationale": "API key has withdrawal permission enabled",
                             "evidence_ids": [items["security.api_permissions"]["id"]]})
                stance["risk"] = "tighten"
            act = (items.get("activity.entries_last_10min", {}).get("data") or {}).get("n", 0)
            if act and act > 10:
                recs.append({"action": "risk_scale", "target": "0.5", "direction": "tighten",
                             "rationale": f"abnormal activity: {act} entries in 10 minutes",
                             "evidence_ids": [items["activity.entries_last_10min"]["id"]]})
        elif aid in ("brain_quant", "exec_hqr"):
            for src, it in items.items():
                if src.startswith("db.trades.") and isinstance(it["data"], dict) and "virtual" in it["data"]:
                    v = it["data"]["virtual"]
                    if v["trades"]:
                        f.append({"text": f"{src[10:]}: {v['trades']} shadow trades, PF {v['profit_factor']}, "
                                          f"expR {v['expectancy_r']}", "evidence_ids": [it["id"]]})
        elif aid in ("brain_market",):
            for src, it in items.items():
                if src == "binance.scanner":
                    for row in it["data"][:10]:
                        if row.get("regime"):
                            f.append({"text": f"{row['symbol']}: {row['regime']} / {row.get('volatility')}",
                                      "evidence_ids": [it["id"]]})
        if aid.startswith("exec_") and not f:
            for src, it in items.items():
                if src.startswith("agent_runs.") or src.startswith("board."):
                    d = it["data"] if isinstance(it["data"], dict) else {}
                    o = d.get("output", d)
                    if isinstance(o, dict) and o.get("summary"):
                        f.append({"text": f"{src}: {o.get('summary')}", "evidence_ids": [it["id"]]})
        return {"mode": "deterministic", "summary": f"{len(f)} rule-based findings", "findings": f,
                "recommendations": recs, "stance": stance, "confidence": None,
                "limitations": ["no language-model analysis in this run"]}

    def _apply(self, aid: str, out: dict[str, Any], engine, rid: str) -> None:
        allowed = AGENTS[aid].get("can_apply", [])
        for r in out.get("recommendations", []) or []:
            if r.get("direction") != "tighten" or r.get("action") not in allowed:
                continue
            if out.get("mode") == "llm" and not r.get("traceable"):
                continue
            if not self.auto_tighten:
                continue
            self._apply_one(aid, r, engine, rid)

    def _apply_one(self, aid: str, r: dict[str, Any], engine, rid: str):
        """Apply ONE tightening recommendation. Returns what was applied or None."""
        if r.get("direction") != "tighten" or r.get("action") not in AGENTS[aid].get("can_apply", []):
            return None
        if engine is not None:
            act, tgt = r["action"], str(r.get("target", ""))
            applied = None
            if act == "risk_scale":
                try:
                    f = float(tgt)
                except ValueError:
                    return None
                if f < engine.risk.tighten_factor:
                    applied = engine.risk.tighten(f)
            elif act == "suspend_bot" and tgt in engine.comp.books:
                if engine.sup.status.get(tgt) not in (BotStatus.SUSPENDED, BotStatus.DISABLED):
                    engine.sup.set_status(tgt, BotStatus.SUSPENDED, f"suspended by {aid}: {r.get('rationale')}")
                    engine.save_bot_state(tgt)
                    applied = tgt
            elif act == "suspend_all":
                applied = engine.risk.tighten(0.0)
            if applied is not None:
                self.store.audit(aid, f"auto_tighten:{act}", {"run_id": rid, "recommendation": r}, "auto (tighten-only)",
                                 {"applied": applied})
                engine.alert("warning", "risk", f"{AGENTS[aid]['title']}: {act}", r.get("rationale", ""))
            return applied
        return None

    # ---------------------------------------------------------- CRO gate
    def cro_review(self, engine, signal, dec) -> tuple[bool, float, str]:
        """Deterministic CRO checks on every trade (in addition to the
        RiskManager's hard limits). Can only veto or reduce."""
        eq = max(1e-9, engine.risk.state.equity)
        used = sum(p.qty * p.entry_price / max(1, p.leverage) for p in engine.positions.values())
        new_margin = dec.notional / max(1, dec.leverage)
        if (used + new_margin) / eq > 0.8:
            return False, 0.0, "margin utilisation would exceed 80%"
        n = (self.store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry' AND created_at>?",
                            (now_ms() - 600_000,)) or {}).get("n", 0)
        if n >= 10:
            return False, 0.0, "abnormal activity: 10+ entries in 10 minutes"
        perms = self.store.kv_get("api_permissions", {})
        if perms.get("withdrawals"):
            return False, 0.0, "API key has withdrawal permission"
        return True, 1.0, "within CRO policy"

    # ------------------------------------------------------- scheduling
    def on_tick(self, engine) -> None:
        if self._running or now_ms() - self.last_cycle < self.interval_ms:
            return
        self.last_cycle = now_ms()
        self._running = True
        self.pool.submit(self._cycle, engine)

    def _cycle(self, engine) -> None:
        try:
            for aid in BRAINS:
                try:
                    self.run_agent(aid, engine)
                except Exception as e:
                    self.store.audit(aid, "agent_error", {"error": str(e)})
        finally:
            self._running = False

    def on_new_day(self, engine) -> None:
        self.pool.submit(self.board_meeting, engine)

    AGENDA = [("exec_ceo", "opening_report", "CEO opening report: state of the organisation and today's objectives"),
              ("exec_cio", "capital_report", "CIO capital report: allocation, exposure, risk-adjusted results"),
              ("exec_hqr", "quant_report", "Quantitative research report: validation status, degradation, evidence"),
              ("exec_cro", "risk_assessment", "CRO risk assessment: limits, drawdown, exposures, incidents"),
              ("exec_coo", "operational_assessment", "COO operational assessment: connectivity, data, execution, audit")]
    # actions the CRO may approve for automatic application (tightening only)
    TIGHTEN_ACTIONS = {"risk_scale", "suspend_bot", "suspend_all"}

    def board_meeting(self, engine) -> dict[str, Any]:
        """Eight-item agenda. Every item is a real agent run (LLM or explicitly
        deterministic); nothing is scripted or invented."""
        mid = uuid.uuid4().hex[:10]
        llm_on = self.llm.available
        items: list[dict[str, Any]] = []
        reports: dict[str, dict[str, Any]] = {}
        shared: list[tuple[str, Any]] = []
        # 1-5: department reports; later speakers receive earlier reports (inter-agent communication)
        for n, (aid, kind, task) in enumerate(self.AGENDA, start=1):
            try:
                out = self.run_agent(aid, engine, f"board:{kind}", extra_items=list(shared), task=task, meeting_id=mid)
            except Exception as e:
                out = {"mode": "error", "summary": f"agent failed: {e}", "stance": {}}
            reports[aid] = out
            shared.append((f"board.{kind}.{aid}", {"summary": out.get("summary"), "stance": out.get("stance"),
                                                   "recommendations": out.get("recommendations"),
                                                   "mode": out.get("mode"), "run_id": out.get("run_id")}))
            items.append({"item": n, "title": task.split(":")[0], "agent": aid, "mode": out.get("mode"),
                          "run_id": out.get("run_id"), "summary": out.get("summary"), "stance": out.get("stance"),
                          "findings": out.get("findings", []), "recommendations": out.get("recommendations", []),
                          "note": out.get("note")})
        # 6: disagreements and discussion
        disagreements = []
        for key in ("risk", "activity"):
            vals = {aid: (o.get("stance") or {}).get(key) for aid, o in reports.items() if (o.get("stance") or {}).get(key)}
            if len(set(vals.values())) > 1:
                disagreements.append({"topic": key, "positions": vals})
        discussion = []
        if disagreements and llm_on:
            for d in disagreements:
                for aid in d["positions"]:
                    others = {k: v for k, v in d["positions"].items() if k != aid}
                    task = (f"Discussion: you said {d['topic']}={d['positions'][aid]}; colleagues said {others}. "
                            "Respond to their arguments (cite evidence) and give your final stance.")
                    out = self.run_agent(aid, engine, "board:discussion", extra_items=list(shared), task=task,
                                         meeting_id=mid)
                    discussion.append({"agent": aid, "topic": d["topic"], "mode": out.get("mode"),
                                       "run_id": out.get("run_id"), "summary": out.get("summary"),
                                       "final_stance": (out.get("stance") or {}).get(d["topic"])})
                    shared.append((f"board.discussion.{aid}.{d['topic']}", discussion[-1]))
        items.append({"item": 6, "title": "Discussion of disagreements", "disagreements": disagreements,
                      "discussion": discussion,
                      "note": None if (discussion or not disagreements) else
                      "LLM offline: positions recorded, no discussion round was run"})
        # 7: CEO final decision
        ceo = self.run_agent("exec_ceo", engine, "board:final_decision", extra_items=list(shared),
                             task="Final CEO decision: choose the actions for today from the reports and discussion. "
                                  "You cannot loosen CRO limits.", meeting_id=mid)
        decisions = [dict(r, index=i) for i, r in enumerate(ceo.get("recommendations", []) or [])]
        items.append({"item": 7, "title": "Final CEO decision", "agent": "exec_ceo", "mode": ceo.get("mode"),
                      "run_id": ceo.get("run_id"), "summary": ceo.get("summary"), "decisions": decisions})
        # 8: independent CRO approval / veto (hard rules first, then the CRO agent)
        cro_out = self.run_agent("exec_cro", engine, "board:cro_review", extra_items=list(shared) + [
            ("board.ceo_decisions", decisions)],
            task="CRO review of the CEO decisions: for each decision index state approve or veto with evidence. "
                 "Express a veto as a recommendation {action:'veto', target:'<index>'}.", meeting_id=mid)
        llm_vetoes = {str(r.get("target")) for r in cro_out.get("recommendations", []) or []
                      if str(r.get("action", "")).lower() == "veto"}
        verdicts = []
        for d in decisions:
            reason, status = "", "approved_by_cro"
            if d.get("direction") == "loosen":
                status, reason = "vetoed_by_cro", "hard rule: loosening risk requires the owner"
            elif d.get("mode") == "llm" and d.get("traceable") is False:
                status, reason = "vetoed_by_cro", "not traceable to evidence"
            elif d.get("traceable") is False:
                status, reason = "vetoed_by_cro", "not traceable to evidence"
            elif str(d["index"]) in llm_vetoes:
                status, reason = "vetoed_by_cro", "CRO agent veto"
            applied = None
            if status == "approved_by_cro" and d.get("direction") == "tighten" and \
                    d.get("action") in self.TIGHTEN_ACTIONS and self.auto_tighten and engine is not None:
                applied = self._apply_one("exec_cro", d, engine, cro_out.get("run_id", ""))
            final = status if status.startswith("vetoed") else ("applied (tighten-only)" if applied is not None
                                                                else "awaiting owner decision")
            verdicts.append({**d, "cro": status, "cro_reason": reason, "status": final})
        items.append({"item": 8, "title": "Independent CRO approval or veto", "agent": "exec_cro",
                      "mode": cro_out.get("mode"), "run_id": cro_out.get("run_id"), "summary": cro_out.get("summary"),
                      "verdicts": verdicts})
        data = {"id": mid, "ts": now_ms(), "llm": llm_on, "agenda": items, "disagreements": disagreements,
                "decisions": verdicts, "participants": [a for a, *_ in self.AGENDA],
                # backwards-compatible fields used by older app builds
                "outputs": reports, "recommendations": [dict(v, **{"from": "exec_ceo"}) for v in verdicts]}
        self.store.x("INSERT INTO meetings VALUES(?,?,?)", (mid, now_ms(), json.dumps(data, default=str)))
        self.store.audit("organization", "board_meeting", {"meeting_id": mid, "llm": llm_on,
                         "disagreements": disagreements, "decisions": len(verdicts)})
        if engine is not None:
            engine.alert("info", "boardroom", "Board meeting finished",
                         f"{len(verdicts)} decisions, {sum(v['cro'].startswith('vetoed') for v in verdicts)} vetoed, "
                         f"{len(disagreements)} disagreements" + ("" if llm_on else " (deterministic, LLM offline)"))
        return data

    # ------------------------------------------------------------ status
    def overview(self) -> list[dict[str, Any]]:
        out = []
        for aid, meta in AGENTS.items():
            c = self.config(aid)
            last = self.store.one("SELECT id, ts, output, confidence, error, model FROM agent_runs WHERE agent_id=? "
                                  "ORDER BY ts DESC LIMIT 1", (aid,))
            tot = self.store.one("SELECT COUNT(*) runs, SUM(input_tokens) tin, SUM(output_tokens) tout, "
                                 "SUM(cost_usd) cost, SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) errors "
                                 "FROM agent_runs WHERE agent_id=?", (aid,))
            o = json.loads(last["output"]) if last else {}
            online = bool(c.get("enabled")) and self.llm.available
            out.append({"id": aid, **{k: v for k, v in meta.items() if k != "can_apply"},
                        "auto_apply": meta.get("can_apply", []),
                        "enabled": bool(c.get("enabled")), "model": c.get("model"),
                        "prompt_version": c.get("active_version"),
                        "status": "online" if online else ("deterministic" if c.get("enabled") else "offline"),
                        "last_run": last["ts"] if last else None, "last_summary": o.get("summary"),
                        "last_mode": o.get("mode"), "confidence": last["confidence"] if last else None,
                        "last_error": last["error"] if last else None, "totals": tot})
        return out
