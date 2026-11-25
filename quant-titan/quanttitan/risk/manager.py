"""AGENT 5 — Risk Manager (veto authority).

Every proposed trade passes :meth:`RiskManager.review`. The review runs *all* checks (so the
dashboard can show every reason) and approves only if every check passes. An approval carries a
signed, single-use token (see ``approval.py``) that the execution layer verifies before sending any
exposure-increasing order. No other component can create such a token, so no other agent can
override a veto.

Position sizing (risk-based, never fixed size):
    budget     = equity × risk_per_trade% × regime_mult × engine_mult × drawdown_mult   (all mults ≤ 1)
    loss/unit  = |entry − stop| + entry × (2·taker_fee + 2·slippage_bps/1e4)
    qty        = floor_to_step(budget / loss/unit)
Losses never increase size: drawdown_mult only scales *down* as drawdown grows (no martingale).

Isolated-margin liquidation estimate (first maintenance bracket, mmr):
    long  liq ≈ entry × (1 − 1/L + mmr)        short liq ≈ entry × (1 + 1/L − mmr)
Leverage L is the lowest integer that fits the margin into available balance, and must satisfy
liquidation distance ≥ min_liquidation_distance_mult × stop distance and L ≤ max_leverage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import AppConfig, DirectionMode
from ..core.logging import get_logger
from ..core.types import ContractSpec, Decision, Direction
from ..orchestrator.costs import slippage_bps
from .approval import ApprovalSigner
from .breakers import CircuitBreakers
from .portfolio import PortfolioState

log = get_logger("risk")


@dataclass
class MarketCheck:
    """Live market facts the risk manager needs (all optional; unknown = not checked, noted)."""

    spread_bps: float | None = None
    funding_rate: float = 0.0
    data_age_s: float = 0.0
    bar_quote_volume: float = 0.0
    atr: float = 0.0
    regime_multiplier: float = 1.0
    price: float = 0.0


@dataclass
class RiskVerdict:
    approved: bool
    qty: float = 0.0
    notional: float = 0.0
    risk_usdt: float = 0.0
    budget_usdt: float = 0.0
    leverage: float = 1.0
    liquidation_price: float = 0.0
    checks: dict[str, dict] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    approval: dict | None = None

    def fail(self, name: str, detail: str) -> None:
        self.checks[name] = {"ok": False, "detail": detail}
        self.reasons.append(detail)

    def ok(self, name: str, detail: str = "") -> None:
        self.checks[name] = {"ok": True, "detail": detail}

    def to_dict(self) -> dict:
        return {"approved": self.approved, "qty": self.qty, "notional": self.notional, "risk_usdt": self.risk_usdt,
                "budget_usdt": self.budget_usdt, "leverage": self.leverage, "liquidation_price": self.liquidation_price,
                "checks": self.checks, "reasons": self.reasons}


def liquidation_price(entry: float, direction: Direction, leverage: float, mmr: float) -> float:
    if direction is Direction.LONG:
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


class RiskManager:
    name = "risk_manager"
    title = "Risk Manager"
    responsibilities = ["Validate every proposed trade", "Size positions from stop distance and risk budget",
                        "Enforce exposure, leverage, correlation and loss limits", "Operate circuit breakers",
                        "Emergency shutdown", "Veto authority over all agents"]

    def __init__(self, cfg: AppConfig, breakers: CircuitBreakers | None = None, signer: ApprovalSigner | None = None) -> None:
        self.cfg = cfg
        self.breakers = breakers or CircuitBreakers(cfg.risk)
        self.signer = signer or ApprovalSigner()
        self.vetoes = 0
        self.approvals = 0
        self.last_verdicts: list[dict] = []

    def update_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.breakers.cfg = cfg.risk

    # ------------------------------------------------------------------ helpers
    def drawdown_multiplier(self, pf: PortfolioState) -> float:
        r = self.cfg.risk
        dd = pf.drawdown_pct()
        if dd <= r.drawdown_derisk_start_pct:
            return 1.0
        span = max(r.monthly_drawdown_limit_pct - r.drawdown_derisk_start_pct, 1e-9)
        return max(r.drawdown_derisk_floor, 1.0 - (dd - r.drawdown_derisk_start_pct) / span * (1 - r.drawdown_derisk_floor))

    def engine_multiplier(self, engine: str) -> float:
        ec = getattr(self.cfg.engines, engine, None)
        return float(getattr(ec, "risk_multiplier", 1.0)) if ec is not None else 1.0

    def size(self, *, equity: float, entry: float, stop: float, spec: ContractSpec, atr: float,
             regime_mult: float = 1.0, engine: str = "", pf: PortfolioState | None = None,
             bar_quote_volume: float = 0.0, budget_override: float | None = None) -> tuple[float, float, float]:
        """Return (qty, budget_usdt, actual_risk_usdt incl. costs)."""
        r = self.cfg.risk
        mult = min(1.0, regime_mult) * min(1.0, self.engine_multiplier(engine))
        if pf is not None:
            mult *= self.drawdown_multiplier(pf)
        budget = budget_override if budget_override is not None else equity * r.risk_per_trade_pct / 100 * mult
        slip = slippage_bps(self.cfg.costs, entry, atr, budget * 20, bar_quote_volume) / 1e4
        per_unit = abs(entry - stop) + entry * (2 * self.cfg.costs.taker_fee + 2 * slip)
        if per_unit <= 0 or budget <= 0:
            return 0.0, budget, 0.0
        qty = spec.round_qty(budget / per_unit)
        return qty, budget, qty * per_unit

    # ------------------------------------------------------------------ review
    def review(self, d: Decision, pf: PortfolioState, spec: ContractSpec, mkt: MarketCheck, *,
               trading_paused: bool = False, pair_trade: bool = False, budget_override: float | None = None) -> RiskVerdict:
        cfg, r = self.cfg, self.cfg.risk
        v = RiskVerdict(False)
        now = d.ts or None
        active = self.breakers.evaluate(pf, now)
        direction = d.direction
        entry, stop = d.entry, d.stop

        if "EMERGENCY_STOP" in active:
            v.fail("emergency_stop", "emergency stop active")
        else:
            v.ok("emergency_stop")
        others = [a for a in active if a != "EMERGENCY_STOP"]
        if others:
            v.fail("circuit_breakers", "circuit breaker active: " + ", ".join(others))
        else:
            v.ok("circuit_breakers")
        if trading_paused:
            v.fail("paused", "trading paused by user")
        dm = cfg.system.direction
        if (dm is DirectionMode.LONG and direction is Direction.SHORT) or (dm is DirectionMode.SHORT and direction is Direction.LONG):
            v.fail("direction_mode", f"{direction.value} not allowed in direction mode {dm.value}")
        if mkt.data_age_s > r.max_data_staleness_s:
            v.fail("data_freshness", f"market data {mkt.data_age_s:.0f}s old > {r.max_data_staleness_s:.0f}s")
        else:
            v.ok("data_freshness", f"{mkt.data_age_s:.0f}s")
        equity = pf.equity
        if equity < r.min_equity_usdt:
            v.fail("min_equity", f"equity {equity:.2f} < minimum {r.min_equity_usdt}")
        # stop validity
        if direction is Direction.FLAT or entry <= 0 or stop <= 0 or (entry - stop) * direction.sign <= 0:
            v.fail("stop_loss", "missing or wrong-side stop-loss")
            return self._finish(v, d)
        stop_pct = abs(entry - stop) / entry * 100
        if stop_pct > r.max_stop_distance_pct:
            v.fail("stop_loss", f"stop distance {stop_pct:.2f}% > {r.max_stop_distance_pct}%")
        else:
            v.ok("stop_loss", f"stop {stop_pct:.2f}% away")
        # market quality
        if mkt.spread_bps is not None and math.isfinite(mkt.spread_bps):
            if mkt.spread_bps > r.max_spread_bps:
                v.fail("spread", f"spread {mkt.spread_bps:.1f}bps > {r.max_spread_bps}bps")
            else:
                v.ok("spread", f"{mkt.spread_bps:.1f}bps")
        else:
            v.ok("spread", "spread unknown (bar data) — modelled in costs")
        pays = (direction is Direction.LONG and mkt.funding_rate > 0) or (direction is Direction.SHORT and mkt.funding_rate < 0)
        if pays and abs(mkt.funding_rate) > r.max_abs_funding_rate:
            v.fail("funding", f"{direction.value} pays funding {mkt.funding_rate:.4%} > {r.max_abs_funding_rate:.4%}")
        else:
            v.ok("funding", f"{mkt.funding_rate:.4%}")
        # engine-level limits
        eng = d.engine
        ecfg = getattr(cfg.engines, eng, None)
        if eng in pf.suspended_engines:
            v.fail("engine_suspended", f"engine {eng} is suspended")
        if ecfg is not None:
            if not ecfg.enabled:
                v.fail("engine_enabled", f"engine {eng} disabled")
            open_eng = sum(1 for p in pf.positions if p.engine == eng)
            if open_eng >= ecfg.max_open_positions:
                v.fail("engine_positions", f"{eng} already has {open_eng} open positions (max {ecfg.max_open_positions})")
            if -pf.engine_day_r.get(eng, 0.0) >= ecfg.daily_loss_limit_r:
                v.fail("engine_daily_loss", f"{eng} lost {-pf.engine_day_r[eng]:.2f}R today (limit {ecfg.daily_loss_limit_r}R)")
        # sizing
        regime_mult = mkt.regime_multiplier if mkt.regime_multiplier > 0 else 0.0
        if regime_mult <= 0:
            v.fail("regime", "regime risk multiplier is 0 (unsafe/unknown market)")
        qty, budget, risk_usdt = self.size(equity=equity, entry=entry, stop=stop, spec=spec, atr=mkt.atr or abs(entry - stop),
                                           regime_mult=max(regime_mult, 1e-9), engine=eng, pf=pf,
                                           bar_quote_volume=mkt.bar_quote_volume, budget_override=budget_override)
        v.budget_usdt, v.qty, v.risk_usdt = budget, qty, risk_usdt
        notional = qty * entry
        v.notional = notional
        if qty <= 0 or qty < spec.min_qty:
            v.fail("position_size", f"risk budget {budget:.2f} USDT buys {qty} < exchange minimum {spec.min_qty} — trade not feasible")
        elif notional < spec.min_notional:
            v.fail("position_size", f"notional {notional:.2f} < exchange minimum {spec.min_notional}")
        elif qty > spec.max_qty:
            v.fail("position_size", f"qty {qty} > exchange maximum {spec.max_qty}")
        else:
            v.ok("position_size", f"qty {qty} ({notional:.2f} USDT), risk {risk_usdt:.2f} of budget {budget:.2f}")
        if mkt.bar_quote_volume > 0 and notional > cfg.costs.max_bar_participation * mkt.bar_quote_volume:
            v.fail("liquidity", f"order {notional:.0f} USDT > {cfg.costs.max_bar_participation:.1%} of bar volume")
        # portfolio limits
        positions = [p for p in pf.positions if not (pair_trade and p.group_id and p.group_id == d.id)]
        if len(positions) >= r.max_concurrent_positions:
            v.fail("max_positions", f"{len(positions)} open positions (max {r.max_concurrent_positions})")
        if any(p.symbol == d.symbol for p in positions):
            v.fail("symbol_position", f"a position on {d.symbol} is already open (one position per symbol)")
        open_risk = pf.open_risk()
        if open_risk + risk_usdt > equity * r.max_open_risk_pct / 100 + 1e-9:
            v.fail("open_risk", f"open risk {open_risk:.2f} + {risk_usdt:.2f} > {r.max_open_risk_pct}% of equity")
        else:
            v.ok("open_risk", f"{(open_risk + risk_usdt) / equity * 100:.2f}% of equity")
        if pf.symbol_notional(d.symbol) + notional > equity * r.max_symbol_exposure_pct / 100:
            v.fail("symbol_exposure", f"{d.symbol} exposure would exceed {r.max_symbol_exposure_pct}% of equity")
        gross = pf.gross_notional() + notional
        if gross > equity * r.max_leverage:
            v.fail("gross_leverage", f"gross exposure {gross / equity:.2f}x > {r.max_leverage}x")
        else:
            v.ok("gross_leverage", f"{gross / equity:.2f}x")
        corr_risk = pf.correlated_risk(d.symbol, direction, r.correlation_threshold)
        if corr_risk + risk_usdt > equity * r.max_correlated_risk_pct / 100 + 1e-9:
            v.fail("correlation", f"correlated same-direction risk {corr_risk + risk_usdt:.2f} > {r.max_correlated_risk_pct}% of equity")
        else:
            v.ok("correlation", f"correlated risk {(corr_risk + risk_usdt) / equity * 100:.2f}%")
        # leverage & liquidation
        avail = max(pf.balance - sum(p.notional(pf.prices.get(p.symbol, p.entry_price)) / max(p.leverage, 1) for p in pf.positions), 0.0)
        lev = max(1.0, math.ceil(notional / avail)) if avail > 0 and notional > 0 else float("inf")
        cap = min(r.max_leverage, spec.max_leverage)
        if lev > cap:
            v.fail("leverage", f"required leverage {lev:.0f}x > cap {cap:.0f}x (available margin {avail:.2f})")
        else:
            liq = liquidation_price(entry, direction, lev, spec.maint_margin_rate)
            liq_dist, stop_dist = abs(entry - liq), abs(entry - stop)
            v.leverage, v.liquidation_price = lev, liq
            if liq_dist < r.min_liquidation_distance_mult * stop_dist:
                v.fail("liquidation", f"liquidation {liq:.6g} only {liq_dist / stop_dist:.1f}× stop distance (min {r.min_liquidation_distance_mult}×)")
            else:
                v.ok("liquidation", f"{lev:.0f}x isolated, liq {liq:.6g} = {liq_dist / stop_dist:.1f}× stop distance")
        return self._finish(v, d)

    def _finish(self, v: RiskVerdict, d: Decision) -> RiskVerdict:
        v.approved = not v.reasons
        if v.approved:
            self.approvals += 1
            side = "BUY" if d.direction is Direction.LONG else "SELL"
            stop_pct = abs(d.entry - d.stop) / d.entry * 100
            v.approval = self.signer.issue(decision_id=d.id, symbol=d.symbol, side=side, max_qty=v.qty, ref_price=d.entry,
                                           max_deviation_pct=self.cfg.execution.max_entry_drift_r * stop_pct,
                                           ttl_ms=max(self.cfg.orchestrator.semi_auto_ttl_s, 120) * 1000)
        else:
            self.vetoes += 1
            log.info("VETO %s %s %s: %s", d.symbol, d.engine, d.direction.value, "; ".join(v.reasons))
        self.last_verdicts.append({"decision_id": d.id, "symbol": d.symbol, "approved": v.approved, "reasons": v.reasons[:5], "ts": d.ts})
        self.last_verdicts = self.last_verdicts[-200:]
        return v

    def report(self, pf: PortfolioState) -> dict:
        r = self.cfg.risk
        eq = pf.equity
        return {
            "agent": self.name, "approvals": self.approvals, "vetoes": self.vetoes,
            "breakers": self.breakers.to_dict(),
            "utilisation": {
                "open_risk_pct": round(pf.open_risk() / eq * 100, 3) if eq else 0, "open_risk_limit_pct": r.max_open_risk_pct,
                "daily_loss_pct": round(pf.daily_loss_pct(), 3), "daily_limit_pct": r.daily_loss_limit_pct,
                "weekly_loss_pct": round(pf.weekly_loss_pct(), 3), "weekly_limit_pct": r.weekly_loss_limit_pct,
                "monthly_dd_pct": round(pf.monthly_drawdown_pct(), 3), "monthly_limit_pct": r.monthly_drawdown_limit_pct,
                "gross_leverage": round(pf.gross_notional() / eq, 3) if eq else 0, "max_leverage": r.max_leverage,
                "positions": len(pf.positions), "max_positions": r.max_concurrent_positions,
                "consecutive_losses": pf.consecutive_losses, "max_consecutive_losses": r.max_consecutive_losses,
                "drawdown_multiplier": round(self.drawdown_multiplier(pf), 3),
            },
            "recent_verdicts": self.last_verdicts[-20:],
        }
