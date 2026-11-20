"""AI trading desk: Independent Trader + AI Supervisor (Phases 3-4).

Independent Trader
  * Gets ONLY the market packet (ai/market.py) — never the six agents' outputs — and
    must finish its own analysis first. Returns LONG / SHORT / NO_TRADE per instrument
    with entry, stop, take profit, R:R, confidence, reasoning, invalidation conditions,
    validity time and the market_data_id it used.
  * Every decision is validated deterministically and stored in `ai_decisions`
    (separate from the bots) for objective comparison. Valid LONG/SHORT decisions
    are paper-traded as the `ai_trader` shadow book and, if allowed, submitted to
    the normal pipeline: SUPERVISOR -> RISK ENGINE -> EXECUTION ENGINE.

AI Supervisor
  * Reviews every candidate that the deterministic risk engine already sized
    (bots, AI trader, TradingView), comparing it with the six agents' latest
    reports and the Independent Trader's own view. Deterministic red flags reject
    without asking a model. Decisions: APPROVE / REJECT / WAIT / NO_TRADE /
    EMERGENCY_STOP, plus the maximum leverage it approves. Majority agreement alone
    is never an approval (prompt + code: APPROVE needs its own reasons).
  * APPROVE does not place an order: the engine re-sizes at the current price
    through the deterministic risk engine and pre-trade governance, which can still
    refuse. EMERGENCY_STOP blocks new entries (positions keep their exchange stops).
  * If every AI provider is down: safe NO_TRADE.

No AI component has access to API keys or the exchange client.
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..models import Side, Signal, now_ms
from ..storage import Store
from .market import build_packet
from .providers import ProviderChain, parse_json_obj

TRADER_PROMPT = """You are MILLION-X Independent Trader, a disciplined crypto-futures scalper.
You receive ONLY a market-data packet for the most liquid Binance USDT-M perpetuals (1m/5m/15m EMA 9/21/50,
RSI, MACD, ATR, VWAP, 24h volume profile, order-book imbalance and spread, funding rate, open interest,
swing support/resistance, breakout and false-breakout flags). Analyse market structure yourself.
Most of the time the correct answer is NO_TRADE. Only propose a trade when structure, momentum and
liquidity agree and the stop sits beyond a real level. Never invent data that is not in the packet.
Costs: ~0.14 % round trip, so stops tighter than 0.4 % are rarely worth it; reward/risk must be >= 1.5.
Return STRICT JSON only:
{"market_data_id": "<id from the packet>",
 "decisions": [{"instrument": "BTCUSDT", "direction": "LONG|SHORT|NO_TRADE", "entry": 0, "stop_loss": 0,
   "take_profit": 0, "risk_reward": 0, "confidence": 0.0, "reasoning": "...",
   "invalidation": "...", "valid_minutes": 15}]}
