"""Exchange adapter interface. Binance Futures (demo/live) and the paper
broker implement it; another venue (e.g. a MiCA/MiFID-licensed EU exchange)
can be added by implementing the same methods."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import Bars, OrderBookSnapshot
from ..risk import SymbolRules


class ExchangeError(Exception):
    def __init__(self, msg: str, code: Optional[int] = None, retriable: bool = False):
        super().__init__(msg)
        self.code = code
        self.retriable = retriable


@dataclass
class OrderResult:
    client_id: str
    exchange_id: Optional[str]
    symbol: str
    side: str
    type: str
    status: str                 # NEW | FILLED | PARTIALLY_FILLED | CANCELED | REJECTED | EXPIRED | UNKNOWN
    qty: float
    filled_qty: float = 0.0
    avg_price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class Exchange(ABC):
    name = "abstract"
    is_paper = False

    # market data
    @abstractmethod
    def symbols(self) -> dict[str, SymbolRules]: ...
    @abstractmethod
    def tickers_24h(self) -> list[dict[str, Any]]: ...
    @abstractmethod
    def klines(self, symbol: str, limit: int = 500, end_time: Optional[int] = None) -> Bars: ...
    @abstractmethod
    def order_book(self, symbol: str, limit: int = 20) -> OrderBookSnapshot: ...
    def premium_index(self, symbol: str) -> dict[str, Any]:
        return {}
    def open_interest(self, symbol: str) -> dict[str, Any]:
        return {}
    def server_time(self) -> int:
        from ..models import now_ms
        return now_ms()

    # account
    @abstractmethod
    def balance(self) -> dict[str, float]: ...
    @abstractmethod
    def position_amount(self, symbol: str) -> float: ...
    def commission(self, symbol: str) -> Optional[tuple[float, float]]:
        return None
    def api_permissions(self) -> dict[str, Any]:
        return {"checked": False}
    def set_leverage(self, symbol: str, leverage: int) -> None: ...
    def set_isolated(self, symbol: str) -> None: ...

    # orders
    @abstractmethod
    def market_order(self, symbol: str, side: str, qty: float, client_id: str, reduce_only: bool = False) -> OrderResult: ...
    @abstractmethod
    def limit_order(self, symbol: str, side: str, qty: float, price: float, client_id: str, post_only: bool = True) -> OrderResult: ...
    @abstractmethod
    def query_order(self, symbol: str, client_id: str) -> OrderResult: ...
    @abstractmethod
    def cancel_order(self, symbol: str, client_id: str) -> None: ...
    @abstractmethod
    def conditional_order(self, symbol: str, side: str, type_: str, trigger: float, client_id: str,
                          qty: Optional[float] = None, close_position: bool = False) -> OrderResult: ...
    @abstractmethod
    def cancel_conditional(self, symbol: str, client_id: str) -> None: ...
    @abstractmethod
    def cancel_all(self, symbol: str) -> None: ...
    @abstractmethod
    def realized_since(self, symbol: str, since_ms: int) -> dict[str, float]:
        """Sum of realized PnL, commission and funding for symbol since ts."""
