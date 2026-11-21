"""Append-only, hash-chained audit log.

Each row stores sha256(prev_hash | ts | actor | action | entity | canonical(detail)).
`verify_chain` recomputes the chain and reports the first broken link, so any
UPDATE/DELETE on historical rows is detectable.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import AuditLog

GENESIS = "0" * 64


def canonical(x):
    """JSON-safe canonical form that survives a PostgreSQL JSONB round trip
    (JSONB numeric has no -0.0, NaN or Infinity)."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return 0.0 if x == 0 else x
    if isinstance(x, dict):
        return {str(k): canonical(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [canonical(v) for v in x]
    return x


def _digest(prev: str, ts: datetime, actor: str, action: str, entity: str | None, detail: dict) -> str:
    canon = json.dumps(canonical(detail), sort_keys=True, separators=(",", ":"), default=str)
    raw = "|".join([prev, ts.astimezone(timezone.utc).isoformat(), actor, action, entity or "", canon])
    return hashlib.sha256(raw.encode()).hexdigest()


def audit(s: Session, actor: str, action: str, entity: str | None = None, **detail) -> AuditLog:
    # Serialise writers so the chain stays linear (advisory lock on Postgres).
    if s.bind is not None and s.bind.dialect.name == "postgresql":
        s.execute(text("SELECT pg_advisory_xact_lock(7240001)"))
    last = s.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    prev = last.hash if last else GENESIS
    ts = datetime.now(timezone.utc)
    detail = canonical(json.loads(json.dumps(canonical(detail), default=str)))
    row = AuditLog(ts=ts, actor=actor, action=action, entity=entity, detail=detail, prev_hash=prev,
                   hash=_digest(prev, ts, actor, action, entity, detail))
    s.add(row)
    s.flush()
    return row


def verify_chain(s: Session) -> dict:
    prev = GENESIS
    n = 0
    for row in s.scalars(select(AuditLog).order_by(AuditLog.id)):
        ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
        if row.prev_hash != prev or _digest(prev, ts, row.actor, row.action, row.entity, row.detail) != row.hash:
            return {"valid": False, "broken_at_id": row.id, "checked": n}
        prev = row.hash
        n += 1
    return {"valid": True, "checked": n, "head": prev}
