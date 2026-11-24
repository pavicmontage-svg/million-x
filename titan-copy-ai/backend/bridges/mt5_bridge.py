"""TITAN MT5 bridge - run on the Windows machine/VPS where your MetaTrader 5 terminal is logged in.

Uses only the official MetaQuotes package (pip install MetaTrader5 fastapi uvicorn), which talks to
the locally running terminal. Your MT5 password never leaves that machine and is never given to a
third-party copy service.

    set TITAN_BRIDGE_SECRET=<long random string, same value configured in TITAN as the credential>
    python mt5_bridge.py --host 0.0.0.0 --port 8765

Put it behind TLS (e.g. Caddy) or a WireGuard tunnel; requests are HMAC-signed and time-limited.
Endpoints: GET /health /account /positions /symbol/{s} /tick/{s} /order/{client_id} /deals
           POST /open /modify /close
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import time

import MetaTrader5 as mt5  # type: ignore
from fastapi import FastAPI, HTTPException, Request

_SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_secret.txt")
SECRET = os.environ.get("TITAN_BRIDGE_SECRET") or (open(_SECRET_FILE).read().strip() if os.path.exists(_SECRET_FILE) else "")
MAGIC = int(os.environ.get("TITAN_MAGIC", "7240001"))
app = FastAPI(title="TITAN MT5 bridge")


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path == "/pair":   # pairing page: only from this PC itself
        if (request.client.host if request.client else "") in LOCAL_HOSTS:
            return await call_next(request)
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "the pairing page is only available on this computer"}, status_code=403)
    if not SECRET:
        raise HTTPException(500, "TITAN_BRIDGE_SECRET not set")
    ts = request.headers.get("X-Titan-Ts", "0")
    sig = request.headers.get("X-Titan-Sig", "")
    body = await request.body()
    msg = f"{ts}.{request.method}.{request.url.path}.".encode() + body
    good = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
    if abs(time.time() * 1000 - float(ts)) > 30_000 or not hmac.compare_digest(good, sig):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "unauthorised"}, status_code=401)
    return await call_next(request)


def _need_init():
    if not mt5.initialize():
        raise HTTPException(503, f"MT5 initialize failed: {mt5.last_error()}")


_SYM_CACHE: dict[str, str] = {}


def _sym(name: str) -> str:
    """Broker symbol for a canonical name (EURUSD -> EURUSD, EURUSD.r, EURUSDm, EURUSD-ECN, GOLD for XAUUSD...)."""
    if name in _SYM_CACHE:
        return _SYM_CACHE[name]
    if mt5.symbol_info(name) is not None:
        _SYM_CACHE[name] = name
        return name
    base = name.upper()
    aliases = {"XAUUSD": ("XAUUSD", "GOLD")}.get(base, (base,))
    cands = [s.name for s in (mt5.symbols_get() or [])
             if any(s.name.upper().replace("/", "").startswith(a) and len(s.name) - len(a) <= 5 for a in aliases)]
    if not cands:
        raise HTTPException(404, f"symbol {name} not offered by this broker")
    best = min(cands, key=len)          # the plainest variant (e.g. EURUSD.r before EURUSD.r.raw)
    mt5.symbol_select(best, True)
    _SYM_CACHE[name] = best
    return best


def _pos(p):
    return {"ticket": p.ticket, "symbol": p.symbol, "side": "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
            "volume": p.volume, "price_open": p.price_open, "sl": p.sl or None, "tp": p.tp or None,
            "profit": p.profit, "comment": p.comment, "magic": p.magic, "time": p.time}


def _tailscale_ip() -> str | None:
    import shutil
    import subprocess
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        return out[0] if out else None
    except Exception:
        return None


def _lan_ip() -> str | None:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 80))   # no packet is sent; picks the outgoing interface
            ip = sk.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


def pair_payload(port: int) -> str:
    """QR content for the TITAN app: titan-mt5:<base64url(JSON)> with every address this PC can be reached on."""
    import base64
    import json
    urls = [f"http://{ip}:{port}" for ip in (_tailscale_ip(), _lan_ip()) if ip]
    data = {"v": 1, "urls": urls, "secret": SECRET, "name": os.environ.get("COMPUTERNAME", "MT5 PC")}
    return "titan-mt5:" + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


@app.get("/pair")
def pair(request: Request):
    from fastapi.responses import HTMLResponse
    payload = pair_payload(request.url.port or 8765)
    try:
        import io

        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2).save(buf)
        qr = buf.getvalue().decode()
    except Exception as e:  # qrcode missing: still show the values
        qr = f"<p>(QR not available: {e})</p>"
    try:
        ti = mt5.terminal_info() if mt5.initialize() else None
        mt5_ok = bool(ti and ti.connected)
    except Exception:
        mt5_ok = False
    urls = ", ".join(json_urls(payload))
    warn = "" if mt5_ok else "<p style='color:#FF5D6C'><b>MetaTrader 5 is not open / not logged in.</b></p>"
    return HTMLResponse(
        "<html><head><meta name='viewport' content='width=device-width'><title>TITAN MT5 pairing</title></head>"
        "<body style='background:#0A0E13;color:#E6EDF3;font-family:sans-serif;text-align:center;padding:24px'>"
        "<h2 style='color:#D9B25F'>TITAN Copy AI - MetaTrader 5</h2>"
        "<p>In the TITAN app: <b>Broker Connections &rarr; Scan QR from PC</b></p>"
        f"<div style='background:#fff;display:inline-block;padding:16px;border-radius:12px'>{qr}</div>"
        f"{warn}<p style='color:#8B98A9'>Addresses in the code: {urls or 'none found'}</p>"
        "<p style='color:#8B98A9'>Keep this computer on and MetaTrader 5 open while copying.</p></body></html>")


def json_urls(payload: str) -> list[str]:
    import base64
    import json
    raw = payload.split(":", 1)[1]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))["urls"]


@app.get("/health")
def health():
    _need_init()
    ti = mt5.terminal_info()
    return {"connected": bool(ti and ti.connected), "trade_allowed": bool(ti and ti.trade_allowed)}


@app.get("/account")
def account():
    _need_init()
    a = mt5.account_info()
    return {"currency": a.currency, "balance": a.balance, "equity": a.equity, "margin": a.margin,
            "free_margin": a.margin_free, "leverage": a.leverage, "server": a.server, "login": a.login,
            "trade_mode": a.trade_mode}


@app.get("/positions")
def positions():
    _need_init()
    return [_pos(p) for p in (mt5.positions_get() or [])]


@app.get("/symbol/{symbol}")
def symbol(symbol: str):
    _need_init()
    symbol = _sym(symbol)
    mt5.symbol_select(symbol, True)
    s = mt5.symbol_info(symbol)
    if s is None:
        raise HTTPException(404, "unknown symbol")
    return {"name": s.name, "digits": s.digits, "point": s.point, "contract_size": s.trade_contract_size,
            "volume_min": s.volume_min, "volume_step": s.volume_step, "volume_max": s.volume_max,
            "currency_profit": s.currency_profit, "currency_base": s.currency_base, "filling_mode": s.filling_mode}


@app.get("/tick/{symbol}")
def tick(symbol: str):
    _need_init()
    t = mt5.symbol_info_tick(_sym(symbol))
    if t is None:
        raise HTTPException(404, "no tick")
    return {"bid": t.bid, "ask": t.ask, "time_msc": t.time_msc}


def _filling(symbol):
    s = mt5.symbol_info(symbol)
    if s and s.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    if s and s.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


@app.post("/open")
async def open_(request: Request):
    _need_init()
    r = await request.json()
    r["symbol"] = _sym(r["symbol"])
    t = mt5.symbol_info_tick(r["symbol"])
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": r["symbol"], "volume": float(r["volume"]),
           "type": mt5.ORDER_TYPE_BUY if r["side"] == "buy" else mt5.ORDER_TYPE_SELL,
           "price": t.ask if r["side"] == "buy" else t.bid, "sl": float(r["sl"]), "tp": float(r.get("tp") or 0.0),
           "deviation": int(r.get("deviation", 20)), "magic": MAGIC, "comment": r["client_id"][:31],
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(r["symbol"])}
    res = mt5.order_send(req)
    if res is None:
        raise HTTPException(502, f"order_send failed: {mt5.last_error()}")
    return {"retcode": res.retcode, "done": res.retcode == mt5.TRADE_RETCODE_DONE, "deal": res.deal,
            "order": res.order, "volume": res.volume, "price": res.price, "comment": res.comment}


@app.post("/modify")
async def modify(request: Request):
    _need_init()
    r = await request.json()
    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": int(r["ticket"]), "symbol": _sym(r["symbol"]),
                          "sl": float(r["sl"] or 0), "tp": float(r.get("tp") or 0), "magic": MAGIC})
    return {"retcode": res.retcode, "done": res.retcode == mt5.TRADE_RETCODE_DONE, "comment": res.comment}


@app.post("/close")
async def close(request: Request):
    _need_init()
    r = await request.json()
    p = (mt5.positions_get(ticket=int(r["ticket"])) or [None])[0]
    if p is None:
        raise HTTPException(404, "position not found")
    t = mt5.symbol_info_tick(p.symbol)
    sell = p.type == mt5.POSITION_TYPE_BUY
    res = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol,
                          "volume": float(r["volume"]), "type": mt5.ORDER_TYPE_SELL if sell else mt5.ORDER_TYPE_BUY,
                          "price": t.bid if sell else t.ask, "deviation": 20, "magic": MAGIC,
                          "comment": r["client_id"][:31], "type_filling": _filling(p.symbol)})
    return {"retcode": res.retcode, "done": res.retcode == mt5.TRADE_RETCODE_DONE, "deal": res.deal,
            "volume": res.volume, "price": res.price, "comment": res.comment}


@app.get("/order/{client_id}")
def order(client_id: str):
    _need_init()
    frm = time.time() - 7 * 86400
    deals = mt5.history_deals_get(frm, time.time() + 60) or []
    hit = [d for d in deals if d.comment == client_id[:31]]
    if not hit:
        return {"found": False}
    d = hit[-1]
    return {"found": True, "deal": d.ticket, "position": d.position_id, "volume": d.volume, "price": d.price,
            "commission": d.commission, "profit": d.profit}


@app.get("/deals")
def deals(since: float):
    _need_init()
    out = []
    for d in mt5.history_deals_get(since, time.time() + 60) or []:
        out.append({"ticket": d.ticket, "position": d.position_id, "symbol": d.symbol, "type": d.type,
                    "entry": d.entry, "volume": d.volume, "price": d.price, "profit": d.profit,
                    "commission": d.commission, "swap": d.swap, "time_msc": d.time_msc, "comment": d.comment})
    return out


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port)
