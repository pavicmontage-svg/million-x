"""Authorised signal ingestion.

Signals enter from (a) HMAC-signed webhooks of providers the owner authorised, (b) owner-controlled
source accounts (cTrader execution events, MT5 deals, Binance user data), or (c) the simulator.
Every signal gets a deterministic idempotency key; the UNIQUE constraint on
signals.idempotency_key makes duplicate deliveries harmless.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .._util import parse_dt
from ..audit import audit
from ..events import SIGNALS, get_bus
from ..models import Market, Signal, SignalKind, SignalStatus, Trader


class SignalError(ValueError):
    """Invalid signal payload (HTTP 422)."""


_FIELDS = {"trader_id", "kind", "source_position_id", "symbol", "side", "entry_price", "sl", "tp", "close_fraction",
           "source_volume", "source_risk_pct", "source_ts", "signal_id", "is_simulated"}


@dataclass
class SignalIn:
    trader_id: int
    kind: SignalKind
    source_position_id: str
    symbol: str
    source_ts: datetime
    side: str | None = None
    entry_price: float | None = None
    sl: float | None = None
    tp: float | None = None
    close_fraction: float | None = None
    source_volume: float | None = None
    source_risk_pct: float | None = None
    signal_id: str | None = None          # provider's own event id, if any
    is_simulated: bool = False

    def __post_init__(self):
        try:
            self.trader_id = int(self.trader_id)
            self.kind = SignalKind(self.kind)
        except (TypeError, ValueError) as e:
            raise SignalError(f"invalid trader_id/kind: {e}") from e
        self.source_position_id = str(self.source_position_id or "")
        if not 1 <= len(self.source_position_id) <= 120:
            raise SignalError("source_position_id must be 1-120 characters")
        self.symbol = str(self.symbol or "")
        if not 3 <= len(self.symbol) <= 30:
            raise SignalError("symbol must be 3-30 characters")
        if self.side is not None:
            self.side = str(self.side).lower()
            if self.side not in ("buy", "sell"):
                raise SignalError("side must be buy or sell")
        for name, lo, hi, incl_lo in (("entry_price", 0, None, False), ("sl", 0, None, False), ("tp", 0, None, False),
                                      ("close_fraction", 0, 1, False), ("source_volume", 0, None, False),
                                      ("source_risk_pct", 0, None, True)):
            v = getattr(self, name)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError) as e:
                raise SignalError(f"{name} must be a number") from e
            if (v < lo if incl_lo else v <= lo) or (hi is not None and v > hi):
                raise SignalError(f"{name} out of range")
            setattr(self, name, v)
        if isinstance(self.source_ts, str):
            ts = parse_dt(self.source_ts)
            if ts is None:
                raise SignalError("source_ts is not a valid timestamp")
            self.source_ts = ts
        elif isinstance(self.source_ts, (int, float)):
            self.source_ts = parse_dt(self.source_ts)
        if not isinstance(self.source_ts, datetime):
            raise SignalError("source_ts is required")
        if self.source_ts.tzinfo is None:
            self.source_ts = self.source_ts.replace(tzinfo=timezone.utc)
        if self.signal_id is not None and len(str(self.signal_id)) > 120:
            raise SignalError("signal_id too long")
        if self.kind == SignalKind.OPEN and (self.side is None or self.entry_price is None):
            raise SignalError("open signals need side and entry_price")
        if self.kind == SignalKind.PARTIAL_CLOSE and self.close_fraction is None:
            raise SignalError("partial_close needs close_fraction")
        if self.kind == SignalKind.MODIFY and self.sl is None and self.tp is None:
            raise SignalError("modify needs sl and/or tp")

    @classmethod
    def model_validate(cls, data: dict) -> "SignalIn":
        """Build from an untrusted dict (unknown keys such as bid/ask are ignored)."""
        missing = [k for k in ("trader_id", "kind", "source_position_id", "symbol", "source_ts") if data.get(k) in (None, "")]
        if missing:
            raise SignalError(f"missing fields: {missing}")
        return cls(**{k: v for k, v in data.items() if k in _FIELDS})

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
