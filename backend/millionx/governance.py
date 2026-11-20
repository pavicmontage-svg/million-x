"""Risk Governance Layer — independent, hard, backend-enforced.

* Every risk parameter has explicit hard bounds (BOUNDS). Configuration files,
  the Android app, the owner API, AI prompts, executives and LLM
  recommendations can never set a value outside them; AI layers can only
  tighten via RiskManager.tighten().
* `pre_trade()` re-validates every condition immediately before a real order
  is sent, using the exchange as the source of truth for open positions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

BOUNDS: dict[str, tuple[float, float]] = {
    "risk_per_trade": (0.0005, 0.02),
    "max_daily_loss": (0.005, 0.10),
    "max_total_drawdown": (0.02, 0.35),
    "max_consecutive_losses": (1, 20),
    "cooldown_minutes": (5, 1440),
    "loss_streak_risk_scale": (0.1, 1.0),
    "drawdown_risk_scale_start": (0.01, 0.30),
    "max_open_positions": (1, 10),
    "max_total_exposure": (0.1, 5.0),
    "max_correlated_exposure": (0.1, 3.0),
    "max_leverage": (1, 20),
    "leverage_vol_target": (0.002, 0.05),
    "max_fee_to_risk": (0.05, 0.50),
    "min_reward_risk": (1.0, 5.0),
    "stale_data_seconds": (30, 180),
    "max_orders_per_minute": (1, 10),
    "max_orders_per_hour": (1, 120),
    "max_slippage_bps": (1.0, 50.0),
    "max_price_drift_bps": (1.0, 50.0),
    "max_spread_bps": (0.5, 20.0),
    "min_depth_usdt": (1_000.0, 1e9),
    "max_unlock_hours": (1, 24),
    "max_daily_loss_eur": (0.0, 1_000_000.0),
    "min_liq_buffer": (2.0, 20.0),
}
STR_FIELDS = {"leverage_tiers"}
BOOL_FIELDS = {"disconnect_flatten"}

# PAPER/DEMO only (virtual money): wider bounds so the owner can experiment with
# very high risk. LIVE always uses BOUNDS; switching to live with wider limits is refused.
SIM_BOUNDS: dict[str, tuple[float, float]] = {
    **BOUNDS,
    "risk_per_trade": (0.0005, 0.05),
    "max_daily_loss": (0.005, 0.50),
    "max_total_drawdown": (0.02, 0.90),
    "max_consecutive_losses": (1, 50),
    "max_total_exposure": (0.1, 50.0),
    "max_leverage": (1, 50),
    "leverage_vol_target": (0.002, 1.0),
}


def bounds_for(mode: str) -> dict[str, tuple[float, float]]:
    return BOUNDS if mode == "live" else SIM_BOUNDS


def validate_limits(d: dict[str, Any], mode: str = "live") -> list[str]:
    errs = []
    bounds = bounds_for(mode)
    for k, v in d.items():
        if k in BOOL_FIELDS:
            if not isinstance(v, bool):
                errs.append(f"{k} must be true/false")
            continue
        if k in STR_FIELDS:
            try:
                tiers = [int(x) for x in str(v).split(",")]
            except ValueError:
                errs.append(f"{k} must be comma-separated integers")
                continue
            hi = bounds.get("max_leverage", (1, 20))[1]
            if not tiers or tiers != sorted(tiers) or tiers[0] < 1 or tiers[-1] > hi:
                errs.append(f"{k}={v}: ascending integers between 1 and {hi}")
            continue
        if k not in bounds:
            errs.append(f"{k} is not a known risk parameter")
            continue
        lo, hi = bounds[k]
        try:
            x = float(v)
        except (TypeError, ValueError):
            errs.append(f"{k} must be a number")
            continue
        if math.isnan(x) or x < lo or x > hi:
            errs.append(f"{k}={v} outside hard bounds [{lo}, {hi}]")
    if "drawdown_risk_scale_start" in d and "max_total_drawdown" in d:
        if float(d["drawdown_risk_scale_start"]) >= float(d["max_total_drawdown"]):
            errs.append("drawdown_risk_scale_start must be below max_total_drawdown")
    if "max_orders_per_minute" in d and "max_orders_per_hour" in d:
        if float(d["max_orders_per_minute"]) > float(d["max_orders_per_hour"]):
            errs.append("max_orders_per_minute cannot exceed max_orders_per_hour")
    return errs


# Risk profiles: preset values, always validated against BOUNDS like any other change.
RISK_PROFILES: dict[str, dict[str, Any]] = {
    "safe": {"risk_per_trade": 0.0025, "max_daily_loss": 0.02, "max_total_drawdown": 0.10,
             "max_consecutive_losses": 3, "cooldown_minutes": 120, "loss_streak_risk_scale": 0.5,
             "max_open_positions": 2, "max_leverage": 5, "max_total_exposure": 1.5,
             "leverage_vol_target": 0.02, "leverage_tiers": "2,3,4,5"},
    # owner specification (500 EUR, 0.5 %, 10 EUR/day, 2 positions, 5-20x by setup quality, isolated)
    "standard": {"risk_per_trade": 0.005, "max_daily_loss": 0.02, "max_daily_loss_eur": 10.0,
                 "max_total_drawdown": 0.20, "max_consecutive_losses": 4, "cooldown_minutes": 60,
                 "loss_streak_risk_scale": 0.5, "max_open_positions": 2, "max_leverage": 20,
                 "max_total_exposure": 3.0, "leverage_vol_target": 0.02, "leverage_tiers": "5,10,15,20"},
    "aggressive": {"risk_per_trade": 0.02, "max_daily_loss": 0.10, "max_total_drawdown": 0.35,
                   "max_consecutive_losses": 10, "cooldown_minutes": 15, "loss_streak_risk_scale": 1.0,
                   "max_open_positions": 6, "max_leverage": 15, "max_total_exposure": 5.0,
                   "leverage_vol_target": 0.02, "leverage_tiers": "5,10,15,15"},
    # PAPER/DEMO only: 5 % risk per trade, up to 50x, no volatility leverage cap.
    "max": {"risk_per_trade": 0.05, "max_daily_loss": 0.50, "max_total_drawdown": 0.90,
            "max_consecutive_losses": 50, "cooldown_minutes": 5, "loss_streak_risk_scale": 1.0,
            "max_open_positions": 10, "max_leverage": 50, "max_total_exposure": 50.0,
            "leverage_vol_target": 1.0, "leverage_tiers": "20,30,40,50"},
}
SIM_ONLY_PROFILES = {"max"}


def apply_limit_changes(limits, changes: dict[str, Any], mode: str = "live") -> dict[str, Any]:
    """Validate the merged result before touching the live object (all-or-nothing)."""
    merged = dict(limits.__dict__)
    for k, v in changes.items():
        if k not in merged:
            raise ValueError(f"unknown limit {k}")
        cur = merged[k]
        merged[k] = (v if isinstance(v, bool) else bool(v)) if isinstance(cur, bool) else (
            str(v) if isinstance(cur, str) else type(cur)(v))
    errs = validate_limits(merged, mode)
    if errs:
        raise ValueError("; ".join(errs))
    old = dict(limits.__dict__)
    limits.__dict__.update(merged)
    return old


@dataclass
class PreTradeContext:
    symbol: str
    side: int                      # +1 long / -1 short
    qty: float
    leverage: int
    signal_price: float
    stop: float
    tp1: float
    exec_price: float              # price a market order would hit now
    equity: float
    risk_fraction: float
    taker_fee: float
    exchange_positions: dict[str, tuple[float, float]]
    spread_bps: Optional[float]
    depth_usdt: Optional[float]    # same-side depth within 10 bps
    entries_last_minute: int
    entries_last_hour: int
    data_fresh: bool
    connection_ok: bool
    emergency: bool
    live_mode: bool
    live_gate_ok: bool
    corr: Optional[Callable[[str, str], float]] = None
    extra: dict[str, Any] = field(default_factory=dict)


def pre_trade(L, c: PreTradeContext) -> list[str]:
    """Return a list of violations (empty = order may be sent)."""
    v: list[str] = []
    if c.emergency:
        v.append("emergency stop active")
    if not c.connection_ok:
        v.append("exchange connection not healthy")
    if not c.data_fresh:
        v.append("market data stale")
    if c.live_mode and not c.live_gate_ok:
        v.append("live authorisation missing, expired or invalidated")
    if c.qty <= 0 or c.exec_price <= 0 or c.equity <= 0:
        v.append("invalid quantity/price/equity")
        return v
    if c.entries_last_minute >= L.max_orders_per_minute:
        v.append(f"order frequency {c.entries_last_minute}/min >= {L.max_orders_per_minute}")
    if c.entries_last_hour >= L.max_orders_per_hour:
        v.append(f"order frequency {c.entries_last_hour}/h >= {L.max_orders_per_hour}")
    if c.symbol in c.exchange_positions:
        v.append("exchange already holds a position on this symbol")
    if len(c.exchange_positions) >= L.max_open_positions:
        v.append(f"open positions on exchange {len(c.exchange_positions)} >= {L.max_open_positions}")
    if c.leverage > L.max_leverage:
        v.append(f"leverage {c.leverage} > {L.max_leverage}")
    notional = c.qty * c.exec_price
    total = notional + sum(abs(a) * (p or c.exec_price) for a, p in c.exchange_positions.values())
    if total > L.max_total_exposure * c.equity:
        v.append(f"aggregate exposure {total:.2f} > {L.max_total_exposure}x equity")
    if c.corr is not None:
        corr_n = notional
        for sym, (amt, px) in c.exchange_positions.items():
            if (amt > 0) == (c.side > 0) and c.corr(c.symbol, sym) > 0.7:
                corr_n += abs(amt) * (px or c.exec_price)
        if corr_n > L.max_correlated_exposure * c.equity:
            v.append(f"correlated exposure {corr_n:.2f} > {L.max_correlated_exposure}x equity")
    if c.spread_bps is None or c.spread_bps > L.max_spread_bps:
        v.append(f"spread {c.spread_bps} bps above {L.max_spread_bps}")
    if c.depth_usdt is None or c.depth_usdt < L.min_depth_usdt:
        v.append(f"depth {c.depth_usdt} below {L.min_depth_usdt} USDT")
    elif notional > 0.1 * c.depth_usdt:
        v.append("order exceeds 10% of visible depth within 10 bps")
    drift_bps = (c.exec_price - c.signal_price) / c.signal_price * 1e4 * c.side
    if drift_bps > L.max_price_drift_bps:
        v.append(f"adverse price drift {drift_bps:.1f} bps > {L.max_price_drift_bps}")
    if (c.side > 0 and (c.exec_price <= c.stop or c.exec_price >= c.tp1)) or \
       (c.side < 0 and (c.exec_price >= c.stop or c.exec_price <= c.tp1)):
        v.append("current price already beyond stop or first target")
    risk_now = c.qty * abs(c.exec_price - c.stop) + notional * 2 * c.taker_fee
    allowed = c.equity * c.risk_fraction
    if risk_now > allowed * 1.10:
        v.append(f"risk at current price {risk_now:.4f} > allowed {allowed:.4f}")
    return v