At most 3 decisions; include NO_TRADE entries for instruments you looked at and rejected."""

SUPERVISOR_PROMPT = """You are MILLION-X AI Supervisor. You decide whether ONE candidate trade may proceed to the
deterministic risk engine. You see: the candidate (already sized by the risk engine), the latest reports of the six
specialist agents, the Independent Trader's own view of the same instrument, and automatic checks.
Rules: agreement of many agents is NOT a reason to approve; approve only with your own concrete reasons.
Reject stale or illogical signals (stop on the wrong side, unrealistic target, entry far from price), excessive or
correlated exposure, repeated losing patterns of the same bot, bad market data, possible duplicate orders.
Use WAIT when the setup may become valid within minutes; NO_TRADE when the market is unsuitable;
EMERGENCY_STOP only for clear system danger (broken data, runaway losses, duplicate executions).
Approve leverage by setup quality, volatility, liquidity, spread and stop distance: 5, 10, 15 or 20; 20 only for
an exceptional setup with calm volatility, tight spread and a stop far from liquidation. Confidence alone is never
enough for 20. Return STRICT JSON only:
{"decision": "APPROVE|REJECT|WAIT|NO_TRADE|EMERGENCY_STOP", "max_leverage": 5,
 "reasons": ["..."], "disagreements": ["..."], "checked": ["..."]}"""

DECISIONS = ("APPROVE", "REJECT", "WAIT", "NO_TRADE", "EMERGENCY_STOP")
BRAINS = ("brain_market", "brain_strategy", "brain_quant", "brain_execution", "brain_risk", "brain_capital")


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def validate_trader_reply(text: str, packet_id: str) -> dict[str, Any]:
    d = parse_json_obj(text)
    if not isinstance(d.get("decisions"), list):
        raise ValueError("missing decisions list")
    if d.get("market_data_id") != packet_id:
        raise ValueError("market_data_id does not match the packet")
    return d


def check_trade_decision(x: dict[str, Any], price: Optional[float]) -> tuple[bool, str, dict[str, Any]]:
    """Deterministic validation of one trader decision. Returns (ok, reason, normalised)."""
    direction = str(x.get("direction", "")).upper().replace(" ", "_")
    sym = str(x.get("instrument", "")).upper()
    norm = {"instrument": sym, "direction": direction, "confidence": _num(x.get("confidence")) or 0.0,
            "reasoning": str(x.get("reasoning", ""))[:1200], "invalidation": str(x.get("invalidation", ""))[:500],
            "valid_minutes": int(min(60, max(1, _num(x.get("valid_minutes")) or 15)))}
    if direction == "NO_TRADE":
        return True, "", norm
    if direction not in ("LONG", "SHORT"):
        return False, f"unknown direction {direction!r}", norm
    e, sl, tp = _num(x.get("entry")), _num(x.get("stop_loss")), _num(x.get("take_profit"))
    if not e or not sl or not tp:
        return False, "entry/stop_loss/take_profit missing", norm
    norm.update(entry=e, stop_loss=sl, take_profit=tp)
    if direction == "LONG" and not (sl < e < tp):
        return False, "LONG needs stop < entry < target", norm
    if direction == "SHORT" and not (tp < e < sl):
        return False, "SHORT needs target < entry < stop", norm
    rr = abs(tp - e) / abs(e - sl)
    norm["risk_reward"] = round(rr, 3)
    if rr < 1.2:
        return False, f"reward/risk {rr:.2f} < 1.2", norm
    if not 0 <= norm["confidence"] <= 1:
        return False, "confidence outside 0..1", norm
    if price and abs(e / price - 1) > 0.005:
        return False, f"entry {e} is {abs(e / price - 1) * 100:.2f}% away from price {price}", norm
    return True, "", norm


class AIDesk:
    def __init__(self, store: Store, chain: ProviderChain):
        self.store = store
        self.chain = chain
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai")
        self.last_trader_run = 0
        self._trader_busy = False
        self._reviewing: set[str] = set()

    # ---- settings ------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        d = {"trader_enabled": True, "supervisor_enabled": True, "trader_interval_min": 5,
             "trader_min_confidence": 0.6, "trader_max_trades_per_run": 2}
        d.update(self.store.kv_get("ai_desk", {}) or {})
        return d

    def update_settings(self, **kw: Any) -> dict[str, Any]:
        d = self.settings()
        for k, v in kw.items():
            if k in d and v is not None:
                d[k] = type(d[k])(v)
        d["trader_interval_min"] = max(1, min(60, int(d["trader_interval_min"])))
        self.store.kv_set("ai_desk", d)
        return d

    def trader_active(self) -> bool:
        return self.settings()["trader_enabled"] and self.chain.any_configured and not self.chain.paused

    def supervisor_active(self) -> bool:
        """AI review gate is on when enabled and at least one provider is configured.
        With no provider at all the deterministic pipeline runs unchanged."""
        return self.settings()["supervisor_enabled"] and self.chain.any_configured

    # ---- storage ---------------------------------------------------------------
    def record(self, agent: str, symbol: str, decision: str, payload: dict[str, Any], status: str,
               market_data_id: str = "", provider: str = "", model: str = "", signal_id: str = "") -> str:
        did = uuid.uuid4().hex[:12]
        self.store.x("INSERT INTO ai_decisions(id,ts,agent,symbol,decision,payload,market_data_id,provider,model,"
                     "status,signal_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (did, now_ms(), agent, symbol, decision, json.dumps(payload, default=str)[:20000],
                      market_data_id, provider or "", model or "", status, signal_id))
        return did

    # ---- scheduling (called from the engine loop; never blocks it) -------------
    def on_tick(self, engine) -> None:
        st = self.settings()
        if not self.trader_active() or self._trader_busy:
            return
        if now_ms() - self.last_trader_run < st["trader_interval_min"] * 60_000:
            return
        if not engine.risk.state.data_fresh or not engine.universe:
            return
        self.last_trader_run = now_ms()
        self._trader_busy = True
        self.pool.submit(self._safe_trader, engine)

    def _safe_trader(self, engine) -> None:
        try:
            self.run_trader(engine)
        except Exception as e:  # never let the AI thread die silently
            self.store.audit("ai_trader", "ai_trader_error", {"error": str(e)[:300]})
        finally:
            self._trader_busy = False

    # ---- Independent Trader ---------------------------------------------------
    def run_trader(self, engine) -> dict[str, Any]:
        packet = build_packet(engine)
        pid = packet["market_data_id"]
        if not packet["symbols"]:
            return {"ok": False, "error": "no market data"}
        user = "MARKET DATA PACKET:\n" + json.dumps(packet, default=str)[:24000]
        res = self.chain.complete(TRADER_PROMPT, user, validate=lambda t: validate_trader_reply(t, pid),
                                  max_tokens=1600, temperature=0.2, purpose="independent_trader")
        if not res.ok:
            self.record("independent_trader", "*", "NO_TRADE", {"reason": res.error, "attempts": res.attempts},
                        "ai_unavailable", pid)
            return {"ok": False, "error": res.error}
        st = self.settings()
        submitted = 0
        out = []
        for x in res.data["decisions"][:3]:
            sym = str(x.get("instrument", "")).upper()
            price = float(engine.bars[sym].close[-1]) if sym in engine.bars else None
            ok, why, norm = check_trade_decision(x, price)
            if sym not in engine.universe:
                ok, why = False, f"{sym} is not in the traded universe"
            status = "no_trade" if ok and norm["direction"] == "NO_TRADE" else ("valid" if ok else "invalid")
            # the trader's decision is stored BEFORE anything else sees it (objective comparison)
            did = self.record("independent_trader", sym, norm["direction"], {**norm, "check": why}, status, pid,
                              res.provider or "", res.model or "")
            if status == "valid" and norm["confidence"] >= st["trader_min_confidence"] \
                    and submitted < st["trader_max_trades_per_run"]:
                sig = self._signal_from(norm)
                r = engine.submit_external(sig, origin="ai_trader")
                status = "submitted" if r.get("ok") else f"not_submitted: {r.get('error')}"
                submitted += 1
                self.store.x("UPDATE ai_decisions SET status=?, signal_id=? WHERE id=?", (status, sig.id, did))
            elif status == "valid":
                status = "valid_below_confidence_or_quota"
                self.store.x("UPDATE ai_decisions SET status=? WHERE id=?", (status, did))
            out.append({"symbol": sym, "direction": norm["direction"], "status": status, "why": why})
        return {"ok": True, "provider": res.provider, "model": res.model, "decisions": out}

    @staticmethod
    def _signal_from(n: dict[str, Any]) -> Signal:
        side = Side.LONG if n["direction"] == "LONG" else Side.SHORT
        e, sl, tp = n["entry"], n["stop_loss"], n["take_profit"]
        tp1 = e + (tp - e) * 0.5
        return Signal(bot_id="ai_trader", symbol=n["instrument"], side=side, entry=e, stop=sl, tp1=tp1, tp2=tp,
                      confidence=float(n["confidence"]), bar_time=now_ms(), timeframe=5,
                      reason=("AI trader: " + n["reasoning"])[:300],
                      features={"valid_until": now_ms() + n["valid_minutes"] * 60_000})

    # ---- AI Supervisor ------------------------------------------------------------
    def red_flags(self, engine, sig: Signal, dec) -> list[str]:
        flags = []
        if not engine.risk.state.data_fresh:
            flags.append("market data stale")
        if sig.symbol in engine.positions:
            flags.append("position already open on this symbol (possible duplicate)")
        dup = [p for sid, p in engine.pending.items() if sid != sig.id and p.signal.symbol == sig.symbol]
        if dup:
            flags.append("another pending order on the same symbol (possible duplicate)")
        if sig.side == Side.LONG and not (sig.stop < sig.entry < sig.tp1 <= sig.tp2):
            flags.append("illogical stop/target for LONG")
        if sig.side == Side.SHORT and not (sig.stop > sig.entry > sig.tp1 >= sig.tp2):
            flags.append("illogical stop/target for SHORT")
        vu = (sig.features or {}).get("valid_until")
        if vu and now_ms() > vu:
            flags.append("signal expired")
        recent = self.store.q("SELECT net_pnl FROM trades WHERE bot_id=? ORDER BY closed_at DESC LIMIT 6", (sig.bot_id,))
        if len(recent) >= 5 and all((r["net_pnl"] or 0) < 0 for r in recent[:5]):
            flags.append(f"{sig.bot_id} lost its last 5 trades (repeating losing pattern)")
        same_dir = [p for p in engine.positions.values() if int(p.side) == int(sig.side)
                    and abs(engine._price_corr(p.symbol, sig.symbol)) > 0.8]
        if same_dir:
            flags.append("highly correlated position already open in the same direction")
        return flags

    def queue_review(self, engine, signal_id: str) -> None:
        if signal_id in self._reviewing:
            return
        self._reviewing.add(signal_id)
        self.pool.submit(self._safe_review, engine, signal_id)

    def _safe_review(self, engine, signal_id: str) -> None:
        try:
            self.review(engine, signal_id)
        except Exception as e:
            self.store.audit("ai_supervisor", "ai_supervisor_error", {"signal_id": signal_id, "error": str(e)[:300]})
            engine.approve(signal_id, False, by="ai_supervisor:error")
        finally:
            self._reviewing.discard(signal_id)

    def context_for(self, engine, sig: Signal) -> dict[str, Any]:
        reports = {}
        for b in BRAINS:
            r = self.store.one("SELECT ts, output, model FROM agent_runs WHERE agent_id=? ORDER BY ts DESC LIMIT 1", (b,))
            if r:
                try:
                    o = json.loads(r["output"] or "{}")
                except ValueError:
                    o = {}
                reports[b] = {"age_min": round((now_ms() - r["ts"]) / 60_000, 1), "summary": o.get("summary"),
                              "stance": o.get("stance"), "findings": [f.get("text") for f in (o.get("findings") or [])[:5]
                                                                      if isinstance(f, dict)]}
        it = self.store.one("SELECT ts, decision, payload FROM ai_decisions WHERE agent='independent_trader' AND symbol=? "
                            "ORDER BY ts DESC LIMIT 1", (sig.symbol,))
        trader = None
        if it:
            p = json.loads(it["payload"] or "{}")
            trader = {"age_min": round((now_ms() - it["ts"]) / 60_000, 1), "decision": it["decision"],
                      "confidence": p.get("confidence"), "reasoning": p.get("reasoning")}
        px = float(engine.bars[sig.symbol].close[-1]) if sig.symbol in engine.bars else None
        return {"six_agents": reports, "independent_trader": trader, "price_now": px,
                "open_positions": [{"symbol": p.symbol, "side": "LONG" if int(p.side) > 0 else "SHORT",
                                    "bot": p.bot_id} for p in engine.positions.values()],
                "risk_state": {"drawdown": engine.risk.drawdown, "daily_pnl": engine.risk.daily_pnl_frac,
                               "consecutive_losses": engine.risk.state.consecutive_losses},
                "regime": engine.scanner.get(sig.symbol, {}).get("regime"),
                "spread_bps": engine.books[sig.symbol].spread_bps if sig.symbol in engine.books else None}

    def review(self, engine, signal_id: str) -> dict[str, Any]:
        p = engine.pending.get(signal_id)
        if not p:
            return {"ok": False, "error": "not pending"}
        sig, dec = p.signal, p.decision
        flags = self.red_flags(engine, sig, dec)
        if flags:
            out = {"decision": "REJECT", "max_leverage": 0, "reasons": flags, "source": "rules"}
            self.record("supervisor", sig.symbol, "REJECT", {**out, "bot": sig.bot_id}, "rules", signal_id=signal_id)
            engine.approve(signal_id, False, by="ai_supervisor:rules")
            return out
        candidate = {"bot": sig.bot_id, "symbol": sig.symbol, "side": "LONG" if sig.side == Side.LONG else "SHORT",
                     "entry": sig.entry, "stop": sig.stop, "tp1": sig.tp1, "tp2": sig.tp2,
                     "confidence": sig.confidence, "reason": sig.reason, "age_s": (now_ms() - sig.bar_time) / 1000,
                     "sizing": {"qty": dec.qty, "notional": dec.notional, "risk_usdt": dec.risk_amount,
                                "leverage_proposed": dec.leverage, "net_rr": dec.net_reward_risk,
                                "adjustments": dec.adjustments}}
        user = "CANDIDATE:\n" + json.dumps(candidate, default=str) + "\nCONTEXT:\n" + \
            json.dumps(self.context_for(engine, sig), default=str)[:14000]

        def validate(t: str) -> dict[str, Any]:
            d = parse_json_obj(t)
            if str(d.get("decision", "")).upper() not in DECISIONS:
                raise ValueError("decision not one of " + "/".join(DECISIONS))
            if str(d.get("decision", "")).upper() == "APPROVE" and not d.get("reasons"):
                raise ValueError("APPROVE without reasons")
            return d
        res = self.chain.complete(SUPERVISOR_PROMPT, user, validate=validate, max_tokens=700, temperature=0.1,
                                  purpose="supervisor")
        if not res.ok:
            out = {"decision": "NO_TRADE", "reasons": [f"safe mode: {res.error}"], "source": "safe_mode"}
            self.record("supervisor", sig.symbol, "NO_TRADE", {**out, "bot": sig.bot_id}, "ai_unavailable",
                        signal_id=signal_id)
            engine.approve(signal_id, False, by="ai_supervisor:safe_mode")
            return out
        d = res.data
        decision = str(d["decision"]).upper()
        lev = int(_num(d.get("max_leverage")) or 5)
        lev = max(1, min(20, lev))
        out = {"decision": decision, "max_leverage": lev, "reasons": d.get("reasons", [])[:8],
               "disagreements": d.get("disagreements", [])[:8], "source": "ai"}
        self.record("supervisor", sig.symbol, decision, {**out, "bot": sig.bot_id, "candidate": candidate},
                    "decided", provider=res.provider or "", model=res.model or "", signal_id=signal_id)
        if decision == "APPROVE":
            p.record["supervisor_max_leverage"] = lev
            if engine.s.execution == "manual":
                engine.mark_supervisor_approved(signal_id, out)
            else:
                engine.approve(signal_id, True, by="ai_supervisor")
        elif decision == "WAIT" and not p.record.get("waited"):
            p.record["waited"] = True
            time.sleep(0)  # re-reviewed once by the engine loop on the next bar
            engine.requeue_review(signal_id)
        elif decision == "EMERGENCY_STOP":
            engine.emergency_stop("ai_supervisor", flatten=False)
            engine.approve(signal_id, False, by="ai_supervisor:emergency")
        else:
            engine.approve(signal_id, False, by="ai_supervisor")
        return out

    # ---- reporting ----------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        day0 = now_ms() - 86_400_000
        rows = self.store.q("SELECT agent, decision, status, COUNT(*) n FROM ai_decisions WHERE ts>? "
                            "GROUP BY agent, decision, status", (day0,))
        reasons = {}
        for r in self.store.q("SELECT payload FROM ai_decisions WHERE agent='supervisor' AND ts>? AND "
                              "decision!='APPROVE' ORDER BY ts DESC LIMIT 200", (day0,)):
            for x in (json.loads(r["payload"] or "{}").get("reasons") or [])[:1]:
                k = str(x)[:90]
                reasons[k] = reasons.get(k, 0) + 1
        return {"settings": self.settings(), "trader_active": self.trader_active(),
                "supervisor_active": self.supervisor_active(), "last_trader_run": self.last_trader_run or None,
                "counts_24h": rows, "top_rejection_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:8]}
