"""Venue-neutral exchange interface. Implemented by the Binance USDT-M client and the paper exchange,
so the order/position managers and the reconciler are identical for paper, testnet and live."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "FINISHED"}


class ExchangeError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, retriable: bool = False, unknown_state: bool = False) -> None:
        super().__init__(msg)
        self.code = code
        self.retriable = retriable
        self.unknown_state = unknown_state  # request may or may not have reached the matching engine


@dataclass
class OrderRequest:
    symbol: str
    side: str  # BUY | SELL
    type: str  # MARKET | LIMIT | STOP_MARKET | TAKE_PROFIT_MARKET
    qty: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    reduce_only: bool = False
    close_position: bool = False
    tif: str = "GTC"  # GTC | IOC
    client_id: str = ""
    working_type: str = "MARK_PRICE"

    @property
    def conditional(self) -> bool:
        return self.type in ("STOP_MARKET", "TAKE_PROFIT_MARKET")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderInfo:
    client_id: str
    symbol: str
    side: str
    type: str
    status: str
    qty: float = 0.0
    filled_qty: float = 0.0
    avg_price: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    reduce_only: bool = False
    close_position: bool = False
    exchange_id: str = ""
    fee: float = 0.0
    update_ms: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class ExchangePosition:
    symbol: str
    qty: float  # signed: + long, - short
    entry_price: float
    mark_price: float = 0.0
    leverage: float = 1.0
    liquidation_price: float = 0.0
    unrealized: float = 0.0
    margin_type: str = "isolated"


@dataclass
class Balance:
    wallet: float
    available: float
    unrealized: float = 0.0

    @property
    def equity(self) -> float:
        return self.wallet + self.unrealized


class Exchange(ABC):
    name = "abstract"
    env = "paper"

    @abstractmethod
    async def server_time(self) -> int: ...

    @abstractmethod
    async def balance(self) -> Balance: ...

    @abstractmethod
    async def positions(self) -> list[ExchangePosition]: ...

    @abstractmethod
    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderInfo: ...

    @abstractmethod
    async def cancel_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo: ...

    @abstractmethod
    async def get_order(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo | None: ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    async def set_margin_type(self, symbol: str, margin_type: str) -> None: ...

    async def cancel_all(self, symbol: str) -> None:
        for o in await self.open_orders(symbol):
            try:
                await self.cancel_order(symbol, o.client_id, o.type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"))
            except ExchangeError:
                pass

    async def close(self) -> None:
        return None
