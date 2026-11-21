"""Scalp position management and cost model (2.2), shared by backtest and the live PaperBook.

Exit rules (pre-registered, identical everywhere):
  * initial stop = engine stop distance; take profit = rr x stop (2R / 3R / 5R: large R/R targets)
  * break-even: after max(+1.0 R, 2 x round-trip cost) the stop moves to entry + round-trip cost
    (never gives back fees; never placed above the market)
  * trailing: after +1.5 R the stop trails the best price by 1.0 R
  * momentum exhaustion: 30 s velocity against the position beyond 1.5 sigma while in profit < 0.5 R
  * order-flow reversal: 10 s CVD flips against the position (|cvd10| >= 0.4) after 30 s in the trade
  * maximum holding time: 15 minutes; only a position already trailing in profit (>= 1.5 R) may run up to
    45 minutes (large-R/R winners) — a losing scalp is never held as a swing trade
  * on a second that touches both stop and target, the STOP is assumed (conservative)
  * during a simulated exchange outage no exit can execute; the exit happens at the first second after it
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

MAX_HOLD_S = 900
MAX_HOLD_WINNER_S = 2700
BE_R, TRAIL_R, TRAIL_DIST_R = 1.0, 1.5, 1.0


@dataclass
class Costs:
    name: str
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slippage_bps: float = 1.0      # per taker fill
    spread_bps: float = 1.0        # full spread; a taker pays half of it per fill
    latency_s: int = 1
    funding_bps_per_8h: float = 1.0

    def round_trip(self, maker_entry: bool = False, hold_s: float = 0) -> float:
        entry = self.maker_bps if maker_entry else (self.taker_bps + self.slippage_bps + self.spread_bps / 2)
        exit_ = self.taker_bps + self.slippage_bps + self.spread_bps / 2
        return entry + exit_ + self.funding_bps_per_8h * hold_s / 28_800


SCENARIOS = {"BASE": Costs("BASE"),
             "STRESSED": Costs("STRESSED", slippage_bps=3.0, spread_bps=3.0, latency_s=3, funding_bps_per_8h=3.0),
             "EXTREME": Costs("EXTREME", taker_bps=6.0, slippage_bps=6.0, spread_bps=6.0, latency_s=5, funding_bps_per_8h=5.0)}


def simulate(i: int, side: int, stop_bps: float, rr: float, c: np.ndarray, h: np.ndarray, lo: np.ndarray,
             v30z: np.ndarray, cvd10: np.ndarray, cost: Costs, outage: Optional[np.ndarray] = None,
             maker_fill_s: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Event-driven exit simulation from decision second i. Returns gross/net bps, R, hold, reason, MFE/MAE."""
    e = i + (maker_fill_s if maker_fill_s is not None else cost.latency_s)
    n = len(c)
    if e >= n - 2 or (outage is not None and outage[e]):
        return None
    entry = c[e]
    end = min(n - 1, e + MAX_HOLD_WINNER_S)
    hh, ll, cc = h[e + 1:end + 1], lo[e + 1:end + 1], c[e + 1:end + 1]
    if len(cc) == 0:
        return None
    sd = stop_bps / 1e4 * entry
    rt = cost.round_trip(maker_fill_s is not None) / 1e4 * entry
    fav = (hh - entry) if side > 0 else (entry - ll)          # favourable excursion per second
    adv = (entry - ll) if side > 0 else (hh - entry)
    best_prev = np.maximum.accumulate(np.r_[0.0, fav[:-1]])    # best excursion BEFORE each second
    stop_off = np.full(len(cc), -sd)                            # stop as offset from entry (negative = loss)
    stop_off = np.where(best_prev >= max(BE_R * sd, 2 * rt), np.maximum(stop_off, rt), stop_off)
    trailing = best_prev >= TRAIL_R * sd
    stop_off = np.where(trailing, np.maximum(stop_off, best_prev - TRAIL_DIST_R * sd), stop_off)
    hit_stop = -adv <= stop_off                                 # adverse move reaches the (moving) stop
    hit_tp = fav >= rr * sd
    rel = (cc - entry) * side
    k = np.arange(len(cc))
    exhaust = (k >= 5) & ((v30z[e + 1:end + 1] * side) <= -1.5) & (rel < 0.5 * sd) & ~trailing
    reverse = (k >= 30) & ((cvd10[e + 1:end + 1] * side) <= -0.4) & ~trailing  # runners exit on the trail
    timeout = (k + 1 >= MAX_HOLD_S) & ~trailing                 # 15 min unless trailing in profit
    events = hit_stop | hit_tp | exhaust | reverse | timeout
    j = int(np.argmax(events)) if events.any() else len(cc) - 1
    if hit_stop[j]:
        px_off, reason = stop_off[j], ("stop" if stop_off[j] < 0 else ("break_even" if not trailing[j] else "trailing"))
    elif hit_tp[j]:
        px_off, reason = rr * sd, "take_profit"
    elif exhaust[j]:
        px_off, reason = rel[j], "momentum_exhaustion"
    elif reverse[j]:
        px_off, reason = rel[j], "order_flow_reversal"
    else:
        px_off, reason = rel[j], "max_hold"
    x = j
    if outage is not None and outage[e + 1 + j]:               # exit delayed until the exchange is back
        after = np.flatnonzero(~outage[e + 1 + j:end + 1])
        x = j + (int(after[0]) if len(after) else len(cc) - 1 - j)
        x = min(x, len(cc) - 1)
        px_off, reason = rel[x], reason + "_after_outage"
    hold = x + 1
    gross = px_off / entry * 1e4
    net = gross - cost.round_trip(maker_fill_s is not None, hold)
    return {"entry_s": e, "gross_bps": float(gross), "net_bps": float(net), "r": float(net / stop_bps),
            "hold_s": int(hold), "reason": reason, "mfe_bps": float(fav[:x + 1].max() / entry * 1e4),
            "mae_bps": float(adv[:x + 1].max() / entry * 1e4), "cost_bps": float(gross - net)}


def maker_fill(i: int, side: int, c: np.ndarray, h: np.ndarray, lo: np.ndarray, buy: np.ndarray, sell: np.ndarray,
               order_qty: float, wait_s: int = 10, participation: float = 0.1) -> Optional[tuple[int, float]]:
    """Passive limit at the decision price (best bid for a long / ask for a short). It counts as a MAKER fill
    only when later trades go THROUGH the limit (strictly beyond it): a touch is not a fill. The filled share is
    capped at `participation` of the aggressive volume that traded through. Returns (seconds to fill, share)."""
    limit = c[i]
    filled = 0.0
    for k in range(1, wait_s + 1):
        j = i + k
        if j >= len(c):
            return None
        through = lo[j] < limit if side > 0 else h[j] > limit
        if through:
            vol = sell[j] if side > 0 else buy[j]   # longs are filled by aggressive sellers hitting the bid
            filled += participation * vol
            if filled >= order_qty:
                return k, 1.0
    return (wait_s, filled / order_qty) if filled > 0 else None
