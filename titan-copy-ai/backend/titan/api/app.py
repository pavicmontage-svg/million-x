"""Starlette application (runs on a server and embedded in the Android app)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import __version__
from ..config import get_settings
from ..db import engine as db_engine
from ..db import get_session, init_db, session_scope
from ..events import EVENTS, get_bus
from ..security import authenticate, redeem_pairing_code
from ..services import state as st
from ..services.http import RateLimiter
from .routes import router
from .web import APIRouter, Body, Depends, HTTPException, Request, json_response

log = logging.getLogger("titan.api")
core = APIRouter(prefix="/api")
_pair_limits: dict[str, RateLimiter] = {}   # per-client brute-force protection on pairing


@core.get("/health")
def health():
    """Service health (database, event bus, engine heartbeat)"""
    out = {"service": "titan", "version": __version__, "time": datetime.now(timezone.utc).isoformat(),
           "embedded": get_settings().embedded}
    try:
        with db_engine().connect() as c:
            c.execute(text("SELECT 1"))
        out["database"] = "ok"
    except Exception as e:
        out["database"] = f"error: {e}"
    try:
        out["redis"] = "ok" if get_bus().ping() else "error"
    except Exception as e:
        out["redis"] = f"error: {e}"
    with session_scope() as s:
        eng = st.get(s, st.ENGINE)
    hb = eng.get("heartbeat")
    out["engine"] = "ok" if hb and (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds() < 90 else "down"
    ok = out["database"] == "ok" and out["redis"] == "ok"
    return json_response(out, 200 if ok else 503)


@core.post("/pair")
def pair(request: Request, code: str = Body(embed=True), device_name: str = Body("android", embed=True),
         s: Session = Depends(get_session)):
    """Exchange a one-time pairing code for a device token"""
    ip = request.client.host if request.client else "?"
    lim = _pair_limits.setdefault(ip, RateLimiter(1 / 60, 5))   # 5 attempts, then 1 per minute
    if not lim.try_acquire():
        raise HTTPException(429, "too many attempts")
    token = redeem_pairing_code(s, code, device_name)
    if token is None:
        raise HTTPException(401, "invalid or expired pairing code")
    from ..audit import audit
    audit(s, "system", "device.paired", None, device_name=device_name)
    return {"token": token}


async def ws(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    with session_scope() as s:
        dev = authenticate(s, token)
    if dev is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    bus = get_bus()
    last = "$"
    try:
        while True:
            items = await asyncio.to_thread(bus.tail, EVENTS, last, 15000)
            for mid, ev in items:
                last = mid
                await websocket.send_json(ev)
            if not items:
                await websocket.send_json({"event": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        return


@asynccontextmanager
async def lifespan(app):
    init_db()
    from ..discovery.service import seed_watchlist
    from ..services.venues import ensure_default_portfolios
    with session_scope() as s:
        ensure_default_portfolios(s)
        seed_watchlist(s)
    yield


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
                            (b"referrer-policy", b"no-referrer")]
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, send_wrapper)


def build_app() -> Starlette:
    middleware = [Middleware(SecurityHeaders)]
    if get_settings().cors_origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"],
                                     allow_headers=["*"]))
    routes = core.routes() + router.routes() + [WebSocketRoute("/api/ws", ws)]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


app = build_app()
