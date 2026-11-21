from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M", "%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y")


def parse_dt(v, fmt: str | None = None) -> datetime | None:
    """Parse common broker timestamp formats; naive values are interpreted as UTC."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    for f in ((fmt,) if fmt else ()) + _FORMATS:
        try:
            d = datetime.strptime(s, f)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def stable_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
