from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import engine as db_engine
from ..db import get_session, init_db, session_scope
from ..events import EVENTS, get_bus
from ..security import authenticate, redeem_pairing_code
from ..services import state as st
from ..services.http import RateLimiter
from .routes import router

log = logging.getLogger("titan.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from ..discovery.service import seed_watchlist
    from ..services.venues import ensure_default_portfolios
    with session_scope() as s:
        ensure_default_portfolios(s)
        seed_watchlist(s)
    yield


app = FastAPI(title="TITAN COPY AI", version=__version__, lifespan=lifespan,
              description="Control-plane API for the TITAN copy-trading backend. All trading runs in the engine "
                          "process; the API never places orders itself.")
cfg = get_settings()
if cfg.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"], allow_headers=["*"])

_pair_limits: dict[str, RateLimiter] = {}   # per-client brute-force protection on pairing


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/api/health")
def health():
    out = {"service": "titan", "version": __version__, "time": datetime.now(timezone.utc).isoformat()}
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
    return JSONResponse(out, status_code=200 if ok else 503)


@app.post("/api/pair")
def pair(request: Request, code: str = Body(embed=True), device_name: str = Body("android", embed=True),
         s: Session = Depends(get_session)):
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


@app.websocket("/api/ws")
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


app.include_router(router)
