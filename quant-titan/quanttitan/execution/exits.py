"""Exit-management rules shared by the backtester and the live position manager.

* Stop-loss first: when a bar touches both the stop and a target, the stop is assumed to fill first
  (conservative). A gap through the stop fills at the bar open (worse than the stop).
* Partial take-profits close their configured fraction of the *initial* quantity.
* After TP1, the stop moves to break-even (entry) if configured.
* Runner: chandelier trailing stop = best price ∓ trail_atr × ATR, ratcheting only in the
  position's favour, updated on bar close (applies from the next bar).
* Time stop after ``time_stop_bars`` bars.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Direction, Position


@dataclass
class ExitFill:
    qty: float
    price: float
    reason: str  # stop_loss | take_profit_N | trailing_stop | breakeven_stop | time_stop | signal_exit


def trail_level(p: Position, atr: float) -> float | None:
    if p.trail_atr <= 0 or atr <= 0 or p.tp_hits == 0:
        return None  # trail only the runner, after TP1
    s = p.direction.sign
    lvl = p.best_price - s * p.trail_atr * atr
    if (lvl - p.stop) * s > 0:
        return lvl
    return None


def stop_reason(p: Position) -> str:
    if p.tp_hits == 0:
        return "stop_loss"
    if abs(p.stop - p.entry_price) <= 1e-12 * max(1.0, p.entry_price):
        return "breakeven_stop"
    return "trailing_stop" if (p.stop - p.entry_price) * p.direction.sign > 0 else "stop_loss"


def simulate_bar(p: Position, o: float, h: float, l: float, c: float, *, move_to_be: bool = True) -> list[ExitFill]:
    """Apply one OHLC bar to an open position (mutates it) and return exit fills."""
    fills: list[ExitFill] = []
    s = p.direction.sign
    # stop first (including a gap through it)
    stop_hit = (l <= p.stop) if s > 0 else (h >= p.stop)
    if stop_hit:
        gap = (o < p.stop) if s > 0 else (o > p.stop)
        px = o if gap else p.stop
        fills.append(ExitFill(p.qty, px, stop_reason(p)))
        p.qty = 0.0
        return fills
    # targets in order
    for k, t in enumerate(p.targets):
        if k < p.tp_hits or p.qty <= 0:
            continue
        hit = (h >= t.price) if s > 0 else (l <= t.price)
        if not hit:
            break
        last = k == len(p.targets) - 1
        q = p.qty if last else min(p.qty, p.initial_qty * t.fraction)
        gap = (o > t.price) if s > 0 else (o < t.price)
        fills.append(ExitFill(q, o if gap else t.price, f"take_profit_{k + 1}"))
        p.qty -= q
        p.tp_hits = k + 1
        if move_to_be and k == 0 and p.qty > 0 and (p.entry_price - p.stop) * s > 0:
            p.stop = p.entry_price
    # best price for trailing (after the bar)
    p.best_price = max(p.best_price, h) if s > 0 else min(p.best_price or l, l)
    return fills


def round_down(x: float, step: float) -> float:
    return int(x / step + 1e-9) * step if step > 0 else x


def direction_side(d: Direction, closing: bool = False) -> str:
    buy = d is Direction.LONG
    if closing:
        buy = not buy
    return "BUY" if buy else "SELL"
