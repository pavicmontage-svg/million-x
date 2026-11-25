"""AGENT 3 — Execution Specialist: pre-trade execution quality and post-trade slippage analysis."""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

from ..config import AppConfig
from ..core.types import Decision, Direction
from ..orchestrator.costs import slippage_bps


def walk_book(book: dict, side: str, qty: float) -> tuple[float, float]:
    """Average fill price and filled qty for a market order walking the visible book."""
    levels = book.get("asks" if side == "BUY" else "bids") or []
    left, cost, filled = qty, 0.0, 0.0
    for p, q in levels:
        p, q = float(p), float(q)
        take = min(left, q)
        cost += take * p
        filled += take
        left -= take
        if left <= 1e-15:
            break
    return (cost / filled if filled > 0 else float("nan")), filled


class ExecutionSpecialist:
    name = "execution_specialist"
    title = "Execution Specialist"
    responsibilities = ["Optimise execution", "Monitor spread", "Estimate slippage", "Analyse market impact",
                        "Choose order types", "Avoid poor execution"]

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.realized: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

    def pre_trade(self, d: Decision, qty: float, *, book: dict | None = None, spread_bps: float | None = None,
                  bar_quote_volume: float = 0.0, atr: float = 0.0) -> dict:
        """Return {ok, order_type, expected_slippage_bps, notes}."""
        side = "BUY" if d.direction is Direction.LONG else "SELL"
        notes: list[str] = []
        model = slippage_bps(self.cfg.costs, d.entry, atr, qty * d.entry, bar_quote_volume)
        expected = model
        if book and book.get("bids") and book.get("asks"):
            mid = (float(book["bids"][0][0]) + float(book["asks"][0][0])) / 2
            avg, filled = walk_book(book, side, qty)
            if filled < qty * 0.999:
                return {"ok": False, "order_type": None, "expected_slippage_bps": float("inf"),
                        "notes": [f"visible book depth {filled:.6g} < order {qty:.6g} — insufficient liquidity"]}
            book_bps = abs(avg - mid) / mid * 1e4
            expected = max(model, book_bps)
            notes.append(f"book walk {book_bps:.2f}bps vs model {model:.2f}bps")
        ok = expected <= self.cfg.risk.max_slippage_bps
        if not ok:
            notes.append(f"expected slippage {expected:.1f}bps > {self.cfg.risk.max_slippage_bps}bps")
        order_type = self.cfg.execution.entry_order_type
        if spread_bps is not None and np.isfinite(spread_bps) and spread_bps > self.cfg.risk.max_spread_bps / 2:
            order_type = "LIMIT_IOC"
            notes.append(f"spread {spread_bps:.1f}bps — using marketable limit (IOC) to cap price")
        return {"ok": ok, "order_type": order_type, "expected_slippage_bps": round(expected, 3), "notes": notes}

    def record_fill(self, symbol: str, expected_price: float, fill_price: float, side: str, expected_bps: float) -> None:
        sgn = 1 if side == "BUY" else -1
        realized = (fill_price - expected_price) / expected_price * 1e4 * sgn
        self.realized[symbol].append((realized, expected_bps))

    def report(self) -> dict:
        per = {}
        for s, rows in self.realized.items():
            r = np.array([x for x, _ in rows]); e = np.array([y for _, y in rows])
            per[s] = {"fills": len(rows), "avg_realized_bps": round(float(r.mean()), 3),
                      "avg_expected_bps": round(float(e.mean()), 3),
                      "underestimated": bool(r.mean() > e.mean() * 1.5 + 0.5)}
        return {"agent": self.name, "per_symbol": per}
