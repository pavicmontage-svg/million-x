from __future__ import annotations

from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Device
from ..security import authenticate
from .web import Depends, HTTPException, Request

COMMANDS = "titan:commands"


def device(request: Request, s: Session = Depends(get_session)) -> Device:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    dev = authenticate(s, token)
    if dev is None:
        raise HTTPException(401, "invalid or missing device token")
    return dev


def fmt(v):
    """Present None as the literal UNKNOWN for metrics."""
    return "UNKNOWN" if v is None else v
