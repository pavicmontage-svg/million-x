"""Global switches and tighten-only risk overlays persisted in system_state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import SystemState
from ..risk.engine import RiskOverlay

KILL = "global_kill_switch"
OVERLAY = "risk_overlay"
ENGINE = "copy_engine"
LIVE_APPROVALS = "live_approvals"


def get(s: Session, key: str, default=None) -> dict:
    row = s.get(SystemState, key)
    return dict(row.value) if row else (default if default is not None else {})


def put(s: Session, key: str, value: dict) -> None:
    row = s.get(SystemState, key)
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        s.add(SystemState(key=key, value=value))
    s.flush()


def kill_switch_active(s: Session) -> bool:
    return bool(get(s, KILL).get("active"))


def add_block(s: Session, kind: str, key: str, reason: str, minutes: float, by: str) -> None:
    """kind: currency | symbol | trader. Blocks expire automatically."""
    ov = get(s, OVERLAY, {"blocks": []})
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    ov.setdefault("blocks", []).append({"kind": kind, "key": str(key), "reason": reason, "until": until, "by": by})
    put(s, OVERLAY, ov)


def set_multiplier(s: Session, value: float, reason: str, minutes: float, by: str) -> None:
    value = max(0.0, min(1.0, float(value)))  # tighten-only
    ov = get(s, OVERLAY, {"blocks": []})
    ov["multiplier"] = {"value": value, "reason": reason, "by": by,
                        "until": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()}
    put(s, OVERLAY, ov)


def current_overlay(s: Session, now: datetime | None = None) -> RiskOverlay:
    now = now or datetime.now(timezone.utc)
    ov = get(s, OVERLAY, {"blocks": []})
    o = RiskOverlay()
    live = []
    for b in ov.get("blocks", []):
        if datetime.fromisoformat(b["until"]) <= now:
            continue
        live.append(b)
        if b["kind"] == "currency":
            o.blocked_currencies[b["key"]] = b["reason"]
        elif b["kind"] == "symbol":
            o.blocked_symbols[b["key"]] = b["reason"]
        elif b["kind"] == "trader":
            o.blocked_traders[int(b["key"])] = b["reason"]
    m = ov.get("multiplier")
    if m and datetime.fromisoformat(m["until"]) > now:
        o.risk_multiplier = max(0.0, min(1.0, m["value"]))
        o.reason = m["reason"]
    if len(live) != len(ov.get("blocks", [])):
        ov["blocks"] = live
        put(s, OVERLAY, ov)
    return o
