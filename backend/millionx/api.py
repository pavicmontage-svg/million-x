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
        if path == "/api/pair":
            ok = self._allow(f"pair:{ip}", self.pair_per_minute)
        elif path == "/api/tradingview/webhook":
            ok = self._allow(f"tv:{ip}", 30)
        else:
            ok = self._allow(f"k:{auth or ip}", self.per_minute)
        if ok:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4429})
            return
        await JSONResponse({"error": "rate limit exceeded"}, status_code=429)(scope, receive, send)


MINUTE = 60_000


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
                feas[name] = feasibility_report(cap * rt.s.eur_usdt, price, 0.0042, rules, rt.s.risk, rt.s.fees)
        from .governance import RISK_PROFILES
        return J({"limits": rt.s.risk.__dict__, "fees": rt.s.fees.__dict__,
                  "profile": store.kv_get("risk_profile", "standard"), "profiles": RISK_PROFILES, "mode": rt.s.mode,
                  "state": e.risk.state.__dict__ if e else None,
                  "drawdown": e.risk.drawdown if e else None, "daily_pnl": e.risk.daily_pnl_frac if e else None,
                  "risk_fraction_now": e.risk.risk_fraction() if e else None,
                  "tighten_factor": e.risk.tighten_factor if e else None,
                  "exposure": sum(p.qty * p.entry_price for p in e.positions.values()) if e else 0.0,
                  "feasibility": feas, "feasibility_assumption": "ETHUSDT, 0.42% stop (fee-aware minimum), configured fees"})

    @guard("change_risk_limits")
    async def risk_limits(request):
        body = await request.json()
        from .governance import apply_limit_changes, BOUNDS
        try:
            old = apply_limit_changes(rt.s.risk, body, rt.s.mode)
        except ValueError as e:
            store.audit(request.state.actor, "risk_limits_refused", {"requested": body, "error": str(e)})
            return J({"error": f"refused by risk governance: {e}", "bounds": BOUNDS}, 400)
        store.kv_set("risk_limits", rt.s.risk.__dict__)
        rt.gate.invalidate("risk limits changed", request.state.actor)
        store.audit(request.state.actor, "risk_limits_changed", {"old": old, "new": rt.s.risk.__dict__}, "owner")
        return J({"ok": True, "limits": rt.s.risk.__dict__})

    @guard("change_risk_limits")
    async def risk_profile(request):
        """Switch between safe / standard / aggressive presets (hard BOUNDS still apply)."""
        from .governance import RISK_PROFILES, SIM_ONLY_PROFILES, apply_limit_changes
        body = await request.json()
        name = str(body.get("profile", ""))
        if name not in RISK_PROFILES:
            return J({"error": f"profile must be one of {list(RISK_PROFILES)}"}, 400)
        if name in SIM_ONLY_PROFILES and rt.s.mode == "live":
            return J({"error": "MAX is only available in PAPER or DEMO (virtual money)"}, 400)
        if rt.s.mode == "live" and name == "aggressive" and body.get("confirm") != "I ACCEPT HIGH RISK":
            return J({"error": "LIVE + aggressive needs confirm='I ACCEPT HIGH RISK'"}, 400)
        try:
            old = apply_limit_changes(rt.s.risk, RISK_PROFILES[name], rt.s.mode)
        except ValueError as e:
            return J({"error": f"refused by risk governance: {e}"}, 400)
        store.kv_set("risk_limits", rt.s.risk.__dict__)
        store.kv_set("risk_profile", name)
        rt.gate.invalidate(f"risk profile changed to {name}", request.state.actor)
        store.audit(request.state.actor, "risk_profile_changed", {"profile": name, "old": old,
                                                                   "new": rt.s.risk.__dict__}, "owner")
        return J({"ok": True, "profile": name, "limits": rt.s.risk.__dict__})

    @guard("change_risk_limits")
    async def risk_cooldown_reset(request):
        """Owner override of the loss-streak pause (audited)."""
        e = eng()
        if not e:
            return J({"error": "engine not running"}, 400)
        st = e.risk.state
        before = {"consecutive_losses": st.consecutive_losses, "cooldown_until": st.cooldown_until}
        st.cooldown_until = 0
        st.consecutive_losses = 0
        store.audit(request.state.actor, "loss_streak_pause_reset", before, "owner")
        return J({"ok": True})

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
                  "timeframe": rt.s.strategy_timeframe,
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
                           ("capital_mode", ("experimental", "multi", "pro"))):
            if k in body:
                if body[k] not in allowed:
                    return J({"error": f"{k} must be one of {allowed}"}, 400)
                changes[k] = body[k]
        if changes.get("mode") == "live" and not rt.gate.is_valid():
            return J({"error": f"live mode locked: {rt.gate.status()['reason']}"}, 400)
        if changes.get("mode") == "live":
            from .governance import validate_limits
            errs = validate_limits(rt.s.risk.__dict__, "live")
            if errs:
                return J({"error": "current risk limits are only allowed with virtual money; choose the Standard "
                                   "or Aggressive profile first: " + "; ".join(errs)}, 400)
        if "timeframe" in body:
            tf = int(body["timeframe"])
            if tf not in (0, 5, 15, 30, 60, 240):
                return J({"error": "timeframe must be 0 (bot defaults), 5, 15, 30, 60 or 240 minutes"}, 400)
            changes["strategy_timeframe"] = tf
        if "brain_interval_min" in body:
            store.kv_set("brain_interval_min", max(15, int(body["brain_interval_min"])))
            rt.org.interval_ms = max(15, int(body["brain_interval_min"])) * 60_000
        for k, v in changes.items():
            setattr(rt.s, k, v)
        rt.save_runtime()
        store.audit(request.state.actor, "settings_changed", body, "owner")
        if {"mode", "capital_mode", "strategy_timeframe"} & set(changes):
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
        _acct_cache.clear()
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
    DEPT_LABEL = {"risk_governance": "Final pre-trade check", "execution": "Execution", "market_intelligence": "Market", "strategy_intelligence": "Supervisor",
                  "quant_research": "Validation", "capital_intelligence": "Risk sizing",
                  "execution_intelligence": "Execution plan", "cro": "CRO (risk officer)"}
    OUTCOME_LABEL = {"executed": "Trade opened", "awaiting_owner_approval": "Waiting for your approval",
                     "rejected": "Rejected", "vetoed": "Vetoed by risk", "rejected_at_approval": "Rejected at approval",
                     "duplicate_blocked": "Duplicate blocked", "blocked_by_governance": "Blocked by governance",
                     "execution_error": "Execution error", "rejected_by_exchange": "Rejected by exchange",
                     "awaiting_ai_supervisor": "Waiting for AI supervisor",
                     "no_symbol_rules": "No exchange rules for symbol"}

    PLAIN = {"loss_streak_cooldown": "paused after several losses in a row (risk rule; Risk → Reset pause or change profile)",
             "daily_loss_limit": "daily loss limit reached (resets at 00:00 UTC)",
             "max_drawdown_reached": "maximum drawdown reached",
             "max_open_positions": "maximum number of open positions reached",
             "stale_market_data": "market data is late",
             "suspended_by_risk_oversight": "suspended by risk oversight (Risk → reset oversight factor)"}

    def _why(steps: list) -> tuple[str, str]:
        """(department, reason) of the step that stopped a signal."""
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            res = st.get("result")
            stopped = res in ("rejected", "vetoed", "not_validated", "blocked", "error") or st.get("ok") is False
            if not stopped:
                continue
            reasons = st.get("violations") or st.get("reasons") or (st.get("decision") or {}).get("reasons") or \
                [st.get("reason") or st.get("error") or res]
            text = "; ".join(PLAIN.get(str(x), str(x)) for x in reasons if x) or str(res)
            if res == "not_validated":
                text = f"bot status {st.get('bot_status')}: not allowed to trade in this mode"
            return DEPT_LABEL.get(st.get("dept", ""), st.get("dept", "") or "?"), text
        return "", ""

    def _engine_diag(e, sig_24h: int) -> dict[str, Any]:
        """Plain-language answer to 'why is nothing happening?' from the engine's real state."""
        if e is None:
            return {"running": False, "verdict": f"Trading engine is not running: {rt.engine_error or 'unknown reason'}",
                    "level": "critical"}
        alive = bool(e.thread and e.thread.is_alive())
        now = now_ms()
        up_min = (now - e.started_at) / 60_000
        ages = [(now - (e.last_bar_ms.get(s, 0) + MINUTE)) / 1000 for s in e.universe if e.last_bar_ms.get(s)]
        scanned = [v for v in e.scanner.values() if "ok" in v]
        failed = [v for v in scanned if not v.get("ok")]
        d = {"running": alive, "mode": rt.s.mode, "volume_source": getattr(e, "volume_source_used", "venue"), "uptime_min": round(up_min, 1), "universe": list(e.universe),
             "bars_processed": e.bars_processed, "signals_since_start": e.signals_seen,
             "data_fresh": e.risk.state.data_fresh, "connection_ok": e.risk.state.connection_ok,
             "last_bar_age_s": round(max(ages), 1) if ages else None, "errors": e.errors[-5:],
             "liquidity_failed": [{"symbol": v["symbol"], "spread_bps": v.get("spread_bps"),
                                   "depth": min(v.get("depth_bid_10bps") or 0, v.get("depth_ask_10bps") or 0),
                                   "reason": v.get("reason")} for v in failed[:10]],
             "limits": {"max_spread_bps": rt.s.max_spread_bps, "min_depth_usdt": rt.s.min_depth_usdt},
             "recent_trades": store.q("SELECT bot_id, symbol, side, net_pnl, exit_reason, closed_at FROM trades "
                                      "WHERE virtual=0 AND mode=? ORDER BY closed_at DESC LIMIT 6", (rt.s.mode,))}
        if not alive:
            d.update(level="critical", verdict="Engine thread stopped. Open Admin → Engine on this phone → Start.")
        elif e.risk.state.emergency_stop:
            d.update(level="critical", verdict="EMERGENCY STOP is active: no new trades until you reset it in Risk.")
        elif not e.universe:
            d.update(level="warning", verdict=(
                f"No market passed the liquidity filter (spread ≤ {rt.s.max_spread_bps} bps and ≥ "
                f"{rt.s.min_depth_usdt:,.0f} USDT depth within 10 bps on each side). {len(failed)} of {len(scanned)} "
                "scanned markets failed — see the list below." if scanned else
                "Market list not loaded yet (or Binance did not answer). " + ("; ".join(e.errors[-2:]) or "")))
        elif now < e.risk.state.cooldown_until:
            mins = max(1, round((e.risk.state.cooldown_until - now) / 60_000))
            d.update(level="warning", cooldown_until=e.risk.state.cooldown_until, verdict=(
                f"New trades paused for another {mins} min after "
                f"{rt.s.risk.max_consecutive_losses} losing trades in a row. This is the hard risk rule, not the AI "
                "CRO. You can reset the pause or pick another risk profile in Risk."))
        elif not e.risk.state.connection_ok:
            d.update(level="critical", verdict="Connection to Binance is failing: " + "; ".join(e.errors[-2:]))
        elif e.bars_processed == 0:
            d.update(level="info", verdict=f"Watching {len(e.universe)} markets; waiting for the first closed 1-minute candle.")
        elif not e.risk.state.data_fresh:
            d.update(level="warning", verdict="Market data is late, so new entries are paused: " + "; ".join(e.errors[-2:]))
        elif e.signals_seen == 0 and sig_24h == 0:
            d.update(level="info", verdict=(
                f"Working normally: watching {len(e.universe)} markets for {up_min:.0f} min and analysed "
                f"{e.bars_processed} candles. No bot has found its setup yet. Each bot only fires on its specific "
                "pattern (e.g. a breakout with volume, a VWAP stretch, a squeeze release), so quiet markets can "
                "stay silent for a while."))
        else:
            d.update(level="ok", verdict=(f"Working: {len(e.universe)} markets, {e.bars_processed} candles analysed, "
                                          f"{e.signals_seen} bot signals since start."))
        return d

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
        return J({"window_h": 24, "engine": _engine_diag(e, sig_total),
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


    # ------------------------------------------- Binance account (read-only)
    _acct_cache: dict[tuple, tuple[float, dict]] = {}
    PNL_TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")

    def _summarize_income(rows: list, since: int, day0: int) -> dict[str, Any]:
        tot = {"realized": 0.0, "commission": 0.0, "funding": 0.0}
        today = 0.0
        closes = wins = 0
        daily: dict[int, float] = {}
        curve = []
        cum = 0.0
        for r in sorted(rows, key=lambda x: int(x.get("time", 0))):
            t, typ, v = int(r.get("time", 0)), r.get("incomeType"), float(r.get("income", 0) or 0)
            if typ not in PNL_TYPES or t < since:
                continue
            tot[{"REALIZED_PNL": "realized", "COMMISSION": "commission", "FUNDING_FEE": "funding"}[typ]] += v
            if typ == "REALIZED_PNL":
                closes += 1
                wins += 1 if v > 0 else 0
            if t >= day0:
                today += v
            d = t // 86_400_000 * 86_400_000
            daily[d] = daily.get(d, 0.0) + v
            cum += v
            curve.append([t, round(cum, 6)])
        if len(curve) > 300:
            step = len(curve) / 300
            curve = [curve[int(i * step)] for i in range(300)] + [curve[-1]]
        return {**tot, "net": tot["realized"] + tot["commission"] + tot["funding"], "today_net": today,
                "closing_fills": closes, "winning_fills": wins,
                "daily": [{"day": k, "net": v} for k, v in sorted(daily.items())], "curve": curve}

    @guard("read")
    async def exchange_account(request):
        """Read-only view of the connected Binance futures account: balance, positions, real P/L."""
        from .exchange.base import ExchangeError
        from .exchange.binance import BinanceFutures
        from .security import load_api_keys
        stored = {m: bool(store.one("SELECT 1 FROM api_keys WHERE exchange='binance' AND mode=?", (m,)))
                  for m in ("demo", "live")}
        which = request.query_params.get("account") or (
            rt.s.mode if rt.s.mode in ("demo", "live") else ("demo" if stored["demo"] or not stored["live"] else "live"))
        if which not in ("demo", "live"):
            return J({"error": "account must be demo or live"}, 400)
        days = max(1.0, min(float(request.query_params.get("days", 7)), 30.0))
        base = {"account": which, "stored": stored, "engine_mode": rt.s.mode,
                "bots_trade_here": rt.s.mode == which, "days": days}
        keys = load_api_keys(store, rt.vault, "binance", which) if rt.vault else None
        if not keys:
            return J({**base, "connected": False})
        ck = (which, days)
        hit = _acct_cache.get(ck)
        if hit and hit[0] > now_ms() - 15_000:
            data = hit[1]
        else:
            ex = BinanceFutures("https://fapi.binance.com" if which == "live" else "https://demo-fapi.binance.com",
                                keys[0], keys[1], live=which == "live")
            now = now_ms()

            def fetch():
                return ex.balance(), ex.position_details(), ex.income_history(now - int(days * 86_400_000))
            try:
                bal, pos, inc = await asyncio.to_thread(fetch)
            except ExchangeError as e:
                msg = str(e)
                if e.code in (401, -2014, -2015) or "-2015" in msg or "-2014" in msg:
                    msg = "Binance rejected the API key (wrong key/secret, or Futures permission missing). " + msg
                return J({**base, "connected": True, "error": msg[:300]})
            day0 = now // 86_400_000 * 86_400_000
            data = {"balance": bal, "positions": pos, "pnl": _summarize_income(inc, now - int(days * 86_400_000), day0),
                    "income": [{"time": int(r.get("time", 0)), "symbol": r.get("symbol", ""),
                                "type": r.get("incomeType"), "amount": float(r.get("income", 0) or 0),
                                "asset": r.get("asset", "")}
                               for r in sorted(inc, key=lambda x: -int(x.get("time", 0)))[:60]],
                    "fetched_at": now}
            _acct_cache[ck] = (now_ms(), data)
        bots = store.q("SELECT bot_id, COUNT(*) n, SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) w, SUM(net_pnl) pnl "
                       "FROM trades WHERE virtual=0 AND mode=? GROUP BY bot_id ORDER BY pnl DESC", (which,))
        return J({**base, "connected": True, **data, "bot_results": bots})


    # ------------------------------------------------- AI command center (1.9)
    @guard("read")
    async def ai_status(request):
        agents = rt.org.overview()
        e = eng()
        return J({"chain": rt.ai_chain.status(), "desk": rt.ai_desk.status(),
                  "agents": [{k: a.get(k) for k in ("id", "name", "title", "kind", "status", "active", "last_run",
                                                     "last_summary", "last_error")} for a in agents],
                  "pending_ai_review": [sid for sid, p in (e.pending.items() if e else [])
                                        if p.record.get("stage") == "ai_review"]})

    @guard("manage_keys")
    async def ai_provider_put(request):
        pid = request.path_params["pid"]
        if pid not in rt.ai_chain.providers:
            return J({"error": "unknown provider"}, 404)
        if not rt.vault and request.method == "PUT":
            return J({"error": "no master key: cannot store AI keys encrypted"}, 500)
        body = await request.json()
        p = rt.ai_chain.configure(pid, key=body.get("api_key"), model=body.get("model"),
                                  enabled=body.get("enabled"), timeout_s=body.get("timeout_s"))
        hc = await asyncio.to_thread(p.health_check) if p.key else {"ok": False, "error": "no key"}
        store.audit(request.state.actor, "ai_provider_configured",
                    {"provider": pid, "model": p.model or "auto", "enabled": p.enabled, "health": hc.get("ok")})
        return J({"status": p.status(), "health": hc})

    @guard("read")
    async def ai_provider_test(request):
        pid = request.path_params["pid"]
        p = rt.ai_chain.providers.get(pid)
        if not p or not p.key:
            return J({"error": "provider not configured"}, 400)
        hc = await asyncio.to_thread(p.health_check)
        if not hc.get("ok"):
            return J({"health": hc, "status": p.status()})
        from .ai.providers import parse_json_obj
        r = await asyncio.to_thread(p.chat, "Return the JSON object {\"ok\": true} and nothing else.",
                                    "health check", 50, 0.0)
        fmt_ok = False
        if r.get("ok"):
            try:
                fmt_ok = parse_json_obj(r["text"]).get("ok") is True
            except Exception:
                fmt_ok = False
        return J({"health": hc, "chat_ok": bool(r.get("ok")), "json_format_ok": fmt_ok, "model": r.get("model"),
                  "latency_ms": r.get("latency_ms"), "error": r.get("error"), "status": p.status()})

    @guard("read")
    async def ai_decisions(request):
        lim = min(int(request.query_params.get("limit", 60)), 300)
        agent = request.query_params.get("agent")
        q, a = "SELECT * FROM ai_decisions", []
        if agent:
            q, a = q + " WHERE agent=?", [agent]
        rows = store.q(q + " ORDER BY ts DESC LIMIT ?", (*a, lim))
        for r in rows:
            r["payload"] = json.loads(r["payload"] or "{}")
        return J(rows)

    @guard("read")
    async def ai_errors(request):
        return J(store.q("SELECT * FROM ai_errors ORDER BY id DESC LIMIT 100"))

    @guard("change_settings")
    async def ai_control(request):
        body = await request.json()
        act = str(body.get("action", ""))
        d = rt.ai_desk
        if act == "pause":
            rt.ai_chain.paused = True
            rt.ai_chain.save()
        elif act == "resume":
            rt.ai_chain.paused = False
            rt.ai_chain.save()
        elif act in ("trader_on", "trader_off"):
            d.update_settings(trader_enabled=act == "trader_on")
        elif act in ("supervisor_on", "supervisor_off"):
            d.update_settings(supervisor_enabled=act == "supervisor_on")
        elif act == "settings":
            d.update_settings(**{k: body.get(k) for k in ("trader_interval_min", "trader_min_confidence",
                                                          "trader_max_trades_per_run")})
        elif act == "run_trader_now":
            e = eng()
            if not e:
                return J({"error": "engine not running"}, 400)
            if not d.trader_active():
                return J({"error": "Independent Trader is disabled, paused or has no AI provider"}, 400)
            res = await asyncio.to_thread(d.run_trader, e)
            store.audit(request.state.actor, "ai_control", {"action": act})
            return J({"ok": True, "result": res})
        else:
            return J({"error": "unknown action"}, 400)
        store.audit(request.state.actor, "ai_control", {"action": act}, "owner")
        return J({"ok": True, "chain": rt.ai_chain.status(), "desk": d.status()})

    async def tradingview_webhook(request):
        """Public endpoint (TradingView cannot send tokens): authenticated by the secret in the body."""
        from .tradingview import handle
        raw = await request.body()
        if len(raw) > 8192:
            return J({"error": "payload too large"}, 413)
        code, out = handle(store, eng(), raw, {k.lower(): v for k, v in request.headers.items()})
        return J(out, code)

    @guard("manage_keys")
    async def tradingview_setup(request):
        from .tradingview import secret
        rotate = request.method == "POST"
        s = secret(store, rotate=rotate)
        if rotate:
            store.audit(request.state.actor, "tv_secret_rotated", {})
        return J({"webhook_path": "/api/tradingview/webhook", "secret": s,
                  "alert_message": {"secret": s, "id": "{{strategy.order.id}}-{{timenow}}", "symbol": "{{ticker}}",
                                    "side": "LONG", "timeframe": "{{interval}}", "time": "{{timenow}}",
                                    "price": "{{close}}", "stop": 0, "take_profit": 0},
                  "note": "Needs a public HTTPS server (VPS). The phone engine on 127.0.0.1 cannot receive webhooks.",
                  "recent": store.q("SELECT id, received_at, symbol, side, timeframe, status, reason FROM tv_signals "
                                    "ORDER BY received_at DESC LIMIT 30")})

    @guard("read")
    async def moneyline_status(request):
        return J({"implemented": False, "strategy": "Moneyline Breakout Entry v3.5",
                  "reason": "No specification or source code has been provided; it is not invented or substituted.",
                  "needed": ["exact entry/exit rules or the original Pine Script source",
                             "parameter defaults", "timeframe(s)", "example trades to verify the implementation"],
                  "comparison_plan": "once implemented: backtest + walk-forward against bot4_breakout and "
                                     "bot9_rangeexp on the same data"})

    @guard("read")
    async def performance_audit(request):
        from .auditor import report
        days = max(1.0, min(float(request.query_params.get("days", 30)), 365.0))
        e = eng()
        start = e.start_capital_usdt if e else rt.s.starting_capital_eur * rt.s.eur_usdt
        return J(await asyncio.to_thread(report, store, rt.s.mode, start, days))

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

    # ---- Research 2.0 (results from CI, selection, parameter history) ---------------
    @guard("read")
    async def research2_overview(request):
        from . import research2_store
        return J(research2_store.overview(store, eng(), rt.s))

    @guard("manage_bots")
    async def research2_import(request):
        from . import research2_store
        raw = await request.body()
        if len(raw) > 8_000_000:
            return J({"error": "file too large"}, 413)
        try:
            agg = json.loads(raw)
            out = research2_store.import_results(store, agg, f"upload by {request.state.actor}")
        except (ValueError, TypeError, AttributeError) as ex:
            return J({"error": f"not a research2.json: {ex}"}, 400)
        return J(out)

    # ---- 2.1 microstructure data (collector, snapshot, research results) ---------------
    @guard("read")
    async def micro_status(request):
        from .micro import snapshot as S
        db = rt.micro_db()
        try:
            st = S.collector_status(db)
        finally:
            if db:
                db.close()
        st.update({"thread_alive": rt.collector_running(), "symbols": rt.micro_symbols,
                   "enabled": (store.kv_get("micro_collector") or {}).get("enabled", False)})
        return J(st)

    @guard("manage_bots")
    async def micro_collector(request):
        body = await request.json()
        if body.get("enabled"):
            syms = [str(x).upper() for x in body.get("symbols") or []][:20] or None
            rt.start_collector(syms)
        else:
            rt.stop_collector()
        store.audit(request.state.actor, "micro_collector", {"enabled": bool(body.get("enabled"))}, "owner")
        return J({"ok": True, "running": rt.collector_running()})

    @guard("read")
    async def micro_snapshot(request):
        from .micro import snapshot as S
        sym = request.path_params["symbol"].upper()
        e = eng()
        bars = e.bars.get(sym) if e else None
        reg, reg_ts = None, None
        if e and bars is not None and len(bars):
            try:
                from .regime2 import classify
                r2 = classify(bars)
                reg, reg_ts = r2.at_1m(len(bars) - 1)[0], int(bars.open_time[-1]) + 60_000
            except Exception:
                pass
        db = rt.micro_db()
        try:
            return J(S.build(sym, db, bars=bars, regime=reg, regime_ts=reg_ts))
        finally:
            if db:
                db.close()

    @guard("read")
    async def micro_series(request):
        sym = request.path_params["symbol"].upper()
        minutes = max(5, min(1440, int(request.query_params.get("minutes", 180))))
        db = rt.micro_db()
        if db is None:
            return J({"symbol": sym, "rows": [], "note": "no recorded data yet (collector never ran)"})
        try:
            since = now_ms() - minutes * 60_000
            q = ("SELECT b.m AS t, b.imb10, b.spread_bps, b.depth10, t.delta, f.funding_rate, o.oi FROM "
                 "(SELECT ts/60000*60000 m, AVG(imb10) imb10, AVG(spread_bps) spread_bps, AVG(bid10+ask10) depth10 "
                 " FROM book_1s WHERE sym=? AND ts>=? GROUP BY m) b "
                 "LEFT JOIN (SELECT ts/60000*60000 m, SUM(buy_qty-sell_qty) delta FROM trades_1s WHERE sym=? AND ts>=? GROUP BY m) t ON t.m=b.m "
                 "LEFT JOIN (SELECT ts/60000*60000 m, AVG(funding_rate) funding_rate FROM funding_1s WHERE sym=? AND ts>=? GROUP BY m) f ON f.m=b.m "
                 "LEFT JOIN oi_1m o ON o.sym=? AND o.ts=b.m ORDER BY b.m")
            rows = db.execute(q, (sym, since, sym, since, sym, since, sym)).fetchall()
            cols = ["t", "imb10", "spread_bps", "depth10", "delta", "funding_rate", "oi"]
            liq = db.execute("SELECT ev_ts, side, notional FROM liquidations WHERE sym=? AND ev_ts>=? ORDER BY ev_ts",
                             (sym, since)).fetchall()
            return J({"symbol": sym, "rows": [dict(zip(cols, r)) for r in rows],
                      "liquidations": [{"ts": a, "side": b, "notional": c} for a, b, c in liq],
                      "liquidation_note": "lower bound: at most 1 liquidation per symbol per second is published"})
        finally:
            db.close()

    @guard("read")
    async def micro_research(request):
        from .micro import results_store
        return J(results_store.overview())

    # ---- 2.2 continuous scalping scanner (virtual PaperBook; never sends exchange orders) -------
    def _scalp_db():
        return rt.micro_db()

    @guard("read")
    async def scalp_status(request):
        sc = getattr(rt, "scanner", None)
        st = sc.status() if sc else {"stats": None, "note": "scanner runs with the collector (Research Data -> Record)"}
        st["running"] = bool(sc) and rt.collector_running()
        db = _scalp_db()
        try:
            if db is not None:
                try:
                    since = now_ms() - 86_400_000
                    agg = db.execute("SELECT decision, COUNT(*) FROM scalp_opps WHERE ts>=? GROUP BY decision", (since,)).fetchall()
                    missed = db.execute("SELECT COUNT(*) FROM scalp_opps WHERE ts>=? AND decision='REJECT' AND outcome_r>0",
                                        (since,)).fetchone()[0]
                    st["last_24h"] = {"by_decision": {a: b for a, b in agg}, "missed_profitable": missed}
                except Exception:
                    st["last_24h"] = None
        finally:
            if db:
                db.close()
        return J(st)

    @guard("read")
    async def scalp_opps(request):
        lim = max(1, min(500, int(request.query_params.get("limit", 100))))
        dec = request.query_params.get("decision")
        db = _scalp_db()
        if db is None:
            return J({"rows": []})
        try:
            q = "SELECT id, ts, sym, engine, cfg, side, price, decision, reasons, eval, snapshot, outcome_r, outcome_reason, outcome_hold FROM scalp_opps"
            args: tuple = ()
            if dec in ("ACCEPT", "REJECT"):
                q += " WHERE decision=?"
                args = (dec,)
            try:
                rows = db.execute(q + " ORDER BY id DESC LIMIT ?", args + (lim,)).fetchall()
            except Exception:
                rows = []
            cols = ["id", "ts", "sym", "engine", "cfg", "side", "price", "decision", "reasons", "eval", "snapshot",
                    "outcome_r", "outcome_reason", "outcome_hold"]
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                for k in ("reasons", "eval", "snapshot"):
                    try:
                        d[k] = json.loads(d[k]) if d[k] else None
                    except ValueError:
                        pass
                out.append(d)
            return J({"rows": out})
        finally:
            db.close()

    @guard("read")
    async def scalp_paper(request):
        db = _scalp_db()
        if db is None:
            return J({"trades": [], "summary": {}})
        try:
            try:
                rows = db.execute("SELECT sym, engine, side, opened_ts, closed_ts, entry, exit, gross_bps, net_bps, r, pnl_eur, fees_eur, "
                                  "lev, reason FROM scalp_paper ORDER BY id DESC LIMIT 300").fetchall()
            except Exception:
                rows = []
            cols = ["sym", "engine", "side", "opened_ts", "closed_ts", "entry", "exit", "gross_bps", "net_bps", "r", "pnl_eur",
                    "fees_eur", "lev", "reason"]
            tr = [dict(zip(cols, r)) for r in rows]
            summ: dict = {}
            if tr:
                rs = [t["r"] for t in tr]
                hold = [(t["closed_ts"] - t["opened_ts"]) / 1000 for t in tr]
                summ = {"n": len(tr), "net_eur": sum(t["pnl_eur"] for t in tr), "fees_eur": sum(t["fees_eur"] for t in tr),
                        "gross_eur": sum(t["pnl_eur"] + t["fees_eur"] for t in tr), "avg_hold_s": sum(hold) / len(hold),
                        "win_rate": sum(1 for x in rs if x > 0) / len(rs), "avg_r": sum(rs) / len(rs)}
            return J({"trades": tr, "summary": summ, "note": "virtual paper fills on live data; no exchange orders"})
        finally:
            db.close()

    @guard("read")
    async def scalp_research(request):
        import pkgutil
        try:
            raw = pkgutil.get_data("millionx", "data/scalp_research_summary.json")
            return J(json.loads(raw) if raw else {"available": False})
        except (OSError, FileNotFoundError, ValueError):
            return J({"available": False})

    routes = [
        Route("/api/scalp/status", scalp_status),
        Route("/api/scalp/opportunities", scalp_opps),
        Route("/api/scalp/paper", scalp_paper),
        Route("/api/scalp/research", scalp_research),
        Route("/api/micro/status", micro_status),
        Route("/api/micro/collector", micro_collector, methods=["POST"]),
        Route("/api/micro/snapshot/{symbol}", micro_snapshot),
        Route("/api/micro/series/{symbol}", micro_series),
        Route("/api/micro/research", micro_research),
        Route("/api/research2", research2_overview),
        Route("/api/research2/import", research2_import, methods=["POST"]),
        Route("/api/exchange/account", exchange_account),
        Route("/api/ai/status", ai_status), Route("/api/ai/decisions", ai_decisions), Route("/api/ai/errors", ai_errors),
        Route("/api/ai/providers/{pid}", ai_provider_put, methods=["PUT"]),
        Route("/api/ai/providers/{pid}/test", ai_provider_test, methods=["POST"]),
        Route("/api/ai/control", ai_control, methods=["POST"]),
        Route("/api/performance/audit", performance_audit),
        Route("/api/tradingview/webhook", tradingview_webhook, methods=["POST"]),
        Route("/api/tradingview/setup", tradingview_setup, methods=["GET", "POST"]),
        Route("/api/strategies/moneyline", moneyline_status),
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
        Route("/api/risk/profile", risk_profile, methods=["POST"]),
        Route("/api/risk/cooldown-reset", risk_cooldown_reset, methods=["POST"]),
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
