"""HTTP + WebSocket API and static dashboard (Starlette).

Every /api route except auth bootstrap and /api/health requires a bearer token (session or device
token). Mutating routes are audited. Dangerous actions need an explicit confirmation value in the
request body (the dashboard collects it with a typed confirmation dialog).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import sys
import time
from contextlib import asynccontextmanager
import traceback
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import __version__
from ..backtest.metrics import compute_metrics
from ..config import ExchangeEnv, TradingMode, dump, load
from ..core.logging import memory_handler, get_logger
from ..core.types import TradeRecord, new_id, now_ms
from ..data.history import HistoryStore
from ..engines import ENGINE_ORDER
from ..research.lab import ResearchLab
from ..runtime.trader import Trader
from ..security.auth import Auth, RateLimiter

log = get_logger("api")
from .. import web as _web_pkg  # noqa: E402  (importing it makes Chaquopy extract the files on Android)

WEB_DIR = Path(os.environ.get("QT_WEB_DIR", Path(_web_pkg.__file__).resolve().parent))
MAX_BODY = 64 * 1024
PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
CONFIRM = {"close_all": "CLOSE ALL", "live": "ENABLE LIVE TRADING", "reset_breaker": "RESET", "paper_reset": "RESET PAPER"}


def _clean(x):
    """Make any structure JSON-safe: non-finite floats → None, numpy scalars → Python, sets → lists."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        try:
            x = x.item()
        except (ValueError, AttributeError):
            return str(x)
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if hasattr(x, "value") and hasattr(x, "name") and not isinstance(x, (int, float, str)):
        return x.value
    return x


class SafeJSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(_clean(content), ensure_ascii=False, allow_nan=False, default=str, separators=(",", ":")).encode()


def ok(data=None, status: int = 200) -> JSONResponse:
    return SafeJSON(data if data is not None else {"ok": True}, status_code=status)


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth: Auth) -> None:
        super().__init__(app)
        self.auth = auth
        self.api_limiter = RateLimiter(900, 60)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        ip = request.client.host if request.client else "?"
        if path.startswith("/api/"):
            if self.api_limiter.hit(ip):
                return err("rate limit exceeded", 429)
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_BODY:
                return err("request too large", 413)
            if path not in PUBLIC:
                tok = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
                who = self.auth.check(tok)
                if not who:
                    return err("authentication required", 401)
                request.state.user = who
        resp: Response = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'")
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


