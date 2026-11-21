"""Live-trading gate: evidence from >= 30 days of paper trading on the same market."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import verify_chain
from ..models import Order, OrderStatus, Portfolio, Position, PositionStatus, Signal, SignalStatus, TradingMode

MIN_DAYS, MIN_TRADES, MIN_PF, MAX_DD, MIN_EXEC = 30, 20, 1.2, 10.0, 0.95


def live_readiness(s: Session, p: Portfolio) -> dict:
    paper_ids = [x.id for x in s.scalars(select(Portfolio).where(Portfolio.market == p.market,
                                                                 Portfolio.mode.in_([TradingMode.PAPER, TradingMode.DEMO])))]
    closed = list(s.scalars(select(Position).where(Position.portfolio_id.in_(paper_ids),
                                                   Position.status == PositionStatus.CLOSED).order_by(Position.closed_at)))
    first = s.scalar(select(func.min(Position.opened_at)).where(Position.portfolio_id.in_(paper_ids)))
    days = (datetime.now(timezone.utc) - (first if first.tzinfo else first.replace(tzinfo=timezone.utc))).days if first else 0
    pnl = [x.realized_pnl for x in closed]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x < 0]
    pf = sum(wins) / -sum(losses) if losses else None
    eq = np.cumsum([0.0] + pnl)
    cap = sum(x.initial_capital for x in s.scalars(select(Portfolio).where(Portfolio.id.in_(paper_ids)))) or 1
    curve = cap + eq
    dd = float(((np.maximum.accumulate(curve) - curve) / np.maximum.accumulate(curve)).max() * 100) if len(curve) > 1 else 0.0
    sig_total = s.scalar(select(func.count(Signal.id)).where(Signal.market == p.market,
                                                             Signal.status.in_([SignalStatus.EXECUTED, SignalStatus.FAILED,
                                                                                SignalStatus.PARTIALLY_EXECUTED]))) or 0
    sig_ok = s.scalar(select(func.count(Signal.id)).where(Signal.market == p.market, Signal.status == SignalStatus.EXECUTED)) or 0
    exec_acc = sig_ok / sig_total if sig_total else None
    unknown = s.scalar(select(func.count(Order.id)).where(Order.portfolio_id.in_(paper_ids), Order.status == OrderStatus.UNKNOWN)) or 0
    chain = verify_chain(s)
    checks = [
        {"check": f">= {MIN_DAYS} days of paper/demo trading", "value": days, "ok": days >= MIN_DAYS},
        {"check": f">= {MIN_TRADES} closed copied trades", "value": len(closed), "ok": len(closed) >= MIN_TRADES},
        {"check": f"profit factor >= {MIN_PF}", "value": pf, "ok": pf is not None and pf >= MIN_PF},
        {"check": f"max drawdown < {MAX_DD}%", "value": round(dd, 3), "ok": dd < MAX_DD},
        {"check": f"execution accuracy >= {MIN_EXEC:.0%}", "value": exec_acc, "ok": exec_acc is not None and exec_acc >= MIN_EXEC},
        {"check": "no unresolved UNKNOWN orders", "value": unknown, "ok": unknown == 0},
        {"check": "audit chain intact", "value": chain["valid"], "ok": chain["valid"]},
    ]
    return {"portfolio_id": p.id, "portfolio": p.name, "market": p.market.value, "ready": all(c["ok"] for c in checks),
            "checks": checks, "note": "Passing the gate is evidence of process quality, not a promise of profit. "
                                      "Live trading additionally needs TITAN_LIVE_TRADING_ENABLED=true and a typed approval."}
