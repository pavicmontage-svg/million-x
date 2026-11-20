"""Independent risk manager (CRO hard limits).

Nothing outside this module can loosen these limits at runtime: the
supervisor, the AI brains and the executives may only *tighten* (reject a
trade, reduce size, suspend a bot). Limits change only through an owner
action that is written to the audit log.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from .config import FeeModel, RiskLimits
from .models import Position, Signal, Side


@dataclass
class SymbolRules:
    symbol: str
    tick_size: float = 0.01
    step_size: float = 0.001
    min_qty: float = 0.001
    min_notional: float = 5.0
    max_leverage: int = 20
    maint_margin_rate: float = 0.004
    maker: Optional[float] = None
    taker: Optional[float] = None

    def round_qty(self, q: float) -> float:
        steps = math.floor(q / self.step_size + 1e-9)
        return round(steps * self.step_size, 12)

    def round_price(self, p: float) -> float:
        return round(round(p / self.tick_size) * self.tick_size, 12)


@dataclass
class SizingDecision:
    approved: bool
    qty: float = 0.0
    leverage: int = 1
    notional: float = 0.0
    risk_amount: float = 0.0
    risk_fraction: float = 0.0
    expected_cost: float = 0.0
    net_reward_risk: float = 0.0
    reasons: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AccountRiskState:
    equity: float = 0.0
    peak_equity: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: int = 0
    emergency_stop: bool = False
    halted_reason: str = ""
    connection_ok: bool = True
    data_fresh: bool = True


class RiskManager:
    def __init__(self, limits: RiskLimits, fees: FeeModel, eur_usdt: float = 1.08):
        self.limits = limits
        self.fees = fees
        self.eur_usdt = eur_usdt
        self.state = AccountRiskState()
        self.tighten_factor = 1.0   # set by CRO/AI layer, can only be <= 1

    # ---- state updates ---------------------------------------------------
    def reset(self, equity: float, ts_ms: int) -> None:
        self.state = AccountRiskState(equity=equity, peak_equity=equity, day=_day(ts_ms), day_start_equity=equity)

    def update_equity(self, equity: float, ts_ms: int) -> None:
        st = self.state
        d = _day(ts_ms)
        if d != st.day:
            st.day, st.day_start_equity = d, equity
            if st.halted_reason == "daily_loss":
                st.halted_reason = ""
        st.equity = equity
        st.peak_equity = max(st.peak_equity, equity)

    def on_trade_closed(self, net_pnl: float, ts_ms: int) -> None:
        st = self.state
        if net_pnl < 0:
            st.consecutive_losses += 1
            if st.consecutive_losses >= self.limits.max_consecutive_losses:
                st.cooldown_until = ts_ms + self.limits.cooldown_minutes * 60_000
        else:
            st.consecutive_losses = 0

    def tighten(self, factor: float) -> float:
        """Tighten-only hook for supervisory layers. Values > 1 are ignored."""
        self.tighten_factor = max(0.0, min(1.0, factor))
        return self.tighten_factor

    def emergency(self, on: bool = True, reason: str = "manual") -> None:
        self.state.emergency_stop = on
        self.state.halted_reason = reason if on else ""

    # ---- metrics ---------------------------------------------------------
    @property
    def drawdown(self) -> float:
        st = self.state
        return 0.0 if st.peak_equity <= 0 else 1 - st.equity / st.peak_equity

    @property
    def daily_pnl_frac(self) -> float:
        st = self.state
        return 0.0 if st.day_start_equity <= 0 else st.equity / st.day_start_equity - 1

    def can_open(self, ts_ms: int) -> tuple[bool, str]:
        st, L = self.state, self.limits
        if st.emergency_stop:
            return False, "emergency_stop"
        if not st.connection_ok:
            return False, "exchange_disconnected"
        if not st.data_fresh:
            return False, "stale_market_data"
        if self.drawdown >= L.max_total_drawdown:
            st.halted_reason = "max_drawdown"
            return False, "max_drawdown_reached"
        if self.daily_pnl_frac <= -L.max_daily_loss:
            st.halted_reason = "daily_loss"
            return False, "daily_loss_limit"
        lim_eur = getattr(L, "max_daily_loss_eur", 0.0) or 0.0
        if lim_eur > 0 and st.day_start_equity > 0 and (st.day_start_equity - st.equity) >= lim_eur * self.eur_usdt:
            st.halted_reason = "daily_loss"
            return False, "daily_loss_limit"
        if ts_ms < st.cooldown_until:
            return False, "loss_streak_cooldown"
        if self.tighten_factor <= 0:
            return False, "suspended_by_risk_oversight"
        return True, "ok"

    def risk_fraction(self) -> float:
        L, st = self.limits, self.state
        f = L.risk_per_trade
        if st.consecutive_losses >= max(2, L.max_consecutive_losses // 2):
            f *= L.loss_streak_risk_scale
        dd = self.drawdown
        if dd > L.drawdown_risk_scale_start:
            span = max(1e-9, L.max_total_drawdown - L.drawdown_risk_scale_start)
            f *= max(0.25, 1 - (dd - L.drawdown_risk_scale_start) / span)
        return f * self.tighten_factor

    # ---- sizing ----------------------------------------------------------
    # ---- leverage --------------------------------------------------------
    def leverage_tier(self, quality: Optional[dict[str, Any]], atr_pct15: float) -> tuple[int, list[str]]:
        """5x weak / 10x standard / 15x very good / 20x exceptional. Confidence alone
        can never reach 20x: it needs a strong setup score AND calm volatility AND a
        tight, liquid book."""
        tiers = [int(x) for x in str(getattr(self.limits, "leverage_tiers", "5,10,15,20")).split(",")]
        q = quality or {}
        pts, why = 0, []
        score = float(q.get("score", 0.0) or 0.0)
        if score >= 0.5:
            pts += 1
            why.append(f"setup score {score:.2f} >= 0.5")
        if score >= 0.7:
            pts += 1
            why.append("setup score >= 0.7")
        if atr_pct15 and 0 < atr_pct15 <= 0.006:
            pts += 1
            why.append(f"calm volatility (ATR15 {atr_pct15 * 100:.2f}%)")
        sp = q.get("spread_bps")
        if sp is not None and sp <= 1.0:
            pts += 1
            why.append(f"tight spread {sp:.2f} bps")
        idx = max(0, min(len(tiers) - 1, pts - 1))
        return tiers[idx], why

    def size(self, sig: Signal, rules: SymbolRules, open_positions: list[Position], atr_pct15: float,
             ts_ms: int, size_multiplier: float = 1.0,
             quality: Optional[dict[str, Any]] = None, max_leverage: Optional[int] = None) -> SizingDecision:
        L = self.limits
        ok, why = self.can_open(ts_ms)
        if not ok:
            return SizingDecision(False, reasons=[why])
        if len(open_positions) >= L.max_open_positions:
            return SizingDecision(False, reasons=["max_open_positions"])
        if any(p.symbol == sig.symbol for p in open_positions):
            return SizingDecision(False, reasons=["position_already_open_on_symbol"])
        equity = self.state.equity
        if equity <= 0:
            return SizingDecision(False, reasons=["no_equity"])

        taker = rules.taker if rules.taker is not None else self.fees.taker
        slip = self.fees.slippage_bps / 1e4
        frac = self.risk_fraction() * max(0.0, min(1.0, size_multiplier))
        risk_amt = equity * frac
        entry = sig.entry * (1 + slip * int(sig.side))
        d = abs(entry - sig.stop)
        if d <= 0:
            return SizingDecision(False, reasons=["invalid_stop"])
        hold_h = 3.0 * max(1, int(getattr(sig, "timeframe", 5) or 5)) / 5.0  # expected hold ~36 bars
        funding = self.fees.funding_rate_8h * min(hold_h, 72.0) / 8.0
        per_unit_cost = entry * (2 * taker + 2 * slip + funding)
        qty = rules.round_qty(risk_amt / (d + per_unit_cost))
        dec = SizingDecision(False, risk_fraction=frac)
        if qty < rules.min_qty or qty * entry < rules.min_notional:
            need = (max(rules.min_qty, rules.min_notional / entry)) * (d + per_unit_cost) / equity
            dec.reasons.append(f"below_exchange_minimum (min notional {rules.min_notional} needs "
                               f"{need * 100:.2f}% risk vs allowed {frac * 100:.2f}%)")
            return dec

        # leverage by setup quality (5/10/15/20x), never the exchange maximum; ISOLATED margin
        tier, why_tier = self.leverage_tier(quality, atr_pct15)
        lev_cap = int(max(1, min(L.max_leverage, rules.max_leverage or L.max_leverage, tier,
                                 max_leverage or L.max_leverage)))
        if max_leverage and max_leverage < tier:
            why_tier = why_tier + [f"supervisor approved max {max_leverage}x"]
        used_margin = sum(p.qty * p.entry_price / max(1, p.leverage) for p in open_positions)
        free = max(0.0, equity - used_margin) * 0.9
        notional = qty * entry
        need_lev = max(1, math.ceil(notional / free)) if free > 0 else 10 ** 6
        if need_lev > lev_cap:
            qty = rules.round_qty(free * lev_cap / entry)
            notional = qty * entry
            dec.adjustments.append(f"size reduced to fit {lev_cap}x margin")
            if qty < rules.min_qty or notional < rules.min_notional:
                dec.reasons.append(f"leverage_cap_{lev_cap}x_prevents_min_notional")
                return dec
        stop_dist = d / entry
        lev = lev_cap
        # liquidation protection: the stop must sit far before the estimated liquidation price
        while lev > 1 and (1 / lev - rules.maint_margin_rate) < L.min_liq_buffer * stop_dist:
            lev -= 1
        liq_dist = 1 / lev - rules.maint_margin_rate
        if liq_dist < L.min_liq_buffer * stop_dist or notional / lev > free:
            dec.reasons.append(f"stop {stop_dist * 100:.2f}% too close to liquidation {liq_dist * 100:.2f}% at {lev}x "
                               f"(needs {L.min_liq_buffer}x buffer)")
            return dec
        if lev < tier:
            dec.adjustments.append(f"leverage {tier}x lowered to {lev}x for liquidation buffer")
        dec.adjustments.append(f"leverage {lev}x ISOLATED (" + ("; ".join(why_tier) or "base tier") + ")")
        liq_price = entry * (1 - int(sig.side) * liq_dist)
        dec.adjustments.append(f"est. liquidation {liq_price:.6g} vs stop {sig.stop:.6g}")
        total_notional = sum(p.qty * p.entry_price for p in open_positions) + notional
        if total_notional > L.max_total_exposure * equity:
            dec.reasons.append("max_total_exposure")
            return dec
        cost = notional * (2 * taker + 2 * slip + funding)
        actual_risk = qty * d + cost
        if cost > L.max_fee_to_risk * actual_risk:
            dec.reasons.append(f"fees {cost:.4f} are {cost / actual_risk * 100:.0f}% of risk (max {L.max_fee_to_risk * 100:.0f}%)")
            return dec
        r1 = abs(sig.tp1 - entry) * qty * 0.5
        r2 = abs(sig.tp2 - entry) * qty * 0.5
        net_rr = (r1 + r2 - cost) / actual_risk
        if net_rr < L.min_reward_risk:
            dec.reasons.append(f"net reward/risk {net_rr:.2f} < {L.min_reward_risk}")
            return dec
        return SizingDecision(True, qty=qty, leverage=int(lev), notional=notional, risk_amount=actual_risk,
                              risk_fraction=frac, expected_cost=cost, net_reward_risk=net_rr,
                              adjustments=dec.adjustments)


def feasibility_report(equity_usdt: float, price: float, stop_pct: float, rules: SymbolRules,
                       limits: RiskLimits, fees: FeeModel) -> dict[str, Any]:
    """Explains, with arithmetic only, whether a trade is possible at this
    capital. Used for EXPERIMENTAL MODE (4 EUR)."""
    risk = equity_usdt * limits.risk_per_trade
    min_notional = max(rules.min_notional, rules.min_qty * price)
    max_notional_by_risk = risk / (stop_pct + 2 * fees.taker + 2 * fees.slippage_bps / 1e4)
    cost_at_min = min_notional * (2 * fees.taker + 2 * fees.slippage_bps / 1e4)
    lev_needed = min_notional / max(1e-9, equity_usdt * 0.9)
    return {
        "equity_usdt": equity_usdt,
        "risk_per_trade_usdt": risk,
        "exchange_min_notional": min_notional,
        "max_notional_allowed_by_risk": max_notional_by_risk,
        "feasible": max_notional_by_risk >= min_notional and cost_at_min <= limits.max_fee_to_risk * risk,
        "fees_at_min_notional": cost_at_min,
        "fees_as_pct_of_risk": cost_at_min / risk * 100 if risk > 0 else float("inf"),
        "leverage_needed_for_min_notional": lev_needed,
        "max_stop_pct_at_min_notional": max(0.0, risk / min_notional - 2 * fees.taker - 2 * fees.slippage_bps / 1e4) * 100,
        "doublings_needed_wins_in_a_row_at_1R": math.log(2) / math.log(1 + limits.risk_per_trade),
    }


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
