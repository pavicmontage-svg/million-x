"""Scalping engines (2.2). Each returns candidate entries at 1-second resolution.

A — Momentum: short impulses with relative volume, taker aggression and CVD acceleration, breaking the
    3-minute range, confirmed by 3m momentum, NOT already exhausted (move from the 5m extreme bounded).
B — Pullback: 1m EMA9/21 trend agreeing with the 5m trend, a pullback into the EMA21/VWAP zone on
    contracting volume, entered only when momentum recovers (1 s velocity + CVD in trend direction and a
    break of the last 60 s high/low) — never on a mere touch.
C — Order Flow: real order-book data only (recorded top-20 book + trade flow). Without a fresh book the
    setup is INCOMPLETE. Historical 1 s order books do not exist, so C runs live (paper) only.

Parameters are a small pre-registered grid; nothing is tuned on the validation period.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

# Owner decision (2026-11-21): optimise for MONEY (expected net R), not win rate -> large R/R targets
# (2R / 3R / 5R) and winners are allowed to run on the trailing stop. Win rate is reported, never a criterion.
GRID = {
    "A": [{"z": 2.5, "relvol": 2.0, "taker": 0.60, "ext_max": 3.0, "sl_sigma": 1.5, "rr": 3.0},
          {"z": 3.0, "relvol": 3.0, "taker": 0.65, "ext_max": 3.0, "sl_sigma": 1.5, "rr": 5.0},
          {"z": 3.0, "relvol": 3.0, "taker": 0.65, "ext_max": 3.0, "sl_sigma": 4.0, "rr": 5.0}],   # wide stop: fees << risk
    "B": [{"depth_atr": 0.5, "contract": 0.8, "sl_atr": 0.8, "rr": 3.0},
          {"depth_atr": 1.0, "contract": 0.7, "sl_atr": 1.0, "rr": 5.0},
          {"depth_atr": 1.0, "contract": 0.8, "sl_atr": 2.0, "rr": 5.0}],
}
COOLDOWN_S = 60  # per engine/config/symbol: at most one candidate per minute


def _cool(mask: np.ndarray, gap: int) -> np.ndarray:
    idx = np.flatnonzero(mask)
    keep, last = [], -10**9
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    return np.array(keep, dtype=np.int64)


def _nz(x, v=0.0):
    return np.nan_to_num(x, nan=v)


def engine_a(f: dict[str, np.ndarray], c: np.ndarray, p: dict[str, Any]) -> list[tuple[int, int, float, float]]:
    """-> [(index, side, stop_distance_bps, score)]"""
    sig = np.maximum(_nz(f["sigma300"], 0.0), 0.05)
    common = (_nz(f["relvol30"]) >= p["relvol"])
    up = common & (_nz(f["v30z"]) >= p["z"]) & (_nz(f["taker30"], 0.5) >= p["taker"]) & (_nz(f["break_up"], -1) > 0) \
        & (_nz(f["cvd_acc"]) > 0) & (_nz(f["mom3m"]) > 0) & (_nz(f["ext_up"], 99) <= p["ext_max"])
    dn = common & (_nz(f["v30z"]) <= -p["z"]) & (_nz(f["taker30"], 0.5) <= 1 - p["taker"]) & (_nz(f["break_dn"], -1) > 0) \
        & (_nz(f["cvd_acc"]) < 0) & (_nz(f["mom3m"]) < 0) & (_nz(f["ext_dn"], 99) <= p["ext_max"])
    out = []
    for side, m in ((1, up), (-1, dn)):
        for i in _cool(m, COOLDOWN_S):
            stop = p["sl_sigma"] * sig[i] * np.sqrt(60)            # ~ 1-minute sigma multiples, bps
            out.append((int(i), side, float(stop), float(abs(f["v30z"][i]))))
    return out


def engine_b(f: dict[str, np.ndarray], c: np.ndarray, p: dict[str, Any]) -> list[tuple[int, int, float, float]]:
    e9, e21, atr = f["ema9_1m"], f["ema21_1m"], f["atr1m"]
    ok = np.isfinite(e9) & np.isfinite(e21) & np.isfinite(atr) & (atr > 0)
    up_tr = ok & (e9 > e21) & (_nz(f["trend5"]) > 0)
    dn_tr = ok & (e9 < e21) & (_nz(f["trend5"]) < 0)
    zone_up = np.fmin(e21, np.nan_to_num(f["vwap4h"], nan=np.inf)) + p["depth_atr"] * atr  # pullback reached EMA21/VWAP zone
    zone_dn = np.fmax(e21, np.nan_to_num(f["vwap4h"], nan=-np.inf)) - p["depth_atr"] * atr
    pulled_up = _nz(f["lo60"], np.inf) <= zone_up
    pulled_dn = _nz(f["hi60"], -np.inf) >= zone_dn
    contract = _nz(f["vol_contract"], 9) <= p["contract"]
    up = up_tr & pulled_up & contract & (_nz(f["v10"]) > 0) & (_nz(f["cvd10"]) > 0) & (c > _nz(f["hi60"], np.inf))
    dn = dn_tr & pulled_dn & contract & (_nz(f["v10"]) < 0) & (_nz(f["cvd10"]) < 0) & (c < _nz(f["lo60"], -np.inf))
    out = []
    for side, m in ((1, up), (-1, dn)):
        for i in _cool(m, COOLDOWN_S):
            stop = p["sl_atr"] * atr[i] / c[i] * 1e4
            depth = ((c[i] - f["lo60"][i]) if side > 0 else (f["hi60"][i] - c[i])) / atr[i]
            out.append((int(i), side, float(stop), float(depth)))
    return out


def engine_c(book: Optional[dict[str, Any]], flow: Optional[dict[str, Any]], p: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Live only. book: latest recorded top-20 features (imb5/imb10, removed_bid10/ask10, spread_bps, impact_*,
    age_ms); flow: last 10 s aggressive buy/sell qty and price move (move_bps). Returns {'status': COMPLETE|INCOMPLETE, 'side', 'reasons'}."""
    p = p or {"imb": 0.35, "delta": 0.30, "max_spread_bps": 2.0, "max_age_ms": 2_000}
    if not book or book.get("age_ms") is None or book["age_ms"] > p["max_age_ms"]:
        return {"status": "INCOMPLETE", "side": 0, "reasons": ["no fresh order book (live data missing)"]}
    if not flow or (flow.get("buy", 0) + flow.get("sell", 0)) <= 0:
        return {"status": "INCOMPLETE", "side": 0, "reasons": ["no trade flow in the last 10 s"]}
    reasons = []
    d = (flow["buy"] - flow["sell"]) / (flow["buy"] + flow["sell"])
    imb = book.get("imb10") or 0.0
    rem_ask, rem_bid = book.get("removed_ask10") or 0.0, book.get("removed_bid10") or 0.0
    if book.get("spread_bps", 99) > p["max_spread_bps"]:
        return {"status": "COMPLETE", "side": 0, "reasons": [f"spread {book.get('spread_bps'):.2f} bps too wide"]}
    side = 0
    if imb >= p["imb"] and d >= p["delta"] and rem_ask >= rem_bid:
        side = 1
        reasons = [f"bid-heavy book {imb:+.2f}", f"aggressive buying {d:+.2f}", "ask liquidity being removed"]
    elif imb <= -p["imb"] and d <= -p["delta"] and rem_bid >= rem_ask:
        side = -1
        reasons = [f"ask-heavy book {imb:+.2f}", f"aggressive selling {d:+.2f}", "bid liquidity being removed"]
    # absorption: strong aggression but price did not move -> passive liquidity is absorbing it: no entry
    if side and abs(d) > 0.6 and abs(flow.get("move_bps", 1.0)) < 0.5:
        return {"status": "COMPLETE", "side": 0, "reasons": ["aggression absorbed by passive liquidity (no price progress)"]}
    return {"status": "COMPLETE", "side": side, "reasons": reasons or ["no order-flow setup"]}
