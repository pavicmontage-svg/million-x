"""Execution adapter contract shared by Forex and Binance adapters.

Each market has its own adapter and contract maths (markets/forex.py vs markets/crypto.py).
The copy engine only talks to this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import OrderStatus


@dataclass
class VenuePosition:
    ref: str                  # venue position id (cTrader positionId, MT5 ticket, binance symbol, paper id)
    symbol: str
    side: str
    quantity: float           # lots (forex) | base qty or contracts (crypto)
    entry_price: float
    sl: float | None = None
    tp: float | None = None
    unrealized: float = 0.0   # account ccy
    liquidation_price: float | None = None
    leverage: float | None = None
    client_id: str | None = None
    protection: dict = field(default_factory=dict)   # persisted protective order ids


@dataclass
class AccountSnapshot:
    currency: str
    balance: float
    equity: float
    margin_used: float = 0.0
    free_margin: float | None = None
    positions: list[VenuePosition] = field(default_factory=list)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict = field(default_factory=dict)


@dataclass
class OpenRequest:
    client_id: str
    symbol: str
    side: str
    quantity: float
    sl: float
    tp: float | None = None
    expected_price: float | None = None
    leverage: float | None = None


@dataclass
class OrderResult:
    client_id: str
    status: OrderStatus
    venue_order_id: str | None = None
    position_ref: str | None = None
    filled_qty: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0
    fee_asset: str | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)
    protection: dict = field(default_factory=dict)   # sl/tp order ids or errors

    @property
    def ok(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.ACKED)


class AdapterError(RuntimeError):
    """Definitive rejection by the venue (safe: nothing was executed)."""


class AdapterUnavailable(RuntimeError):
    """Venue unreachable or outcome unknown (must reconcile)."""


class ExecutionAdapter(ABC):
    market: str = ""            # forex | crypto
    venue: str = ""
    mode: str = "paper"         # paper | demo | live
    is_simulated: bool = False
    account_currency: str = "USD"
    multiple_positions_per_symbol: bool = True

    @abstractmethod
    def account(self) -> AccountSnapshot: ...

    @abstractmethod
    def quote(self, symbol: str) -> tuple[float, float]:
        """(bid, ask)"""

    def mids(self) -> dict[str, float]:
        """Mid prices for currency conversion (forex adapters)."""
        return {}

    @abstractmethod
    def open_position(self, req: OpenRequest) -> OrderResult:
        """Market entry + protective stop-loss (+ optional take-profit).
        Must guarantee: if the stop-loss cannot be placed, the position is closed immediately."""

    @abstractmethod
    def modify_protection(self, pos: VenuePosition, sl: float | None, tp: float | None, client_id: str) -> OrderResult: ...

    @abstractmethod
    def close_position(self, pos: VenuePosition, quantity: float, client_id: str) -> OrderResult: ...

    @abstractmethod
    def get_order(self, client_id: str, symbol: str) -> OrderResult | None:
        """Look up an order by client id (reconciliation of UNKNOWN orders)."""

    @abstractmethod
    def positions(self) -> list[VenuePosition]: ...

    def health(self) -> dict:
        try:
            a = self.account()
            return {"ok": True, "equity": a.equity, "venue": self.venue, "mode": self.mode}
        except Exception as e:
            return {"ok": False, "error": str(e), "venue": self.venue, "mode": self.mode}

    def size(self, symbol: str, side: str, entry: float, sl: float, risk_amount: float):
        """Return (quantity, actual_risk, notional, margin, currency_legs, reason)."""
        raise NotImplementedError