def create_app(trader: Trader, auth: Auth, *, autostart: bool = True, history_dir: str = "data/history") -> Starlette:
    db = trader.db
    jobs: dict[str, dict] = {}
    history = HistoryStore(history_dir)

    async def body(request: Request) -> dict:
        try:
            raw = await request.body()
            if len(raw) > MAX_BODY:
                raise ValueError("too large")
            d = json.loads(raw or b"{}")
            if not isinstance(d, dict):
                raise ValueError("expected an object")
            return d
        except (ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"invalid JSON body: {e}")

    def audit(request: Request, action: str, detail: dict | None = None) -> None:
        db.audit(getattr(request.state, "user", "anonymous"), action, detail,
                 request.client.host if request.client else "")

    def handler(fn):
        async def wrapped(request: Request):
            try:
                return await fn(request)
            except PermissionError as e:
                return err(str(e), 403)
            except (ValueError, KeyError) as e:
                return err(str(e).strip("'"), 400)
            except Exception as e:
                log.error("API error %s: %s", request.url.path, traceback.format_exc(limit=3))
                return err(f"internal error: {type(e).__name__}", 500)
        return wrapped

    # ------------------------------------------------------------------ auth
    @handler
    async def auth_state(request):
        return ok({"has_user": auth.has_user(), "device_mode": bool(auth.device_token), "version": __version__})

    @handler
    async def auth_setup(request):
        d = await body(request)
        auth.create_user(str(d.get("username", "")), str(d.get("password", "")))
        db.audit(d.get("username", ""), "create_admin", {}, request.client.host if request.client else "")
        return ok()

    @handler
    async def auth_login(request):
        d = await body(request)
        tok = auth.login(str(d.get("username", "")), str(d.get("password", "")), request.client.host if request.client else "?")
        return ok({"token": tok})

    @handler
    async def auth_logout(request):
        auth.logout((request.headers.get("authorization") or "").removeprefix("Bearer ").strip())
        return ok()

    @handler
    async def health(request):
        return ok({"ok": True, "running": trader.running, "version": __version__, "ts": now_ms()})

    # ------------------------------------------------------------------ overview / markets
    @handler
    async def status(request):
        return ok(trader.status())

    @handler
    async def markets(request):
        return ok({"views": trader.last_views, "universe": trader.universe, "symbols": trader.symbols,
                   "pairs": trader.pair_stats, "market": trader.analyst.last})

    @handler
    async def regime(request):
        return ok({"analyst": trader.analyst.last, "eligibility": trader.cfg.engines.regime.eligibility,
                   "states": {s: r.to_dict() for s, r in trader.engines["regime"].last_states.items()}})

    # ------------------------------------------------------------------ config
    @handler
    async def get_config(request):
        return ok({"config": dump(trader.cfg), "versions": db.config_versions(30)})

    @handler
    async def patch_config(request):
        d = await body(request)
        patch = d.get("patch") or {}
        new = trader.update_config(patch, request.state.user, str(d.get("note", ""))[:200])
        audit(request, "config_update", {"patch": patch})
        if d.get("restart_venue"):
            await trader.restart_venue()
        return ok({"config": dump(new)})

    @handler
    async def rollback_config(request):
        d = await body(request)
        cfgj = db.config_version(int(d["id"]))
        if cfgj is None:
            raise KeyError("no such version")
        cfgj["execution"]["exchange_env"] = trader.cfg.execution.exchange_env.value  # venue is never changed by rollback
        new = trader.update_config(cfgj, request.state.user, f"rollback to v{d['id']}")
        audit(request, "config_rollback", {"id": d["id"]})
        return ok({"config": dump(new)})

    # ------------------------------------------------------------------ engines
    @handler
    async def engines(request):
        out = []
        for n in ENGINE_ORDER:
            e = trader.engines[n]
            info = e.describe()
            info.pop("config", None)
            info["edges"] = [x.to_dict() for x in trader.edge.all_estimates() if x.key.startswith(n + "|")]
            out.append(info)
        return ok(out)

    @handler
    async def engine_detail(request):
        n = request.path_params["name"]
        if n not in trader.engines:
            raise KeyError("unknown engine")
        e = trader.engines[n]
        trades = [t for t in db.trades(5000) if t["engine"] == n]
        recs = [TradeRecord(**t) for t in trades]
        per_symbol = {s: v["signals"].get(n) for s, v in trader.last_views.items()} if n != "regime" else \
            {s: v["regime"] for s, v in trader.last_views.items()}
        return ok({**e.describe(), "doc": sys.modules[type(e).__module__].__doc__,
                   "edges": [x.to_dict() for x in trader.edge.all_estimates() if x.key.startswith(n + "|")],
                   "performance": compute_metrics(recs, trader.cfg.system.paper_starting_equity),
                   "current": per_symbol, "pairs": trader.pair_stats if n == "stat_arb" else None})

    @handler
    async def engine_toggle(request):
        n = request.path_params["name"]
        d = await body(request)
        if n not in trader.engines:
            raise KeyError("unknown engine")
        trader.update_config({"engines": {n: {"enabled": bool(d.get("enabled"))}}}, request.state.user, f"toggle {n}")
        audit(request, "engine_toggle", {"engine": n, "enabled": bool(d.get("enabled"))})
        return ok()

    # ------------------------------------------------------------------ positions / trades / decisions
    @handler
    async def positions(request):
        return ok(trader.status()["positions"])

    @handler
    async def close_position(request):
        d = await body(request)
        if d.get("confirm") is not True:
            raise ValueError("confirmation required")
        audit(request, "close_position", {"id": request.path_params["pid"]})
        return ok(await trader.close_position(request.path_params["pid"]))

    @handler
    async def close_all(request):
        d = await body(request)
        if d.get("confirm") != CONFIRM["close_all"]:
            raise ValueError(f"type '{CONFIRM['close_all']}' to confirm")
        audit(request, "close_all")
        return ok(await trader.close_all())

    @handler
    async def trades(request):
        lim = min(int(request.query_params.get("limit", 200)), 5000)
        return ok(db.trades(lim, request.query_params.get("mode")))

    @handler
    async def decisions(request):
        lim = min(int(request.query_params.get("limit", 100)), 1000)
        return ok(db.decisions(lim, request.query_params.get("symbol"), request.query_params.get("actionable") == "1"))

    @handler
    async def decision_approve(request):
        audit(request, "decision_approve", {"id": request.path_params["did"]})
        return ok(await trader.approve_pending(request.path_params["did"]))

    @handler
    async def decision_reject(request):
        audit(request, "decision_reject", {"id": request.path_params["did"]})
        trader.reject_pending(request.path_params["did"])
        return ok()

    # ------------------------------------------------------------------ risk / control
    @handler
    async def risk(request):
        return ok({**trader.risk.report(trader.pf), "limits": dump(trader.cfg.risk),
                   "events": db.query("SELECT * FROM breaker_events ORDER BY id DESC LIMIT 100")})

    @handler
    async def reset_breaker(request):
        d = await body(request)
        if d.get("confirm") != CONFIRM["reset_breaker"]:
            raise ValueError(f"type '{CONFIRM['reset_breaker']}' to confirm")
        trader.risk.breakers.manual_reset(str(d["name"]), confirm=True)
        audit(request, "breaker_reset", {"name": d["name"]})
        return ok()

    @handler
    async def control(request):
        action = request.path_params["action"]
        d = await body(request)
        if action == "pause":
            trader.update_config({"system": {"trading_paused": True}}, request.state.user, "pause")
        elif action == "resume":
            if "EMERGENCY_STOP" in trader.risk.breakers.active:
                raise PermissionError("emergency stop active — reset it on the Risk page first")
            trader.update_config({"system": {"trading_paused": False}}, request.state.user, "resume")
        elif action == "emergency_stop":
            if d.get("confirm") is not True:
                raise ValueError("confirmation required")
            res = await trader.emergency_stop(bool(d.get("flatten")), request.state.user)
            audit(request, "emergency_stop", {"flatten": bool(d.get("flatten"))})
            return ok(res)
        elif action == "start":
            await trader.start()
        elif action == "stop":
            await trader.stop()
        else:
            raise KeyError("unknown action")
        audit(request, f"control_{action}")
        return ok({"status": trader.status()["paused"]})

    # ------------------------------------------------------------------ agents / performance
    @handler
    async def agents(request):
        return ok(trader.agents_report())

    @handler
    async def performance(request):
        mode = request.query_params.get("mode")
        recs = [TradeRecord(**t) for t in db.trades(10000, mode)]
        return ok({"metrics": compute_metrics(recs, trader.cfg.system.paper_starting_equity),
                   "equity": db.equity_curve(limit=3000), "audit": trader.auditor.last})

    # ------------------------------------------------------------------ paper trading
    @handler
    async def paper(request):
        ex = trader.exchange
        fills = [f.__dict__ for f in getattr(ex, "fills", [])[-200:]]
        return ok({"venue": trader.venue, "wallet": getattr(ex, "wallet", None), "fills": fills[::-1],
                   "funding_paid": dict(getattr(ex, "funding_paid", {})),
                   "note": "Paper fills are simulated against live data and are not guaranteed real fills."})

    @handler
    async def paper_reset(request):
        d = await body(request)
        if d.get("confirm") != CONFIRM["paper_reset"]:
            raise ValueError(f"type '{CONFIRM['paper_reset']}' to confirm")
        if trader.venue != "PAPER":
            raise PermissionError("only the paper account can be reset")
        if trader.pos and trader.pos.positions:
            raise PermissionError("close paper positions first")
        eq = float(d.get("equity") or trader.cfg.system.paper_starting_equity)
        trader.pf.balance = eq
        trader.pf.peak_equity = trader.pf.month_peak_equity = trader.pf.day_start_equity = eq
        trader.pf.week_start_equity = trader.pf.month_start_equity = eq
        if hasattr(trader.exchange, "wallet"):
            trader.exchange.wallet = eq
        trader._persist()
        audit(request, "paper_reset", {"equity": eq})
        return ok()

    # ------------------------------------------------------------------ research lab
    async def _run_job(jid: str, kind: str, params: dict) -> None:
        jobs[jid]["status"] = "running"
        db.save_experiment(jid, kind, "running", params, None)
        try:
            syms = params.get("symbols") or trader.symbols[:3] or ["BTCUSDT"]
            days = float(params.get("days", 30))
            end = dt.date.today() - dt.timedelta(days=1)
            start = end - dt.timedelta(days=int(days + 8))
            loop = asyncio.get_running_loop()

            def work():
                data, funding = {}, {}
                for s in syms:
                    c = history.load(s, "5m")
                    if c is None or len(c) == 0 or c.ts[-1] < (time.time() - 3 * 86400) * 1000:
                        c = history.download(s, "5m", start, end)
                    data[s] = c.tail(int((days + 8) * 288))
                    funding[s] = history.load_funding(s)
                cfg = load(params["config"]) if params.get("config") else trader.cfg
                lab = ResearchLab(cfg, data, {k: v for k, v in funding.items() if v is not None})
                if kind == "backtest":
                    r = lab.backtest(cfg, label="backtest")
                    return {**r.to_dict(), "monte_carlo": lab.monte_carlo(r.trades, r.equity0, n=500)}
                if kind == "train_test":
                    return lab.train_test(float(params.get("train_frac", 0.6)))
                if kind == "policy_comparison":
                    return lab.policy_comparison()
                if kind == "walk_forward":
                    return lab.walk_forward(params.get("grid") or {}, float(params.get("train_days", 20)),
                                            float(params.get("test_days", 7)), edge_book=None)
                if kind == "sensitivity":
                    return lab.sensitivity(params.get("grid") or {})
                raise ValueError("unknown experiment kind")

            res = await loop.run_in_executor(None, work)
            jobs[jid].update(status="done", result=res)
            db.save_experiment(jid, kind, "done", {k: v for k, v in params.items() if k != "config"}, res)
            trader.researcher.add_lab_result({"id": jid, "kind": kind, "summary": res.get("metrics", {}).get("expectancy_r")
                                              if isinstance(res, dict) else None})
        except Exception as e:
            log.exception("research job %s", jid)
            jobs[jid].update(status="failed", error=str(e))
            db.save_experiment(jid, kind, "failed", params, {"error": str(e)})

    @handler
    async def research_run(request):
        d = await body(request)
        kind = str(d.get("kind", "backtest"))
        if sum(1 for j in jobs.values() if j["status"] in ("queued", "running")) >= 2:
            raise PermissionError("two experiments already running")
        jid = new_id("exp_")
        jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "params": d, "ts": now_ms()}
        asyncio.get_running_loop().create_task(_run_job(jid, kind, d))
        audit(request, "research_run", {"kind": kind})
        return ok({"id": jid})

    @handler
    async def research_jobs(request):
        live = [{k: v for k, v in j.items() if k != "result"} for j in jobs.values()]
        return ok({"running": live, "history": db.experiments(30)})

    @handler
    async def research_job(request):
        jid = request.path_params["jid"]
        if jid in jobs:
            return ok(jobs[jid])
        rows = [r for r in db.experiments(200) if r["id"] == jid]
        if not rows:
            raise KeyError("unknown job")
        return ok(rows[0])

    # ------------------------------------------------------------------ API keys / live
    @handler
    async def keys(request):
        out = {}
        for env in ("testnet", "live"):
            out[env] = {"key": trader.secrets.preview(f"binance_{env}_key"), "secret_set": trader.secrets.get(f"binance_{env}_secret") is not None}
        out["telegram"] = trader.secrets.preview("telegram_bot_token")
        out["live_allowed_by_deployment"] = os.environ.get("QT_ALLOW_LIVE") == "1"
        out["venue"] = trader.venue
        return ok(out)

    @handler
    async def keys_set(request):
        d = await body(request)
        env = str(d.get("env", "")).lower()
        if env == "telegram":
            trader.secrets.set("telegram_bot_token", str(d["token"]).strip())
        elif env in ("testnet", "live"):
            k, s = str(d.get("key", "")).strip(), str(d.get("secret", "")).strip()
            if not (20 <= len(k) <= 128 and 20 <= len(s) <= 128) or not k.isalnum() or not s.isalnum():
                raise ValueError("API key/secret format invalid")
            trader.secrets.set(f"binance_{env}_key", k)
            trader.secrets.set(f"binance_{env}_secret", s)
        else:
            raise ValueError("env must be testnet, live or telegram")
        audit(request, "secret_set", {"env": env})
        return ok()

    @handler
    async def keys_delete(request):
        env = request.path_params["env"]
        for n in (f"binance_{env}_key", f"binance_{env}_secret") if env != "telegram" else ("telegram_bot_token",):
            trader.secrets.delete(n)
        audit(request, "secret_delete", {"env": env})
        return ok()

    @handler
    async def keys_test(request):
        from ..execution.binance_futures import BinanceFutures
        d = await body(request)
        env = str(d.get("env", "testnet")).upper()
        k, s = trader.secrets.get(f"binance_{env.lower()}_key"), trader.secrets.get(f"binance_{env.lower()}_secret")
        if not k or not s:
            raise ValueError("no key stored for " + env)
        c = BinanceFutures(k, s, env)
        try:
            await c.sync_time()
            res = await c.check_account_setup()
            restr = await c.api_restrictions()
            if restr is not None:
                res["restrictions"] = {x: restr.get(x) for x in ("enableWithdrawals", "enableFutures", "ipRestrict", "enableReading")}
                if restr.get("enableWithdrawals"):
                    res["ok"] = False
                    res["problem"] = "this key allows WITHDRAWALS — create a key without withdrawal permission"
                    return ok(res)
            res["ok"] = True
            db.kv_set(f"keycheck_{env}", {"ok": True, "ts": now_ms(), "restrictions": res.get("restrictions")})
            return ok(res)
        finally:
            await c.close()

    @handler
    async def live_enable(request):
        d = await body(request)
        if d.get("confirm") != CONFIRM["live"]:
            raise ValueError(f"type '{CONFIRM['live']}' to confirm")
        if os.environ.get("QT_ALLOW_LIVE") != "1":
            raise PermissionError("live trading is disabled by the deployment (set QT_ALLOW_LIVE=1 on the server)")
        chk = db.kv_get("keycheck_LIVE")
        if not chk or not chk.get("ok") or now_ms() - chk["ts"] > 3600_000:
            raise PermissionError("run a successful LIVE key check (no withdrawal permission) within the last hour")
        if trader.pos and trader.pos.positions:
            raise PermissionError("close all positions first")
        mode = d.get("mode", "SEMI_AUTOMATIC")
        if mode not in ("SEMI_AUTOMATIC", "AUTOMATIC"):
            raise ValueError("mode must be SEMI_AUTOMATIC or AUTOMATIC")
        new = trader.cfg.copy(deep=True)
        new.execution.exchange_env = ExchangeEnv.LIVE
        new.system.trading_mode = TradingMode(mode)
        trader._apply(new)
        db.kv_set("config", dump(new))
        db.save_config_version(dump(new), request.state.user, "LIVE enabled")
        audit(request, "live_enable", {"mode": mode})
        await trader.restart_venue()
        trader._alert("system", "critical", f"LIVE trading enabled ({mode}) by {request.state.user}")
        return ok()

    # ------------------------------------------------------------------ alerts / logs
    @handler
    async def alerts(request):
        return ok(db.alerts(min(int(request.query_params.get("limit", 200)), 1000), int(request.query_params.get("after", 0))))

    @handler
    async def alerts_read(request):
        db.execute("UPDATE alerts SET is_read = 1")
        return ok()

    @handler
    async def logs(request):
        lvl = request.query_params.get("level")
        rows = memory_handler.records[-1000:]
        if lvl:
            rows = [r for r in rows if r["level"] == lvl]
        return ok({"logs": rows[::-1][:500], "audit": db.audit_log(200),
                   "orders": db.query("SELECT * FROM order_events ORDER BY id DESC LIMIT 200")})

    # ------------------------------------------------------------------ websocket
    async def ws_endpoint(ws: WebSocket):
        tok = ws.query_params.get("token", "")
        if not auth.check(tok):
            await ws.close(code=4401)
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        trader.listeners.append(q)
        try:
            await ws.send_text(json.dumps(_clean({"type": "status", "data": trader.status()}), default=str))
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=10)
                except asyncio.TimeoutError:
                    msg = {"type": "status", "data": trader.status()}
                await ws.send_text(json.dumps(_clean(msg), default=str, allow_nan=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if q in trader.listeners:
                trader.listeners.remove(q)

    # ------------------------------------------------------------------ static
    async def index(request):
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def sw(request):
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    async def manifest(request):
        return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")

    @asynccontextmanager
    async def lifespan(app):
        if autostart:
            try:
                await trader.start()
            except Exception as e:
                log.error("trader failed to start: %s", e)
                trader.running = False
                trader._alert("exchange_failure", "critical", f"runtime failed to start: {e}")
        yield
        await trader.stop()

    R = Route
    routes = [
        R("/", index), R("/sw.js", sw), R("/manifest.json", manifest),
        R("/api/health", health), R("/api/auth/state", auth_state), R("/api/auth/setup", auth_setup, methods=["POST"]),
        R("/api/auth/login", auth_login, methods=["POST"]), R("/api/auth/logout", auth_logout, methods=["POST"]),
        R("/api/status", status), R("/api/markets", markets), R("/api/regime", regime),
        R("/api/config", get_config), R("/api/config", patch_config, methods=["PATCH"]),
        R("/api/config/rollback", rollback_config, methods=["POST"]),
        R("/api/engines", engines), R("/api/engines/{name}", engine_detail),
        R("/api/engines/{name}/toggle", engine_toggle, methods=["POST"]),
        R("/api/positions", positions), R("/api/positions/close_all", close_all, methods=["POST"]),
        R("/api/positions/{pid}/close", close_position, methods=["POST"]),
        R("/api/trades", trades), R("/api/decisions", decisions),
        R("/api/decisions/{did}/approve", decision_approve, methods=["POST"]),
        R("/api/decisions/{did}/reject", decision_reject, methods=["POST"]),
        R("/api/risk", risk), R("/api/risk/reset", reset_breaker, methods=["POST"]),
        R("/api/control/{action}", control, methods=["POST"]),
        R("/api/agents", agents), R("/api/performance", performance),
        R("/api/paper", paper), R("/api/paper/reset", paper_reset, methods=["POST"]),
        R("/api/research/run", research_run, methods=["POST"]), R("/api/research/jobs", research_jobs),
        R("/api/research/jobs/{jid}", research_job),
        R("/api/keys", keys), R("/api/keys", keys_set, methods=["POST"]), R("/api/keys/test", keys_test, methods=["POST"]),
        R("/api/keys/{env}", keys_delete, methods=["DELETE"]), R("/api/live/enable", live_enable, methods=["POST"]),
        R("/api/alerts", alerts), R("/api/alerts/read", alerts_read, methods=["POST"]), R("/api/logs", logs),
        WebSocketRoute("/ws", ws_endpoint),
    ]
    if (WEB_DIR / "static").exists():
        routes.append(Mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static"))
    return Starlette(routes=routes, middleware=[Middleware(SecurityMiddleware, auth=auth)],
                     lifespan=lifespan)
