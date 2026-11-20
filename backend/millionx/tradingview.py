"""TradingView webhook intake (secure, never bypasses the risk engine).

TradingView alerts can only POST a body (no custom headers, no signing), so the
alert message must be JSON containing:
  {"secret": "<owner secret>", "id": "{{strategy.order.id}}-{{timenow}}", "symbol": "{{ticker}}",
   "side": "LONG|SHORT", "timeframe": "{{interval}}", "time": "{{timenow}}",
   "price": {{close}}, "stop": <price>, "take_profit": <price>}
Checks: constant-time secret comparison (or an optional HMAC-SHA256 header
X-MX-Signature for other senders), timestamp within +-60 s (replay window),
dedup by id (stored), instrument must be in the engine universe, timeframe on the
allow-list, stop/target on the correct side. Every message is logged in tv_signals
with its status. Accepted signals enter engine.submit_external(): shadow book ->
(AI) supervisor -> deterministic risk engine -> execution.

The engine must be reachable over HTTPS from TradingView: this works on a VPS,
not on the phone (127.0.0.1).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from .models import Side, Signal, now_ms

ALLOWED_TF = {"1", "3", "5", "15", "30", "60", "240", "1m", "3m", "5m", "15m", "30m", "1h", "4h"}
WINDOW_MS = 60_000


def _ts(v: Any) -> int:
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        x = int(float(v))
        return x if x > 10 ** 12 else x * 1000
    return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)


def secret(store, rotate: bool = False) -> str:
    s = store.kv_get("tv_secret")
    if not s or rotate:
        s = secrets.token_urlsafe(24)
        store.kv_set("tv_secret", s)
    return s


def handle(store, engine, raw: bytes, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    now = now_ms()
    try:
        body = json.loads(raw.decode() or "{}")
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return 400, {"error": "body must be a JSON object"}
    expected = store.kv_get("tv_secret")
    sig_hdr = headers.get("x-mx-signature", "")
    authed = False
    if expected and sig_hdr:
        mac = hmac.new(expected.encode(), raw, hashlib.sha256).hexdigest()
        authed = hmac.compare_digest(mac, sig_hdr)
    elif expected and isinstance(body.get("secret"), str):
        authed = hmac.compare_digest(body["secret"], expected)
    sid = str(body.get("id", ""))[:80]
    sym = str(body.get("symbol", "")).upper().replace(".P", "").replace("PERP", "")
    side = str(body.get("side", "")).upper()
    tf = str(body.get("timeframe", ""))
    safe = {k: v for k, v in body.items() if k != "secret"}

    def log(status: str, reason: str, code: int) -> tuple[int, dict[str, Any]]:
        if sid:
            store.x("INSERT OR IGNORE INTO tv_signals(id,ts,received_at,symbol,side,timeframe,payload,status,reason) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", (sid, 0, now, sym, side, tf, json.dumps(safe)[:4000], status, reason))
        store.audit("tradingview", "tv_signal", {"id": sid, "symbol": sym, "side": side, "status": status,
                                                  "reason": reason})
        return code, {"status": status, "reason": reason}

    if not authed:
        store.audit("tradingview", "tv_signal_rejected", {"reason": "authentication failed", "symbol": sym})
        return 401, {"error": "unauthorized"}
    if not sid:
        return 400, {"error": "missing id (dedup key)"}
    if store.one("SELECT 1 FROM tv_signals WHERE id=?", (sid,)):
        return 409, {"status": "duplicate", "reason": "signal id already received"}
    try:
        ts = _ts(body.get("time"))
    except (ValueError, TypeError):
        return log("rejected", "invalid time", 400)
    if abs(now - ts) > WINDOW_MS:
        return log("rejected", f"timestamp outside +-{WINDOW_MS // 1000} s (replay protection)", 400)
    if tf not in ALLOWED_TF:
        return log("rejected", f"timeframe {tf!r} not allowed", 400)
    if engine is None or sym not in engine.universe:
        return log("rejected", f"{sym} is not in the traded universe", 400)
    if side not in ("LONG", "SHORT"):
        return log("rejected", "side must be LONG or SHORT", 400)
    try:
        price, stop, tp = float(body["price"]), float(body["stop"]), float(body["take_profit"])
    except (KeyError, TypeError, ValueError):
        return log("rejected", "price, stop and take_profit are required numbers", 400)
    s = Side.LONG if side == "LONG" else Side.SHORT
    if (s == Side.LONG and not stop < price < tp) or (s == Side.SHORT and not tp < price < stop):
        return log("rejected", "stop/target on the wrong side of the price", 400)
    sig = Signal(bot_id="tradingview", symbol=sym, side=s, entry=price, stop=stop, tp1=price + (tp - price) * 0.5,
                 tp2=tp, confidence=float(body.get("confidence", 0.6) or 0.6), bar_time=ts, timeframe=5,
                 reason=f"TradingView alert {sid}", features={"valid_until": ts + 5 * 60_000})
    log("accepted", "queued for supervisor + risk engine", 202)
    r = engine.submit_external(sig, origin="tradingview")
    store.x("UPDATE tv_signals SET status=?, reason=? WHERE id=?",
            ("submitted" if r.get("ok") else "not_submitted", r.get("error", "") or sig.id, sid))
    return 202, {"status": "submitted" if r.get("ok") else "not_submitted", "signal_id": sig.id,
                 "error": r.get("error")}
