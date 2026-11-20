"""REST + WebSocket API for the Android command center (Starlette)."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .backtest import metrics
from .models import BotStatus, Trade, now_ms
from .org.organization import AGENTS
from .risk import SymbolRules, feasibility_report
from .runtime import Runtime
from .security import authenticate, can, create_pairing_code, redeem_pairing_code, store_api_keys
from .strategies import BASE_CLASSES, build_bots


class RateLimiter:
    """Sliding-window limiter (pure ASGI, also covers WebSocket handshakes)."""

    def __init__(self, app, per_minute: int = 240, pair_per_minute: int = 10):
        self.app = app
        self.per_minute = per_minute
        self.pair_per_minute = pair_per_minute
        self.hits: dict[str, list[float]] = {}

    def _allow(self, key: str, limit: int) -> bool:
        import time as _t
        now = _t.time()
        q = [x for x in self.hits.get(key, []) if now - x < 60]
        if len(q) >= limit:
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        if len(self.hits) > 10_000:
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 60}
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("?", 0))[0]
        path = scope.get("path", "")
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")[-16:].decode(errors="ignore")
        ok = self._allow(f"pair:{ip}", self.pair_per_minute) if path == "/api/pair" else \
            self._allow(f"k:{auth or ip}", self.per_minute)
        if ok:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4429})
            return
        await JSONResponse({"error": "rate limit exceeded"}, status_code=429)(scope, receive, send)


def J(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(metrics.json_safe(data), status_code=status)


def create_app(rt: Runtime, lifespan=None) -> Starlette:
    store = rt.store

    def guard(action: str) -> Callable:
        def deco(fn):
            async def wrapper(request: Request):
                auth = request.headers.get("authorization", "")
                dev = authenticate(store, auth[7:] if auth.lower().startswith("bearer ") else "")
                if not dev:
                    return J({"error": "unauthorized"}, 401)
                if not can(dev["role"], action):
                    store.audit(f"device:{dev['id']}", "permission_denied", {"action": action, "path": request.url.path})
                    return J({"error": f"role '{dev['role']}' may not {action}"}, 403)
                request.state.device = dev
                request.state.actor = f"owner:{dev['name']}" if dev["role"] == "owner" else f"viewer:{dev['name']}"
                try:
                    return await fn(request)
                except (KeyError, ValueError) as e:
                    return J({"error": str(e)}, 400)
            return wrapper
        return deco

    def eng():
        return rt.engine

    # ---------------------------------------------------------------- misc
    async def health(request):
        try:
            h = rt.health()
        except Exception as e:  # report, never a bare 500
            return J({"ok": False, "service": "millionx", "mode": rt.s.mode, "engine_running": False,
                      "health_error": f"{type(e).__name__}: {e}"}, 503)
        h["service"] = "millionx"
        return J(h, 200 if h["ok"] else 503)

    async def pair(request):
        from .security import pairing_throttled
        ip = request.client.host if request.client else "?"
        if pairing_throttled(store, ip):
            return J({"error": "too many failed pairing attempts; try again later"}, 429)
        body = await request.json()
        r = redeem_pairing_code(store, str(body.get("code", "")), str(body.get("device_name", "android")), ip)
        return J(r) if r else J({"error": "invalid or expired code"}, 401)

    @guard("read")
    async def incidents(request):
        return J(store.q("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (int(request.query_params.get("limit", 100)),)))

    @guard("change_settings")
    async def incident_resolve(request):
        iid = int(request.path_params["iid"])
        store.x("UPDATE incidents SET resolved=1 WHERE id=?", (iid,))
        store.audit(request.state.actor, "incident_resolved", {"id": iid}, "owner")
        return J({"ok": True})

    @guard("read")
    async def me(request):
        return J(request.state.device)

    # ----------------------------------------------------------- dashboard
    @guard("read")
    async def dashboard(request):
        e = eng()
        if not e:
            return J({"engine_running": False, "engine_error": rt.engine_error, "mode": rt.s.mode})
        st = e.status_summary()
        rows = store.q("SELECT net_pnl FROM trades WHERE virtual=0 AND mode=?", (rt.s.mode,))
        wins = sum(1 for r in rows if r["net_pnl"] > 0)
        st.update(engine_running=True, total_trades=len(rows), win_rate=wins / len(rows) if rows else None,
                  latest_signals=e.last_signals[:10], goal_probability=_goal_probability(rt, e))
        return J(st)

    @guard("read")
    async def equity(request):
        hours = float(request.query_params.get("hours", 24))
        rows = store.q("SELECT ts, equity FROM equity WHERE mode=? AND ts>? ORDER BY ts",
                       (rt.s.mode, now_ms() - int(hours * 3_600_000)))
        step = max(1, len(rows) // 400)
        return J(rows[::step])

    # ---------------------------------------------------------------- bots
    def _bot_rows():
        e = eng()
        out = []
        protos = {b.bot_id: b for b in build_bots()}
        saved = {r["bot_id"]: r for r in store.q("SELECT * FROM bot_state")}
        for bid, b in protos.items():
            rows = store.q("SELECT * FROM trades WHERE bot_id=? AND mode=? ORDER BY closed_at", (bid, rt.s.mode))
            real = [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in rows if not r["virtual"]]
            virt = [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in rows if r["virtual"]]
            last_sig = next((s for s in (e.last_signals if e else []) if s["signal"]["bot_id"] == bid), None)
            st = e.sup.status.get(bid).value if e else (saved.get(bid, {}).get("status") or "RESEARCH")
            out.append({"bot_id": bid, "name": b.name, "description": b.description, "timeframe": b.timeframe,
                        "is_replacement": getattr(b, "is_replacement", False), "status": st,
                        "status_reason": (e.sup.status_reason.get(bid, "") if e else saved.get(bid, {}).get("reason")),
                        "weight": e.comp.weight(bid) if e else saved.get(bid, {}).get("weight", 1.0),
                        "params": {**b.params, **json.loads(saved.get(bid, {}).get("params") or "{}")},
                        "current_signal": last_sig, "real": metrics.compute(real, rt.s.starting_capital_eur),
                        "virtual": metrics.compute(virt, 100.0),
                        "open_position": next((p.to_dict() for p in (e.positions.values() if e else [])
                                               if p.bot_id == bid), None)})
        return out

    @guard("read")
    async def bots(request):
        return J(_bot_rows())

    @guard("read")
    async def bot_trades(request):
        bid = request.path_params["bot_id"]
        lim = int(request.query_params.get("limit", 100))
        return J(store.q("SELECT * FROM trades WHERE bot_id=? ORDER BY closed_at DESC LIMIT ?", (bid, lim)))

    @guard("manage_bots")
    async def bot_status(request):
        bid = request.path_params["bot_id"]
        body = await request.json()
        e = eng()
        if not e or bid not in e.comp.books:
            return J({"error": "unknown bot or engine stopped"}, 400)
        action = body.get("action")
        if action == "disable":
            e.sup.set_status(bid, BotStatus.DISABLED, f"disabled by {request.state.actor}")
        elif action == "enable":
            # enabling never grants live eligibility; that needs validation evidence
            e.sup.set_status(bid, BotStatus.PAPER, f"enabled by {request.state.actor}; live requires validation")
        elif action == "mark_live_eligible":
            from .validation import eligibility
            el = eligibility(store, rt.s, bid)
            if not el["eligible"]:
                return J({"error": "insufficient validation evidence", "eligibility": el}, 400)
            e.sup.set_status(bid, BotStatus.LIVE_ELIGIBLE, "validated: walk-forward + Monte Carlo + paper evidence")
        else:
            return J({"error": "action must be enable|disable|mark_live_eligible"}, 400)
        e.save_bot_state(bid)
        store.audit(request.state.actor, f"bot_{action}", {"bot": bid}, "owner")
        return J({"ok": True, "status": e.sup.status[bid].value})

    @guard("manage_bots")
    async def bot_params(request):
        bid = request.path_params["bot_id"]
        body = await request.json()
        cls = next((c for c in BASE_CLASSES if c.bot_id == bid), None)
        allowed = set(cls.default_params) if cls else {"min_trades", "min_pf", "min_exp_r"}
        bad = [k for k in body if k not in allowed]
        if bad:
            return J({"error": f"unknown params {bad}"}, 400)
        store.x("INSERT INTO bot_state(bot_id,status,reason,params,weight,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(bot_id) DO UPDATE SET params=excluded.params, status=excluded.status, "
                "reason=excluded.reason, updated_at=excluded.updated_at",
                (bid, "RESEARCH", "parameters changed: re-validation required", json.dumps(body), 1.0, now_ms()))
        e = eng()
        if e:
            e.sup.set_status(bid, BotStatus.RESEARCH, "parameters changed: re-validation required")
            e.bots.clear()
        store.audit(request.state.actor, "bot_params_changed", {"bot": bid, "params": body}, "owner")
        return J({"ok": True})

    @guard("read")
    async def bot_eligibility(request):
        from .validation import degradation, eligibility, regime_performance
        bid = request.path_params["bot_id"]
        return J({"eligibility": eligibility(store, rt.s, bid), "degradation": degradation(store, bid, rt.s.mode),
                  "regimes": regime_performance(store, bid, rt.s.mode),
                  "validation": store.kv_get(f"validation:{bid}")})

    @guard("read")
    async def strategies_compare(request):
        e = eng()
        if not e:
            return J({"error": "engine stopped"}, 400)
        from .validation import return_correlation
        return J({"leaderboard": e.comp.leaderboard(), "best_by_regime": e.comp.best_by_regime(),
                  "return_correlation": return_correlation(store, rt.s.mode, list(e.comp.books)),
                  "regime_table": e.comp.regimes.table(), "signal_correlation": e.sup.correlation_matrix(),
                  "history": e.comp.history[-30:]})

    # ------------------------------------------------------ trading views
    @guard("read")
    async def positions(request):
        e = eng()
        return J({"open": [p.to_dict() for p in e.positions.values()] if e else [],
                  "shadow": [p.to_dict() for p in e.virtual.values()] if e else []})

    @guard("read")
    async def orders(request):
        lim = int(request.query_params.get("limit", 100))
        return J(store.q("SELECT client_id, exchange_id, symbol, side, type, qty, price, status, purpose, mode, "
                         "created_at, updated_at FROM orders ORDER BY created_at DESC LIMIT ?", (lim,)))

    @guard("approve_trade")
    async def close_pos(request):
        e = eng()
        sym = request.path_params["symbol"]
        r = e.close_position(sym, f"manual close by {request.state.actor}") if e else {"ok": False}
        store.audit(request.state.actor, "manual_close", {"symbol": sym}, "owner", r)
        return J(r)

    @guard("read")
    async def approvals(request):
        e = eng()
        return J(e.status_summary()["pending_approvals"] if e else [])

    @guard("approve_trade")
    async def approve(request):
        e = eng()
        body = await request.json()
        return J(e.approve(request.path_params["signal_id"], bool(body.get("approve")), request.state.actor)
                 if e else {"ok": False})

    @guard("read")
    async def scanner(request):
        e = eng()
        return J(sorted(e.scanner.values(), key=lambda r: -(r.get("quote_volume") or 0)) if e else [])

    @guard("read")
    async def trades(request):
        q = "SELECT * FROM trades WHERE mode=?"
        args: list[Any] = [request.query_params.get("mode", rt.s.mode)]
        if request.query_params.get("bot"):
            q += " AND bot_id=?"
            args.append(request.query_params["bot"])
        if request.query_params.get("virtual") in ("0", "1"):
            q += " AND virtual=?"
            args.append(int(request.query_params["virtual"]))
        q += " ORDER BY closed_at DESC LIMIT ?"
        args.append(int(request.query_params.get("limit", 200)))
        return J(store.q(q, args))

    @guard("read")
    async def performance(request):
        rows = store.q("SELECT * FROM trades WHERE mode=? AND virtual=0 ORDER BY closed_at", (rt.s.mode,))
        tr = [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in rows]
        span = (tr[-1].closed_at - tr[0].opened_at) / 60_000 if len(tr) > 1 else None
        by_bot = {}
        for b in {t.bot_id for t in tr}:
            by_bot[b] = metrics.compute([t for t in tr if t.bot_id == b], rt.s.starting_capital_eur, span)
        return J({"overall": metrics.compute(tr, rt.s.starting_capital_eur, span), "by_bot": by_bot})

    # ---------------------------------------------------------------- risk
    @guard("read")
    async def risk(request):
        e = eng()
        rules = SymbolRules("ETHUSDT", min_notional=20.0, step_size=0.001, min_qty=0.001)
        if e and "ETHUSDT" in e.rules:
            rules = e.rules["ETHUSDT"]
        price = float(e.bars["ETHUSDT"].close[-1]) if e and "ETHUSDT" in e.bars else None
        feas = {}
        for name, cap in (("experimental_4eur", rt.s.experimental_capital_eur), ("multi_40eur", rt.s.multi_capital_eur)):
            if price:
                feas[name] = feasibility_report(cap * rt.s.eur_usdt, price, 0.004, rules, rt.s.risk, rt.s.fees)
        return J({"limits": rt.s.risk.__dict__, "fees": rt.s.fees.__dict__,
                  "state": e.risk.state.__dict__ if e else None,
                  "drawdown": e.risk.drawdown if e else None, "daily_pnl": e.risk.daily_pnl_frac if e else None,
                  "risk_fraction_now": e.risk.risk_fraction() if e else None,
                  "tighten_factor": e.risk.tighten_factor if e else None,
                  "exposure": sum(p.qty * p.entry_price for p in e.positions.values()) if e else 0.0,
                  "feasibility": feas, "feasibility_assumption": "ETHUSDT, 0.4% stop, configured fees"})

    @guard("change_risk_limits")
    async def risk_limits(request):
        body = await request.json()
        from .governance import apply_limit_changes, BOUNDS
        try:
            old = apply_limit_changes(rt.s.risk, body)
        except ValueError as e:
            store.audit(request.state.actor, "risk_limits_refused", {"requested": body, "error": str(e)})
            return J({"error": f"refused by risk governance: {e}", "bounds": BOUNDS}, 400)
        store.kv_set("risk_limits", rt.s.risk.__dict__)
        rt.gate.invalidate("risk limits changed", request.state.actor)
        store.audit(request.state.actor, "risk_limits_changed", {"old": old, "new": rt.s.risk.__dict__}, "owner")
        return J({"ok": True, "limits": rt.s.risk.__dict__})

    @guard("change_risk_limits")
    async def risk_tighten_reset(request):
        e = eng()
        if e:
            e.risk.tighten_factor = 1.0
        store.audit(request.state.actor, "risk_tighten_reset", {}, "owner")
        return J({"ok": True})

    # ------------------------------------------------------------- backtest
    @guard("change_settings")
    async def backtest_submit(request):
        body = await request.json()
        kind = body.get("kind", "backtest")
        if not body.get("symbols") or not body.get("start") or not body.get("end"):
            return J({"error": "symbols, start, end required"}, 400)
        return J({"job_id": rt.submit_job(kind, body)})

    @guard("read")
    async def backtest_list(request):
        return J(store.q("SELECT id, kind, status, request, progress, created_at, finished_at FROM jobs "
                         "ORDER BY created_at DESC LIMIT 50"))

    @guard("read")
    async def backtest_get(request):
        r = store.one("SELECT * FROM jobs WHERE id=?", (request.path_params["job_id"],))
        if not r:
            return J({"error": "not found"}, 404)
        r["result"] = json.loads(r["result"] or "null")
        r["request"] = json.loads(r["request"] or "null")
        return J(r)

    @guard("manage_bots")
    async def apply_validation(request):
        """Record the activation verdict of a finished walk-forward job."""
        r = store.one("SELECT * FROM jobs WHERE id=? AND kind='walkforward' AND status='done'",
                      (request.path_params["job_id"],))
        if not r:
            return J({"error": "no finished walk-forward job"}, 404)
        res = json.loads(r["result"])
        e = eng()
        out = {}
        for bid, wf in res.items():
            st = wf["activation"]["status"]
            store.kv_set(f"validation:{bid}", {"status": st, "job": r["id"], "ts": now_ms(),
                                               "oos_expectancy_r": (wf.get("oos_metrics") or {}).get("expectancy_r"),
                                               "oos_trades": (wf.get("oos_metrics") or {}).get("trades")})
            if e and st == "BACKTEST_PASSED":
                e.sup.set_status(bid, BotStatus.PAPER, f"walk-forward passed (job {r['id']}); collecting paper evidence")
                e.save_bot_state(bid)
            out[bid] = st
        store.audit(request.state.actor, "validation_applied", {"job": r["id"]}, "owner", out)
        return J(out)

    # ------------------------------------------------------- notifications
    @guard("read")
    async def notifications(request):
        return J(store.q("SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
                         (int(request.query_params.get("limit", 100)),)))

    @guard("read")
    async def notifications_read(request):
        store.x("UPDATE notifications SET read=1")
        return J({"ok": True})

    @guard("read")
    async def notification_prefs(request):
        if request.method == "PUT":
            store.kv_set("notification_prefs", await request.json())
        return J(store.kv_get("notification_prefs", {}))

    @guard("read")
    async def fcm_token(request):
        body = await request.json()
        store.x("UPDATE devices SET fcm_token=? WHERE id=?", (body.get("token"), request.state.device["id"]))
        return J({"ok": True, "push_enabled_on_server": rt.notifier.enabled})

    # ------------------------------------------------------------ settings
    @guard("read")
    async def settings_get(request):
        keys = {r["mode"]: True for r in store.q("SELECT mode FROM api_keys WHERE exchange='binance'")}
        return J({"mode": rt.s.mode, "execution": rt.s.execution, "capital_mode": rt.s.capital_mode,
                  "rest_base": rt.s.rest_base, "keys_stored": keys, "live_gate": rt.gate.status(),
                  "live_unlocked": rt.gate.is_valid(),
                  "preflight": store.kv_get("preflight_demo") or store.kv_get("preflight"),
                  "live_check": store.kv_get("live_check"), "api_permissions": store.kv_get("api_permissions"),
                  "brain_interval_min": store.kv_get("brain_interval_min", 60), "push_enabled": rt.notifier.enabled,
                  "llm_available": rt.org.llm.available})

    @guard("change_settings")
    async def settings_put(request):
        body = await request.json()
        changes = {}
        for k, allowed in (("mode", ("paper", "demo", "live")), ("execution", ("manual", "auto")),
                           ("capital_mode", ("experimental", "multi"))):
            if k in body:
                if body[k] not in allowed:
                    return J({"error": f"{k} must be one of {allowed}"}, 400)
                changes[k] = body[k]
        if changes.get("mode") == "live" and not rt.gate.is_valid():
            return J({"error": f"live mode locked: {rt.gate.status()['reason']}"}, 400)
        if "brain_interval_min" in body:
            store.kv_set("brain_interval_min", max(15, int(body["brain_interval_min"])))
            rt.org.interval_ms = max(15, int(body["brain_interval_min"])) * 60_000
        for k, v in changes.items():
            setattr(rt.s, k, v)
        rt.save_runtime()
        store.audit(request.state.actor, "settings_changed", body, "owner")
        if {"mode", "capital_mode"} & set(changes):
            await asyncio.get_running_loop().run_in_executor(None, rt.start_engine)
        return J({"ok": True, "engine_error": rt.engine_error})

    @guard("manage_keys")
    async def api_keys(request):
        body = await request.json()
        mode = body.get("mode")
        if mode not in ("demo", "live"):
            return J({"error": "mode must be demo or live"}, 400)
        if not rt.vault:
            return J({"error": "server has no MX_MASTER_KEY"}, 500)
        from .exchange.binance import BinanceFutures
        ex = BinanceFutures("https://fapi.binance.com" if mode == "live" else "https://demo-fapi.binance.com",
                            body["api_key"], body["api_secret"], live=mode == "live")
        try:
            bal = ex.balance()
            perms = ex.api_permissions()
        except Exception as e:
            return J({"error": f"key check failed: {e}"}, 400)
        if perms.get("withdrawals"):
            store.audit(request.state.actor, "api_key_rejected", {"mode": mode, "reason": "withdrawals enabled"})
            return J({"error": "REFUSED: this key has withdrawal permission. Create a key without it."}, 400)
        if mode == "live" and not perms.get("ip_restrict"):
            store.audit(request.state.actor, "api_key_rejected", {"mode": mode, "reason": "no IP restriction"})
            return J({"error": "REFUSED: live keys must be restricted to the server IP."}, 400)
        if mode == "live" and not perms.get("futures"):
            store.audit(request.state.actor, "api_key_rejected", {"mode": mode, "reason": "no futures permission"})
            return J({"error": "REFUSED: live key has no Futures trading permission."}, 400)
        store_api_keys(store, rt.vault, "binance", mode, body["api_key"], body["api_secret"])
        if mode == "live":
            rt.gate.invalidate("live API key rotated", request.state.actor)
        store.kv_set("api_permissions", perms)
        store.audit(request.state.actor, "api_keys_stored", {"mode": mode, "permissions": perms}, "owner")
        return J({"ok": True, "balance": bal, "permissions": perms})

    @guard("unlock_live")
    async def preflight(request):
        res = await asyncio.get_running_loop().run_in_executor(None, rt.preflight)
        return J(res)

    @guard("unlock_live")
    async def live_check(request):
        res = await asyncio.get_running_loop().run_in_executor(None, rt.live_check)
        return J(res)

    @guard("unlock_live")
    async def unlock_live(request):
        body = await request.json()
        res = rt.gate.unlock(request.state.actor, str(body.get("confirm", "")))
        return J(res, 200 if res.get("ok") else 400)

    # ----------------------------------------------------------- emergency
    @guard("emergency_stop")
    async def emergency(request):
        body = await request.json() if await request.body() else {}
        e = eng()
        if not e:
            return J({"error": "engine not running"}, 400)
        r = await asyncio.get_running_loop().run_in_executor(
            None, lambda: e.emergency_stop(request.state.actor, bool(body.get("flatten", True))))
        return J(r)

    @guard("change_settings")
    async def emergency_reset(request):
        e = eng()
        return J(e.emergency_reset(request.state.actor) if e else {"ok": False})


    # ------------------------------------------------ live activity & charts
    DEPT_LABEL = {"market_intelligence": "Market", "strategy_intelligence": "Supervisor",
                  "quant_research": "Validation", "capital_intelligence": "Risk sizing",
                  "execution_intelligence": "Execution plan", "cro": "CRO (risk officer)"}
    OUTCOME_LABEL = {"executed": "Trade opened", "awaiting_owner_approval": "Waiting for your approval",
                     "rejected": "Rejected", "vetoed": "Vetoed by risk", "rejected_at_approval": "Rejected at approval",
                     "duplicate_blocked": "Duplicate blocked", "blocked_by_governance": "Blocked by governance",
                     "execution_error": "Execution error", "rejected_by_exchange": "Rejected by exchange",
                     "no_symbol_rules": "No exchange rules for symbol"}

    def _why(steps: list) -> tuple[str, str]:
        """(department, reason) of the step that stopped a signal."""
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            res = st.get("result")
            stopped = res in ("rejected", "vetoed", "not_validated", "blocked", "error") or st.get("ok") is False
            if not stopped:
                continue
            reasons = st.get("reasons") or (st.get("decision") or {}).get("reasons") or \
                [st.get("reason") or st.get("error") or res]
            text = "; ".join(str(x) for x in reasons if x) or str(res)
            if res == "not_validated":
                text = f"bot status {st.get('bot_status')}: not allowed to trade in this mode"
            return DEPT_LABEL.get(st.get("dept", ""), st.get("dept", "") or "?"), text
        return "", ""

    @guard("read")
    async def activity(request):
        """What the engine is doing right now: signal funnel, decisions and shadow positions."""
        e = eng()
        lim = min(int(request.query_params.get("limit", 40)), 200)
        since = now_ms() - 24 * 3_600_000
        decisions = []
        block_reasons: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for r in store.q("SELECT ts, evidence, approval FROM audit WHERE action='trade_decision' AND ts>? "
                         "ORDER BY seq DESC LIMIT 400", (since,)):
            try:
                ev = json.loads(r["evidence"] or "{}")
            except ValueError:
                continue
            outcome = r["approval"]
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            dept, why = _why(ev.get("steps"))
            if why:
                key = f"{dept}: {why}"[:140]
                block_reasons[key] = block_reasons.get(key, 0) + 1
            if len(decisions) < lim:
                decisions.append({"ts": r["ts"], "bot": ev.get("bot"), "symbol": ev.get("symbol"),
                                  "side": ev.get("side"), "outcome": outcome,
                                  "outcome_label": OUTCOME_LABEL.get(outcome, outcome),
                                  "stopped_at": dept, "reason": why})
        sig_total = store.one("SELECT COUNT(*) n FROM signals WHERE ts>?", (since,))["n"]
        sup_rej: dict[str, int] = {}
        accepted = 0
        for r in store.q("SELECT verdict FROM signals WHERE ts>? ORDER BY ts DESC LIMIT 2000", (since,)):
            try:
                v = json.loads(r["verdict"] or "{}")
            except ValueError:
                continue
            if v.get("accepted"):
                accepted += 1
            else:
                for x in v.get("reasons") or ["rejected"]:
                    k = str(x).split(":")[0][:80]
                    sup_rej[k] = sup_rej.get(k, 0) + 1
        shadow = []
        if e is not None:
            for p in list(e.virtual.values()):
                bars = e.bars.get(p.symbol)
                px = float(bars.close[-1]) if bars is not None and len(bars) else p.entry_price
                upnl = p.unrealized(px) + p.realized_pnl - p.fees_paid
                shadow.append({"bot": p.bot_id, "symbol": p.symbol, "side": "LONG" if int(p.side) > 0 else "SHORT",
                               "entry": p.entry_price, "price": px, "stop": p.stop, "tp1": p.tp1, "tp2": p.tp2,
                               "r_now": upnl / p.risk_amount if p.risk_amount else 0.0, "opened_at": p.opened_at})
            shadow.sort(key=lambda x: -x["opened_at"])
        vt = store.one("SELECT COUNT(*) n, SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) w, SUM(r_multiple) r "
                       "FROM trades WHERE virtual=1 AND mode=? AND closed_at>?", (rt.s.mode, since))
        top = sorted(block_reasons.items(), key=lambda kv: -kv[1])[:6]
        return J({"window_h": 24,
                  "funnel": {"signals": sig_total, "supervisor_accepted": accepted,
                             "supervisor_rejected": sig_total - accepted,
                             "executed": outcomes.get("executed", 0),
                             "awaiting_approval": outcomes.get("awaiting_owner_approval", 0),
                             "blocked": sum(v for k, v in outcomes.items()
                                            if k not in ("executed", "awaiting_owner_approval"))},
                  "top_block_reasons": [{"reason": k, "count": v} for k, v in top],
                  "top_supervisor_reasons": [{"reason": k, "count": v}
                                             for k, v in sorted(sup_rej.items(), key=lambda kv: -kv[1])[:5]],
                  "execution": rt.s.execution, "mode": rt.s.mode,
                  "shadow_open": shadow, "shadow_24h": {"trades": vt["n"] or 0, "wins": vt["w"] or 0,
                                                        "sum_r": vt["r"] or 0.0},
                  "decisions": decisions})

    @guard("read")
    async def candles(request):
        """Last N one-minute candles of a symbol plus bot signals and open levels for the chart."""
        e = eng()
        sym = request.path_params["symbol"].upper()
        n = max(20, min(int(request.query_params.get("n", 90)), 300))
        if e is None or sym not in e.bars:
            return J({"symbol": sym, "candles": [], "signals": [], "levels": [],
                      "available": sorted(e.bars.keys()) if e else []})
        b = e.bars[sym]
        k = min(n, len(b))
        ot = b.open_time[-k:]
        rows = [{"t": int(ot[i]), "o": float(b.open[-k + i]), "h": float(b.high[-k + i]),
                 "l": float(b.low[-k + i]), "c": float(b.close[-k + i]), "v": float(b.volume[-k + i])}
                for i in range(k)]
        t0 = int(ot[0]) if k else 0
        sigs = []
        for r in store.q("SELECT ts, bot_id, data, verdict FROM signals WHERE symbol=? AND ts>=? ORDER BY ts",
                         (sym, t0)):
            try:
                d, v = json.loads(r["data"] or "{}"), json.loads(r["verdict"] or "{}")
            except ValueError:
                continue
            sigs.append({"ts": r["ts"], "bot": r["bot_id"], "side": d.get("side"), "price": d.get("entry"),
                         "accepted": bool(v.get("accepted")), "reason": d.get("reason", "")})
        levels = []
        for p in list(e.positions.values()) + [p for p in list(e.virtual.values())]:
            if p.symbol != sym:
                continue
            levels.append({"bot": p.bot_id, "virtual": bool(p.virtual), "side": "LONG" if int(p.side) > 0 else "SHORT",
                           "entry": p.entry_price, "stop": p.stop, "tp1": p.tp1, "tp2": p.tp2})
        return J({"symbol": sym, "candles": rows, "signals": sigs[-60:], "levels": levels,
                  "available": sorted(e.bars.keys())})

    @guard("read")
    async def competition_curves(request):
        """Cumulative R of every bot's shadow (paper) trades: the strategy competition."""
        hours = float(request.query_params.get("hours", 72))
        since = now_ms() - int(hours * 3_600_000)
        out: dict[str, dict[str, Any]] = {}
        for r in store.q("SELECT bot_id, closed_at, r_multiple FROM trades WHERE virtual=1 AND mode=? AND closed_at>? "
                         "ORDER BY closed_at", (rt.s.mode, since)):
            c = out.setdefault(r["bot_id"], {"bot": r["bot_id"], "points": [], "trades": 0, "wins": 0, "sum_r": 0.0})
            c["sum_r"] += r["r_multiple"] or 0.0
            c["trades"] += 1
            c["wins"] += 1 if (r["r_multiple"] or 0) > 0 else 0
            c["points"].append([r["closed_at"], round(c["sum_r"], 4)])
        res = sorted(out.values(), key=lambda c: -c["sum_r"])
        for c in res:
            if len(c["points"]) > 200:
                step = len(c["points"]) / 200
                c["points"] = [c["points"][int(i * step)] for i in range(200)] + [c["points"][-1]]
        return J({"hours": hours, "bots": res})

    # ---------------------------------------------------- AI model for agents
    @guard("read")
    async def org_activity(request):
        lim = min(int(request.query_params.get("limit", 40)), 200)
        return J({"llm": rt.org.llm.describe(), "next_cycle_in_ms": rt.org.next_cycle_in_ms(),
                  "interval_min": rt.org.effective_interval_ms // 60_000, "runs": rt.org.activity(lim)})

    @guard("read")
    async def llm_get(request):
        from .org.llm import PRESETS
        return J({**rt.org.llm.describe(),
                  "presets": {k: {"base_url": v[1], "model": v[2], "min_gap_s": v[3]} for k, v in PRESETS.items()}})

    @guard("manage_keys")
    async def llm_put(request):
        import base64
        body = await request.json()
        if body.get("disconnect"):
            rt.org.llm.disconnect()
            store.kv_set("llm_config", {})
            store.audit(request.state.actor, "llm_disconnected", {})
            return J(rt.org.llm.describe())
        if not rt.vault:
            return J({"error": "no master key: cannot store the AI key encrypted"}, 500)
        provider = str(body.get("provider", ""))
        key = str(body.get("api_key", "")).strip()
        if len(key) < 10:
            return J({"error": "API key missing"}, 400)
        from .org.llm import LLMClient
        test = LLMClient()
        test.configure(provider, key, str(body.get("base_url", "")), str(body.get("model", "")), min_gap_s=0)
        r = await asyncio.to_thread(test.complete, "Reply with exactly: OK", "Connection test.", max_tokens=200)
        if r.error:
            store.audit(request.state.actor, "llm_connect_failed", {"provider": provider, "error": r.error[:200]})
            return J({"error": f"connection test failed: {r.error}"}, 400)
        rt.org.llm.configure(provider, key, test.base_url, test.model_override or str(body.get("model", "")))
        store.kv_set("llm_config", {"provider": provider, "base_url": test.base_url,
                                    "model": test.model_override, "key_enc": base64.b64encode(rt.vault.enc(key)).decode()})
        store.audit(request.state.actor, "llm_connected", {"provider": provider, "model": test.model_override,
                                                           "test_latency_ms": r.latency_ms})
        rt.org.last_cycle = 0  # run the agents with the model on the next tick
        return J({**rt.org.llm.describe(), "test_reply": r.text[:80], "test_latency_ms": r.latency_ms})

    # ------------------------------------------------------- organisation
    @guard("read")
    async def org_agents(request):
        return J(rt.org.overview())

    @guard("read")
    async def org_agent(request):
        aid = request.path_params["agent_id"]
        if aid not in AGENTS:
            return J({"error": "unknown agent"}, 404)
        runs = store.q("SELECT id, ts, kind, model, prompt_version, output, confidence, input_tokens, output_tokens, "
                       "cost_usd, latency_ms, error FROM agent_runs WHERE agent_id=? ORDER BY ts DESC LIMIT 30", (aid,))
        for r in runs:
            r["output"] = json.loads(r["output"] or "{}")
        return J({"agent": next(a for a in rt.org.overview() if a["id"] == aid), "config": rt.org.config(aid),
                  "prompt": rt.org.prompt(aid), "versions": rt.org.versions(aid), "runs": runs})

    @guard("read")
    async def org_run(request):
        r = store.one("SELECT * FROM agent_runs WHERE id=?", (request.path_params["run_id"],))
        if not r:
            return J({"error": "not found"}, 404)
        r["input"] = json.loads(r["input"] or "{}")
        r["output"] = json.loads(r["output"] or "{}")
        return J(r)

    @guard("edit_prompts")
    async def org_prompt(request):
        aid = request.path_params["agent_id"]
        body = await request.json()
        v = rt.org.update_prompt(aid, body["prompt"], request.state.actor, body.get("note", ""))
        return J({"ok": True, "version": v})

    @guard("edit_prompts")
    async def org_activate(request):
        aid = request.path_params["agent_id"]
        body = await request.json()
        rt.org.activate_version(aid, int(body["version"]), request.state.actor)
        return J({"ok": True})

    @guard("edit_prompts")
    async def org_config(request):
        aid = request.path_params["agent_id"]
        body = await request.json()
        return J(rt.org.set_config(aid, request.state.actor, body.get("enabled"), body.get("model"),
                                   body.get("max_tokens"), body.get("temperature")))

    @guard("edit_prompts")
    async def org_trigger(request):
        aid = request.path_params["agent_id"]
        out = await asyncio.get_running_loop().run_in_executor(None, lambda: rt.org.run_agent(aid, eng(), "manual"))
        return J(out)

    @guard("read")
    async def meetings(request):
        rows = store.q("SELECT data FROM meetings ORDER BY ts DESC LIMIT ?", (int(request.query_params.get("limit", 10)),))
        return J([json.loads(r["data"]) for r in rows])

    @guard("edit_prompts")
    async def meeting_now(request):
        out = await asyncio.get_running_loop().run_in_executor(None, lambda: rt.org.board_meeting(eng()))
        return J(out)

    # --------------------------------------------------------------- audit
    @guard("read")
    async def audit(request):
        q, args = "SELECT * FROM audit", []
        if request.query_params.get("actor"):
            q += " WHERE actor LIKE ?"
            args.append(request.query_params["actor"] + "%")
        q += " ORDER BY seq DESC LIMIT ?"
        args.append(int(request.query_params.get("limit", 200)))
        rows = store.q(q, args)
        for r in rows:
            for k in ("evidence", "result"):
                try:
                    r[k] = json.loads(r[k])
                except (TypeError, ValueError):
                    pass
        return J(rows)

    @guard("read")
    async def audit_verify(request):
        return J(store.verify_audit())

    # --------------------------------------------------------------- admin
    @guard("pair_device")
    async def devices(request):
        return J(store.q("SELECT id, name, role, created_at, last_seen, revoked, fcm_token IS NOT NULL AS push "
                         "FROM devices ORDER BY created_at"))

    @guard("pair_device")
    async def pairing_code(request):
        body = await request.json()
        return J({"code": create_pairing_code(store, body.get("role", "viewer"))})

    @guard("pair_device")
    async def revoke(request):
        did = request.path_params["device_id"]
        if did == request.state.device["id"]:
            return J({"error": "cannot revoke the device you are using"}, 400)
        store.x("UPDATE devices SET revoked=1 WHERE id=?", (did,))
        store.audit(request.state.actor, "device_revoked", {"device": did}, "owner")
        return J({"ok": True})

    # ------------------------------------------------------------ websocket
    async def ws(websocket: WebSocket):
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            dev = authenticate(store, json.loads(first).get("token", ""))
        except Exception:
            dev = None
        if not dev:
            await websocket.send_text(json.dumps({"type": "error", "data": "unauthorized"}))
            await websocket.close(code=4401)
            return
        q = rt.bus.subscribe(asyncio.get_running_loop())
        await websocket.send_text(json.dumps({"type": "hello", "data": {"role": dev["role"], "mode": rt.s.mode}}))
        for m in list(rt.bus.recent)[-20:]:
            await websocket.send_text(json.dumps(m, default=str))
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    await websocket.send_text(msg)
                except asyncio.TimeoutError:
                    await websocket.send_text(json.dumps({"type": "ping", "ts": now_ms()}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.bus.unsubscribe(q)

    routes = [
        Route("/api/activity", activity), Route("/api/candles/{symbol}", candles),
        Route("/api/competition/curves", competition_curves), Route("/api/org/activity", org_activity),
        Route("/api/settings/llm", llm_get), Route("/api/settings/llm", llm_put, methods=["PUT"]),
        Route("/api/health", health), Route("/api/pair", pair, methods=["POST"]), Route("/api/me", me),
        Route("/api/dashboard", dashboard), Route("/api/equity", equity),
        Route("/api/bots", bots), Route("/api/bots/{bot_id}/trades", bot_trades),
        Route("/api/bots/{bot_id}/status", bot_status, methods=["POST"]),
        Route("/api/bots/{bot_id}/params", bot_params, methods=["PUT"]),
        Route("/api/bots/{bot_id}/eligibility", bot_eligibility),
        Route("/api/strategies/compare", strategies_compare),
        Route("/api/positions", positions), Route("/api/orders", orders),
        Route("/api/positions/{symbol}/close", close_pos, methods=["POST"]),
        Route("/api/approvals", approvals), Route("/api/approvals/{signal_id}", approve, methods=["POST"]),
        Route("/api/scanner", scanner), Route("/api/trades", trades), Route("/api/performance", performance),
        Route("/api/risk", risk), Route("/api/risk/limits", risk_limits, methods=["PUT"]),
        Route("/api/risk/tighten-reset", risk_tighten_reset, methods=["POST"]),
        Route("/api/backtests", backtest_submit, methods=["POST"]), Route("/api/backtests", backtest_list),
        Route("/api/backtests/{job_id}", backtest_get),
        Route("/api/backtests/{job_id}/apply-validation", apply_validation, methods=["POST"]),
        Route("/api/notifications", notifications), Route("/api/notifications/read", notifications_read, methods=["POST"]),
        Route("/api/notifications/prefs", notification_prefs, methods=["GET", "PUT"]),
        Route("/api/devices/fcm", fcm_token, methods=["POST"]),
        Route("/api/settings", settings_get), Route("/api/settings", settings_put, methods=["PUT"]),
        Route("/api/settings/apikeys", api_keys, methods=["POST"]),
        Route("/api/preflight", preflight, methods=["POST"]), Route("/api/unlock-live", unlock_live, methods=["POST"]),
        Route("/api/live-check", live_check, methods=["POST"]),
        Route("/api/emergency-stop", emergency, methods=["POST"]),
        Route("/api/emergency-reset", emergency_reset, methods=["POST"]),
        Route("/api/org/agents", org_agents), Route("/api/org/agents/{agent_id}", org_agent),
        Route("/api/org/agents/{agent_id}/prompt", org_prompt, methods=["PUT"]),
        Route("/api/org/agents/{agent_id}/activate", org_activate, methods=["POST"]),
        Route("/api/org/agents/{agent_id}/config", org_config, methods=["PUT"]),
        Route("/api/org/agents/{agent_id}/run", org_trigger, methods=["POST"]),
        Route("/api/org/runs/{run_id}", org_run),
        Route("/api/org/meetings", meetings), Route("/api/org/meetings", meeting_now, methods=["POST"]),
        Route("/api/audit", audit), Route("/api/audit/verify", audit_verify),
        Route("/api/incidents", incidents), Route("/api/incidents/{iid}/resolve", incident_resolve, methods=["POST"]),
        Route("/api/admin/devices", devices), Route("/api/admin/pairing-code", pairing_code, methods=["POST"]),
        Route("/api/admin/devices/{device_id}/revoke", revoke, methods=["POST"]),
        WebSocketRoute("/ws", ws),
    ]
    from starlette.middleware import Middleware

    async def on_error(request, exc):
        import logging
        logging.getLogger("millionx.api").exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": f"server error: {type(exc).__name__}: {exc}"}, status_code=500)

    return Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(RateLimiter)],
                     exception_handlers={500: on_error})


def _goal_probability(rt: Runtime, e) -> dict[str, Any]:
    """Probability of the 1M EUR goal from REAL closed trades only."""
    from .backtest import montecarlo
    rows = rt.store.q("SELECT r_multiple, closed_at FROM trades WHERE virtual=0 AND mode=? ORDER BY closed_at",
                      (rt.s.mode,))
    if len(rows) < 20:
        return {"available": False,
                "reason": f"{len(rows)} closed trades; at least 20 real trades are needed before any estimate",
                "needed_doublings": math.log2(rt.s.goal_eur / rt.s.starting_capital_eur),
                "consecutive_1R_wins_per_doubling": math.log(2) / math.log(1 + rt.s.risk.risk_per_trade)}
    days = max(1.0, (rows[-1]["closed_at"] - rows[0]["closed_at"]) / 86_400_000)
    tpd = len(rows) / days
    res = montecarlo.goal_probability([r["r_multiple"] for r in rows], rt.s.starting_capital_eur, rt.s.goal_eur,
                                      tpd, 18, rt.s.risk.risk_per_trade, runs=1000)
    return res
