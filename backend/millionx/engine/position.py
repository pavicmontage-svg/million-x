"""Exit management shared by backtest, paper and live: stop loss, partial take
profit, break-even protection, ATR trailing stop and a time stop.

Bar-level conservatism: if the stop and a target are both inside one bar the
stop is assumed to have been hit first; gaps through the stop fill at the open.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Position, Side


@dataclass
class ExitPolicy:
    partial_fraction: float = 0.5
    breakeven_after_tp1: bool = True
    be_buffer_fee_mult: float = 2.0   # stop moved to entry +/- round-trip fees
    trail_atr: float = 1.0
    max_hold_minutes: int = 180


@dataclass
class ExitEvent:
    kind: str            # PARTIAL | CLOSE | STOP_MOVED
    price: float
    qty: float = 0.0
    reason: str = ""
    maker: bool = False


def update_on_bar(pos: Position, o: float, h: float, l: float, c: float, atr: float, ts_ms: int,
                  policy: ExitPolicy, taker_fee: float) -> list[ExitEvent]:
    ev: list[ExitEvent] = []
    s = int(pos.side)
    # 1) stop first (conservative)
    stop_hit = (l <= pos.stop) if s > 0 else (h >= pos.stop)
    if stop_hit:
        gap = (o < pos.stop) if s > 0 else (o > pos.stop)
        px = o if gap else pos.stop
        reason = "trailing_stop" if pos.trail_active else ("breakeven_stop" if pos.tp1_done else "stop_loss")
        ev.append(ExitEvent("CLOSE", px, pos.qty, reason))
        return ev
    # 2) partial at tp1
    if not pos.tp1_done:
        tp1_hit = (h >= pos.tp1) if s > 0 else (l <= pos.tp1)
        if tp1_hit:
            q = pos.qty * policy.partial_fraction
            ev.append(ExitEvent("PARTIAL", pos.tp1, q, "take_profit_1"))
            pos.tp1_done = True
            if policy.breakeven_after_tp1:
                be = pos.entry_price * (1 + s * policy.be_buffer_fee_mult * taker_fee)
                if (s > 0 and be > pos.stop) or (s < 0 and be < pos.stop):
                    pos.stop = be
                    ev.append(ExitEvent("STOP_MOVED", be, reason="break_even"))
            pos.trail_active = True
    # 3) final target
    tp2_hit = (h >= pos.tp2) if s > 0 else (l <= pos.tp2)
    if tp2_hit:
        ev.append(ExitEvent("CLOSE", pos.tp2, pos.qty, "take_profit_2"))
        return ev
    # 4) time stop
    if ts_ms - pos.opened_at >= policy.max_hold_minutes * 60_000:
        ev.append(ExitEvent("CLOSE", c, pos.qty, "time_stop"))
        return ev
    # 5) trailing stop evaluated at the close, effective from next bar
    pos.best_price = max(pos.best_price, h) if s > 0 else min(pos.best_price or l, l)
    if pos.trail_active and atr > 0:
        trail = pos.best_price - s * policy.trail_atr * atr
        if (s > 0 and trail > pos.stop) or (s < 0 and trail < pos.stop):
            pos.stop = trail
            ev.append(ExitEvent("STOP_MOVED", trail, reason="trailing"))
    return ev
