"""Hard-coded portfolio risk engine.

This module is deliberately pure (no DB, no network, no AI) so it can be exhaustively
tested and cannot be bypassed by an AI decision. AI agents may only *tighten* risk through
`RiskOverlay` (e.g. a news blackout); nothing an agent produces can raise a limit.

Two-step evaluation for every new position:
  1. `pre_trade(intent, state)`  -> risk budget for this trade (account ccy) or rejection
  2. `post_size(sized, state)`   -> final veto after sizing (actual risk, leverage, margin,
                                    portfolio risk, per-trader risk, currency exposure)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

# Ceilings: the defaults mandated for this system. Users may only tighten them.
CEILINGS = {
    "risk_per_trade_pct": 0.5,
    "daily_loss_pct": 2.0,
    "weekly_loss_pct": 4.0,
    "max_drawdown_pct": 10.0,
    "max_portfolio_risk_pct": 1.5,
    "max_trader_risk_pct": 0.5,
    "max_currency_exposure_pct": 1.0,
    "max_effective_leverage": 10.0,
    "max_margin_usage_pct": 35.0,
    "max_open_positions": 6,
    "max_positions_per_symbol": 2,
    "max_sl_distance_pct": 10.0,
}


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.5
    daily_loss_pct: float = 2.0
    weekly_loss_pct: float = 4.0
    max_drawdown_pct: float = 10.0
    max_portfolio_risk_pct: float = 1.5
    max_trader_risk_pct: float = 0.5
    max_currency_exposure_pct: float = 1.0
    max_effective_leverage: float = 10.0
    max_margin_usage_pct: float = 35.0
    max_open_positions: int = 6
    max_positions_per_symbol: int = 2
    max_sl_distance_pct: float = 10.0
    require_stop_loss: bool = True
    # Drawdown-based de-risking: (drawdown %, multiplier) - applied to the per-trade budget.
    drawdown_steps: tuple = ((3.0, 0.75), (5.0, 0.5), (7.5, 0.25))
    # Volatility adjustment: scale down when current volatility exceeds its baseline.
    vol_ratio_threshold: float = 1.3
    min_vol_multiplier: float = 0.4

    @classmethod
    def for_market(cls, market: str, overrides: dict | None = None) -> "RiskLimits":
        base = cls(max_effective_leverage=10.0 if market == "forex" else 3.0,
                   max_sl_distance_pct=3.0 if market == "forex" else 10.0)
        return base.tightened(overrides or {})

    def tightened(self, overrides: dict) -> "RiskLimits":
        """Apply user overrides, clamped so that no value exceeds its ceiling."""
        data = asdict(self)
        names = {f.name for f in fields(self)}
        for k, v in overrides.items():
            if k not in names or v is None:
                continue
            if k in CEILINGS:
                v = type(data[k])(min(float(v), float(CEILINGS[k]), float(data[k])))
                if float(v) <= 0:
                    raise ValueError(f"{k} must be positive")
            elif k == "require_stop_loss":
                v = True  # can never be disabled
            data[k] = v
        data["drawdown_steps"] = tuple(tuple(x) for x in data["drawdown_steps"])
        return RiskLimits(**data)


@dataclass
class OpenExposure:
    symbol: str
    side: str
    risk_amount: float       # loss at SL (>= 0); 0 when SL is at/through break-even
    notional: float          # account ccy
    margin: float            # account ccy
    trader_id: int | None
    currency_legs: dict[str, float] = field(default_factory=dict)  # forex only


@dataclass
class PortfolioState:
    market: str
    equity: float
    balance: float
    day_start_equity: float
    week_start_equity: float
    high_water_mark: float
    status: str = "active"
    copy_enabled: bool = True
    global_kill_switch: bool = False
    exposures: list[OpenExposure] = field(default_factory=list)

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (self.high_water_mark - self.equity) / self.high_water_mark * 100) if self.high_water_mark else 0.0

    @property
    def daily_loss_pct(self) -> float:
        return max(0.0, (self.day_start_equity - self.equity) / self.day_start_equity * 100) if self.day_start_equity else 0.0

    @property
    def weekly_loss_pct(self) -> float:
        return max(0.0, (self.week_start_equity - self.equity) / self.week_start_equity * 100) if self.week_start_equity else 0.0

    @property
    def open_risk(self) -> float:
        return sum(e.risk_amount for e in self.exposures)

    @property
    def margin_used(self) -> float:
        return sum(e.margin for e in self.exposures)

    @property
    def notional(self) -> float:
        return sum(e.notional for e in self.exposures)


@dataclass
class RiskOverlay:
    """Tighten-only adjustments produced by AI agents or the economic calendar."""
    blocked_currencies: dict[str, str] = field(default_factory=dict)   # ccy -> reason
    blocked_symbols: dict[str, str] = field(default_factory=dict)
    blocked_traders: dict[int, str] = field(default_factory=dict)
    risk_multiplier: float = 1.0          # clamped to (0, 1]
    reason: str | None = None


@dataclass
class TradeIntent:
    symbol: str
    side: str            # buy / sell
    entry: float
    sl: float | None
    trader_id: int | None
    trader_max_risk_pct: float = 0.5
    trader_capital_fraction: float = 1.0
    vol_ratio: float | None = None       # current ATR / baseline ATR, when known
    currencies: tuple[str, ...] = ()     # forex: (base, quote)


@dataclass
class SizedTrade:
    intent: TradeIntent
    risk_amount: float
    notional: float
    margin: float
    currency_legs: dict[str, float] = field(default_factory=dict)


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    budget: float = 0.0
    multipliers: dict[str, float] = field(default_factory=dict)
    checks: dict[str, object] = field(default_factory=dict)

    def reject(self, reason: str) -> "RiskDecision":
        self.approved = False
        self.reasons.append(reason)
        return self


EPS = 1e-9


def portfolio_gate(state: PortfolioState, limits: RiskLimits) -> list[str]:
    """Reasons why *no* new position may be opened at all (empty list == OK)."""
    r = []
    if state.global_kill_switch:
        r.append("global emergency stop is active")
    if state.status != "active":
        r.append(f"portfolio status is {state.status}")
    if not state.copy_enabled:
        r.append("automatic copying is disabled for this portfolio")
    if state.equity <= 0:
        r.append("equity is not positive")
    if state.daily_loss_pct >= limits.daily_loss_pct - EPS:
        r.append(f"daily loss {state.daily_loss_pct:.2f}% reached limit {limits.daily_loss_pct}%")
    if state.weekly_loss_pct >= limits.weekly_loss_pct - EPS:
        r.append(f"weekly loss {state.weekly_loss_pct:.2f}% reached limit {limits.weekly_loss_pct}%")
    if state.drawdown_pct >= limits.max_drawdown_pct - EPS:
        r.append(f"drawdown {state.drawdown_pct:.2f}% reached maximum {limits.max_drawdown_pct}%")
    return r


def pre_trade(intent: TradeIntent, state: PortfolioState, limits: RiskLimits,
              overlay: RiskOverlay | None = None) -> RiskDecision:
    d = RiskDecision(approved=True)
    overlay = overlay or RiskOverlay()
    for reason in portfolio_gate(state, limits):
        d.reject(reason)
    if intent.side not in ("buy", "sell"):
        d.reject(f"invalid side {intent.side!r}")
    if intent.entry is None or intent.entry <= 0:
        d.reject("invalid entry price")
    # --- stop-loss validation -------------------------------------------------------
    if intent.sl is None or intent.sl <= 0:
        d.reject("missing stop-loss (trades without a valid stop-loss are rejected)")
    elif intent.entry and intent.entry > 0:
        wrong_side = (intent.side == "buy" and intent.sl >= intent.entry) or \
                     (intent.side == "sell" and intent.sl <= intent.entry)
        if wrong_side:
            d.reject("stop-loss is on the wrong side of the entry price")
        dist_pct = abs(intent.entry - intent.sl) / intent.entry * 100
        d.checks["sl_distance_pct"] = round(dist_pct, 4)
        if dist_pct > limits.max_sl_distance_pct:
            d.reject(f"stop-loss distance {dist_pct:.2f}% exceeds {limits.max_sl_distance_pct}%")
    # --- position count -------------------------------------------------------------
    if len(state.exposures) >= limits.max_open_positions:
        d.reject(f"maximum open positions ({limits.max_open_positions}) reached")
    same = [e for e in state.exposures if e.symbol == intent.symbol]
    if len(same) >= limits.max_positions_per_symbol:
        d.reject(f"maximum positions on {intent.symbol} ({limits.max_positions_per_symbol}) reached")
    # No averaging down: never add to a symbol where an existing same-side position is losing
    # money with risk still on (its SL is not yet at break-even).
    if any(e.side == intent.side and e.risk_amount > 0 for e in same) and intent.trader_id is not None:
        if any(e.trader_id == intent.trader_id and e.side == intent.side and e.risk_amount > 0 for e in same):
            d.reject("adding to an open at-risk position of the same trader (averaging/pyramiding blocked)")
    # --- overlays (AI / calendar: tighten-only) ---------------------------------------
    if intent.trader_id is not None and intent.trader_id in overlay.blocked_traders:
        d.reject(f"trader blocked: {overlay.blocked_traders[intent.trader_id]}")
    if intent.symbol in overlay.blocked_symbols:
        d.reject(f"symbol blocked: {overlay.blocked_symbols[intent.symbol]}")
    for c in intent.currencies:
        if c in overlay.blocked_currencies:
            d.reject(f"{c} blocked: {overlay.blocked_currencies[c]}")
    if not d.approved:
        return d
    # --- budget ---------------------------------------------------------------------
    base = state.equity * limits.risk_per_trade_pct / 100
    m_dd = 1.0
    for threshold, mult in sorted(limits.drawdown_steps):
        if state.drawdown_pct >= threshold:
            m_dd = min(m_dd, mult)
    m_vol = 1.0
    if intent.vol_ratio and intent.vol_ratio > limits.vol_ratio_threshold:
        m_vol = max(limits.min_vol_multiplier, limits.vol_ratio_threshold / intent.vol_ratio)
    m_ai = min(1.0, max(0.0, overlay.risk_multiplier))
    m_alloc = min(1.0, max(0.0, intent.trader_capital_fraction))
    budget = base * m_dd * m_vol * m_ai * m_alloc
    # Cannot exceed remaining headroom (portfolio, trader, daily loss).
    headroom = {
        "portfolio": state.equity * limits.max_portfolio_risk_pct / 100 - state.open_risk,
        "trader": state.equity * min(intent.trader_max_risk_pct, limits.max_trader_risk_pct) / 100
        - sum(e.risk_amount for e in state.exposures if e.trader_id == intent.trader_id),
        # a stop-out must not be able to breach the daily loss limit
        "daily": state.day_start_equity * limits.daily_loss_pct / 100
        - (state.day_start_equity - state.equity) - state.open_risk,
    }
    d.checks["headroom"] = {k: round(v, 6) for k, v in headroom.items()}
    budget = min(budget, *headroom.values())
    d.multipliers = {"drawdown": m_dd, "volatility": round(m_vol, 4), "ai_overlay": m_ai, "allocation": m_alloc}
    if budget <= EPS:
        tight = min(headroom, key=headroom.get)
        return d.reject(f"no risk budget left ({tight} limit)")
    d.budget = budget
    return d


def post_size(t: SizedTrade, state: PortfolioState, limits: RiskLimits, budget: float) -> RiskDecision:
    d = RiskDecision(approved=True, budget=budget)
    eq = state.equity
    if t.risk_amount <= 0:
        d.reject("sized risk is zero")
    if t.risk_amount > budget * 1.0001:
        d.reject(f"sized risk {t.risk_amount:.4f} exceeds budget {budget:.4f}")
    if t.risk_amount > eq * limits.risk_per_trade_pct / 100 * 1.0001:
        d.reject("risk per trade above limit")
    port = state.open_risk + t.risk_amount
    d.checks["portfolio_risk_pct"] = round(port / eq * 100, 4)
    if port > eq * limits.max_portfolio_risk_pct / 100 * 1.0001:
        d.reject(f"portfolio risk {port / eq * 100:.2f}% would exceed {limits.max_portfolio_risk_pct}%")
    trader_risk = t.risk_amount + sum(e.risk_amount for e in state.exposures if e.trader_id == t.intent.trader_id)
    cap = min(t.intent.trader_max_risk_pct, limits.max_trader_risk_pct)
    if trader_risk > eq * cap / 100 * 1.0001:
        d.reject(f"trader risk {trader_risk / eq * 100:.2f}% would exceed {cap}%")
    lev = (state.notional + t.notional) / eq
    d.checks["effective_leverage"] = round(lev, 4)
    if lev > limits.max_effective_leverage:
        d.reject(f"effective leverage {lev:.2f}x would exceed {limits.max_effective_leverage}x")
    mu = (state.margin_used + t.margin) / eq * 100
    d.checks["margin_usage_pct"] = round(mu, 4)
    if mu > limits.max_margin_usage_pct:
        d.reject(f"margin usage {mu:.1f}% would exceed {limits.max_margin_usage_pct}%")
    if t.currency_legs:
        totals: dict[str, float] = {}
        for e in state.exposures:
            for c, v in e.currency_legs.items():
                totals[c] = totals.get(c, 0.0) + v
        for c, v in t.currency_legs.items():
            totals[c] = totals.get(c, 0.0) + v
        worst = max(totals.items(), key=lambda kv: abs(kv[1]))
        d.checks["currency_exposure_pct"] = {c: round(v / eq * 100, 4) for c, v in totals.items()}
        limit = eq * limits.max_currency_exposure_pct / 100
        for c, v in t.currency_legs.items():
            # Only block when this trade *increases* concentration in a currency beyond the limit.
            if abs(totals[c]) > limit * 1.0001 and abs(totals[c]) > abs(totals[c] - v):
                d.reject(f"correlated {c} exposure {totals[c] / eq * 100:+.2f}% would exceed "
                         f"±{limits.max_currency_exposure_pct}%")
        d.checks["worst_currency"] = worst[0]
    return d


@dataclass
class PortfolioAction:
    new_status: str | None
    reason: str | None
    notify_kind: str | None


def evaluate_portfolio(state: PortfolioState, limits: RiskLimits) -> PortfolioAction:
    """Status transitions driven by realised + floating losses (called by the risk monitor)."""
    if state.status == "review_required":
        return PortfolioAction(None, None, None)
    if state.drawdown_pct >= limits.max_drawdown_pct - EPS:
        return PortfolioAction("review_required",
                               f"maximum drawdown {state.drawdown_pct:.2f}% >= {limits.max_drawdown_pct}%: "
                               "trading stopped, copying disabled, manual review required", "max_drawdown")
    if state.status in ("active",) and state.weekly_loss_pct >= limits.weekly_loss_pct - EPS:
        return PortfolioAction("halted_weekly", f"weekly loss {state.weekly_loss_pct:.2f}% reached "
                                                f"{limits.weekly_loss_pct}%", "weekly_loss_limit")
    if state.status in ("active",) and state.daily_loss_pct >= limits.daily_loss_pct - EPS:
        return PortfolioAction("halted_daily", f"daily loss {state.daily_loss_pct:.2f}% reached "
                                               f"{limits.daily_loss_pct}%", "daily_loss_limit")
    return PortfolioAction(None, None, None)


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
