from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Device
from ..security import authenticate

COMMANDS = "titan:commands"


def device(authorization: str = Header(default=""), s: Session = Depends(get_session)) -> Device:
    token = authorization.removeprefix("Bearer ").strip()
    dev = authenticate(s, token)
    if dev is None:
        raise HTTPException(401, "invalid or missing device token")
    return dev


def ws_device(token: str = Query(default=""), s: Session = Depends(get_session)) -> Device | None:
    return authenticate(s, token)


def fmt(v):
    """Present None as the literal UNKNOWN for metrics."""
    return "UNKNOWN" if v is None else v
