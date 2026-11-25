"""Order manager: risk-approval enforcement, idempotency, duplicate prevention, retries, timeouts.

Rules
  * Exposure-increasing orders require a valid, unused Risk Manager approval (``ApprovalSigner.verify``).
    Reduce-only / close-position orders do not (blocking them could only increase risk).
  * Client order ids are deterministic per decision and leg, so a retry cannot create a second order:
    after an ambiguous failure the order is *queried* first and only re-sent if it provably does not exist.
  * A submitted order is never assumed filled: market orders are polled until a terminal status.
  * Concurrent submissions of the same client id are serialised and deduplicated.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from ..core.logging import get_logger
from ..risk.approval import ApprovalError, ApprovalSigner
from .exchange import Exchange, ExchangeError, OrderInfo, OrderRequest

log = get_logger("orders")


class OrderManager:
    def __init__(self, exchange: Exchange, signer: ApprovalSigner, *, max_retries: int = 3, poll_interval: float = 0.25,
                 fill_timeout: float = 10.0, on_event=None) -> None:
        self.ex = exchange
        self.signer = signer
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.fill_timeout = fill_timeout
        self.orders: dict[str, OrderInfo] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.blocked_reason: str | None = None
        self.on_event = on_event
        self.log: list[dict] = []

    def block(self, reason: str) -> None:
        self.blocked_reason = reason

    def unblock(self) -> None:
        self.blocked_reason = None

    def _record(self, event: str, **kw) -> None:
        row = {"event": event, **kw}
        self.log.append(row)
        self.log = self.log[-1000:]
        if self.on_event:
            self.on_event(row)

    async def submit(self, req: OrderRequest, *, approval: dict | None = None, ref_price: float = 0.0,
                     wait_fill: bool = True) -> OrderInfo:
        risk_increasing = not (req.reduce_only or req.close_position)
        if risk_increasing:
            if self.blocked_reason:
                raise ExchangeError(f"new orders blocked: {self.blocked_reason}")
            side_px = ref_price or req.price
            self.signer.verify(approval, symbol=req.symbol, side=req.side, qty=req.qty, price=side_px)
        async with self._locks[req.client_id]:
            existing = self.orders.get(req.client_id)
            if existing is not None:
                self._record("duplicate_suppressed", client_id=req.client_id, status=existing.status)
                return existing
            info = await self._send_with_recovery(req)
            self.orders[req.client_id] = info
            self._record("submitted", client_id=req.client_id, symbol=req.symbol, side=req.side, type=req.type,
                         qty=req.qty, status=info.status)
            if wait_fill and (req.type == "MARKET" or (req.type == "LIMIT" and req.tif == "IOC")):
                info = await self.await_terminal(req.symbol, req.client_id, conditional=False)
                self.orders[req.client_id] = info
            return info

    async def _send_with_recovery(self, req: OrderRequest) -> OrderInfo:
        delay = 0.2
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self.ex.place_order(req)
            except ExchangeError as e:
                last = e
                if e.code == -4116:  # duplicate client id: it already exists — fetch it
                    found = await self._safe_query(req)
                    if found:
                        return found
                if not (e.retriable or e.unknown_state):
                    self._record("rejected", client_id=req.client_id, error=str(e))
                    raise
                if e.unknown_state:
                    found = await self._safe_query(req)
                    if found is not None:
                        self._record("recovered_after_ambiguous_failure", client_id=req.client_id, status=found.status)
                        return found
                self._record("retry", client_id=req.client_id, attempt=attempt + 1, error=str(e))
                await asyncio.sleep(delay)
                delay *= 2
        raise ExchangeError(f"order {req.client_id} failed after retries: {last}", unknown_state=True)

    async def _safe_query(self, req: OrderRequest) -> OrderInfo | None:
        for _ in range(3):
            try:
                return await self.ex.get_order(req.symbol, req.client_id, conditional=req.conditional)
            except ExchangeError as e:
                if not e.retriable:
                    return None
                await asyncio.sleep(0.2)
        raise ExchangeError(f"cannot determine state of order {req.client_id}", unknown_state=True)

    async def await_terminal(self, symbol: str, client_id: str, *, conditional: bool = False) -> OrderInfo:
        waited = 0.0
        info = self.orders.get(client_id)
        while True:
            try:
                got = await self.ex.get_order(symbol, client_id, conditional=conditional)
                if got is not None:
                    info = got
            except ExchangeError as e:
                if not e.retriable:
                    raise
            if info is not None and info.is_terminal:
                return info
            if waited >= self.fill_timeout:
                raise ExchangeError(f"order {client_id} not terminal after {self.fill_timeout}s (status "
                                    f"{info.status if info else 'unknown'})", unknown_state=True)
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval

    async def cancel(self, symbol: str, client_id: str, conditional: bool = False) -> OrderInfo | None:
        for attempt in range(self.max_retries + 1):
            try:
                info = await self.ex.cancel_order(symbol, client_id, conditional)
                self.orders[client_id] = info
                self._record("canceled", client_id=client_id)
                return info
            except ExchangeError as e:
                if e.code in (-2011, -2013):
                    return await self.ex.get_order(symbol, client_id, conditional)
                if not e.retriable:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        return None


__all__ = ["OrderManager", "ApprovalError"]
