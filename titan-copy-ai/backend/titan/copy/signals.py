"""Authorised signal ingestion.

Signals enter from (a) HMAC-signed webhooks of providers the owner authorised, (b) owner-controlled
source accounts (cTrader execution events, MT5 deals, Binance user data), or (c) the simulator.
Every signal gets a deterministic idempotency key; the UNIQUE constraint on
signals.idempotency_key makes duplicate deliveries harmless.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..events import SIGNALS, get_bus
from ..models import Market, Signal, SignalKind, SignalStatus, Trader


class SignalIn(BaseModel):
    trader_id: int
    kind: SignalKind
    source_position_id: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=3, max_length=30)
    side: str | None = None
    entry_price: float | None = Field(default=None, gt=0)
    sl: float | None = Field(default=None, gt=0)
    tp: float | None = Field(default=None, gt=0)
    close_fraction: float | None = Field(default=None, gt=0, le=1)
    source_volume: float | None = Field(default=None, gt=0)
    source_risk_pct: float | None = Field(default=None, ge=0)
    source_ts: datetime
    signal_id: str | None = Field(default=None, max_length=120)   # provider's own event id, if any
    is_simulated: bool = False

    @field_validator("side")
    @classmethod
    def _side(cls, v):
        if v is None:
            return v
        v = v.lower()
        if v not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        return v

    @field_validator("source_ts")
    @classmethod
    def _tz(cls, v: datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == SignalKind.OPEN and (self.side is None or self.entry_price is None):
            raise ValueError("open signals need side and entry_price")
        if self.kind == SignalKind.PARTIAL_CLOSE and self.close_fraction is None:
            raise ValueError("partial_close needs close_fraction")
        if self.kind == SignalKind.MODIFY and self.sl is None and self.tp is None:
            raise ValueError("modify needs sl and/or tp")
        return self

    def idempotency_key(self, source: str) -> str:
        parts = [source, str(self.trader_id), self.source_position_id, self.kind.value,
                 self.signal_id or self.source_ts.isoformat(), str(self.sl), str(self.tp), str(self.close_fraction)]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


def ingest_signal(s: Session, sig: SignalIn, source: str, payload: dict | None = None) -> tuple[Signal, bool]:
    """Persist + publish. Returns (signal, created). Duplicate deliveries return created=False."""
    tr = s.get(Trader, sig.trader_id)
    if tr is None:
        raise ValueError(f"unknown trader {sig.trader_id}")
    key = sig.idempotency_key(source)
    existing = s.scalar(select(Signal).where(Signal.idempotency_key == key))
    if existing:
        return existing, False
    row = Signal(idempotency_key=key, source=source, trader_id=tr.id, market=tr.market, kind=sig.kind,
                 source_position_id=sig.source_position_id, symbol=sig.symbol.upper(), side=sig.side,
                 entry_price=sig.entry_price, sl=sig.sl, tp=sig.tp, close_fraction=sig.close_fraction,
                 source_volume=sig.source_volume, source_risk_pct=sig.source_risk_pct, source_ts=sig.source_ts,
                 payload=payload or {}, is_simulated=sig.is_simulated, status=SignalStatus.RECEIVED)
    try:
        with s.begin_nested():
            s.add(row)
            s.flush()
    except IntegrityError:
        return s.scalar(select(Signal).where(Signal.idempotency_key == key)), False
    audit(s, f"source:{source}", "signal.received", f"signal:{row.id}", kind=sig.kind.value, symbol=row.symbol,
          trader_id=tr.id)
    return row, True


def publish(signal_id: int) -> None:
    get_bus().publish(SIGNALS, {"signal_id": signal_id})


__all__ = ["SignalIn", "ingest_signal", "publish", "Market"]
