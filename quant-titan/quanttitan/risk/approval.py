"""Signed risk approvals.

The execution layer refuses any exposure-increasing order that does not carry a valid approval
issued by the Risk Manager. Approvals are HMAC-SHA256 signed with a key only the Risk Manager holds,
bound to one decision, symbol, side, maximum quantity and price band, expire quickly, and are
single-use. This makes "never place an order without validated risk approval" a property enforced
in code, not a convention.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading

from ..core.types import now_ms


class ApprovalError(PermissionError):
    pass


class ApprovalSigner:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or os.urandom(32)
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def _sig(self, payload: dict) -> str:
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def issue(self, *, decision_id: str, symbol: str, side: str, max_qty: float, ref_price: float,
              max_deviation_pct: float, ttl_ms: int = 120_000, group_id: str = "") -> dict:
        payload = {
            "decision_id": decision_id, "symbol": symbol, "side": side, "max_qty": float(max_qty),
            "min_price": float(ref_price * (1 - max_deviation_pct / 100)),
            "max_price": float(ref_price * (1 + max_deviation_pct / 100)),
            "expires_ms": now_ms() + ttl_ms, "group_id": group_id,
        }
        return {**payload, "sig": self._sig(payload)}

    def verify(self, approval: dict | None, *, symbol: str, side: str, qty: float, price: float,
               consume: bool = True) -> None:
        if not approval:
            raise ApprovalError("no risk approval attached to an exposure-increasing order")
        payload = {k: v for k, v in approval.items() if k != "sig"}
        if not hmac.compare_digest(self._sig(payload), str(approval.get("sig", ""))):
            raise ApprovalError("risk approval signature invalid")
        if now_ms() > int(approval["expires_ms"]):
            raise ApprovalError("risk approval expired")
        if approval["symbol"] != symbol or approval["side"] != side:
            raise ApprovalError("order does not match the approved symbol/side")
        if qty > approval["max_qty"] * (1 + 1e-9):
            raise ApprovalError(f"order qty {qty} exceeds approved {approval['max_qty']}")
        if not (approval["min_price"] <= price <= approval["max_price"]):
            raise ApprovalError(f"price {price} outside approved band [{approval['min_price']:.6g}, {approval['max_price']:.6g}]")
        tag = f"{approval['decision_id']}:{symbol}"
        with self._lock:
            if tag in self._used:
                raise ApprovalError("risk approval already used")
            if consume:
                self._used.add(tag)
