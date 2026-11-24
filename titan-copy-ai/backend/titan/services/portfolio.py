"""Portfolio state assembly: venue account snapshot + persisted positions -> risk engine input."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.base import AccountSnapshot, ExecutionAdapter
from ..markets import crypto as cm
from ..markets import forex as fx
from ..models import Portfolio, Position, PositionStatus, TradingMode
from ..risk.engine import OpenExposure, PortfolioState, RiskLimits
from . import state as st


def risk_at_stop(adapter: ExecutionAdapter, symbol: str, side: str, qty: float, entry: float, sl: float | None) -> float:
    """Loss (account ccy, >= 0) if the stop is hit. Missing SL => whole notional at risk."""
    if adapter.market == "forex":
        sp = fx.spec(symbol)
        vpu = fx.value_per_price_unit(sp, qty, adapter.account_currency, adapter.mids())
        if sl is None:
            return vpu * entry
        loss = (entry - sl) if side == "buy" else (sl - entry)
        return max(0.0, loss * vpu)
    product = getattr(adapter, "product", cm.USDM)
    if sl is None:
        return qty * entry
    if product == cm.COINM:
        f = adapter.filters.get(symbol)
        cs = float(f.contract_size) if f else 100.0
        return max(0.0, -cm.pnl_usd(cm.COINM, side, qty, entry, sl, cs))
    return max(0.0, (entry - sl) * qty if side == "buy" else (sl - entry) * qty)


def notional_margin(adapter: ExecutionAdapter, symbol: str, qty: float, price: float) -> tuple[float, float]:
    if adapter.market == "forex":
        sp = fx.spec(symbol)
        mids = adapter.mids()
        n = fx.convert(sp.contract_size * qty * price, sp.quote, adapter.account_currency, mids)
        return n, fx.margin_required(sp, qty, price, adapter.account_currency, mids)
    product = getattr(adapter, "product", cm.USDM)
    if product == cm.COINM:
        f = adapter.filters.get(symbol)
        n = qty * (float(f.contract_size) if f else 100.0)
    else:
        n = qty * price
    lev = 1.0 if product == cm.SPOT else float(getattr(adapter, "max_leverage", 3) or 3)
    return n, n / lev


def periods(s: Session, p: Portfolio, equity: float, now: datetime | None = None) -> dict:
    """Day/week start equity (UTC day, ISO week). Rolls over automatically."""
    now = now or datetime.now(timezone.utc)
    key = f"portfolio:{p.id}:periods"
    d = st.get(s, key, {})
    day, week = now.date().isoformat(), f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    changed = False
    if d.get("day") != day:
        d.update(day=day, day_start_equity=equity)
        changed = True
    if d.get("week") != week:
        d.update(week=week, week_start_equity=equity)
        changed = True
    if changed:
        st.put(s, key, d)
    d["rolled_day"] = changed
    return d


def limits_for(p: Portfolio) -> RiskLimits:
    return RiskLimits.for_market(p.market.value, p.risk_limits or {})


def build_state(s: Session, p: Portfolio, adapter: ExecutionAdapter, snap: AccountSnapshot | None = None) -> tuple[PortfolioState, AccountSnapshot]:
    snap = snap or adapter.account()
    if p.mode != TradingMode.PAPER and s.scalar(select(Position.id).where(Position.portfolio_id == p.id).limit(1)) is None:
        # A venue account that has not copied anything yet: its starting capital is the real account equity
        # (demo balances, deposits), so P&L and drawdown are measured from what the account actually holds.
        if abs((p.initial_capital or 0) - snap.equity) > 1e-6:
            p.initial_capital = snap.equity
            p.high_water_mark = snap.equity
            st.put(s, f"portfolio:{p.id}:periods", {})
    per = periods(s, p, snap.equity)
    if snap.equity > (p.high_water_mark or 0):
        p.high_water_mark = snap.equity
    exposures = []
    for pos in s.scalars(select(Position).where(Position.portfolio_id == p.id, Position.status == PositionStatus.OPEN)):
        risk = risk_at_stop(adapter, pos.symbol, pos.side, pos.quantity, pos.entry_price, pos.sl)
        n, m = notional_margin(adapter, pos.symbol, pos.quantity, pos.entry_price)
        legs = fx.currency_legs(pos.symbol, pos.side, risk) if adapter.market == "forex" else {}
        exposures.append(OpenExposure(pos.symbol, pos.side, risk, n, m, pos.trader_id, legs))
    state = PortfolioState(market=p.market.value, equity=snap.equity, balance=snap.balance,
                           day_start_equity=per["day_start_equity"], week_start_equity=per["week_start_equity"],
                           high_water_mark=p.high_water_mark, status=p.status.value, copy_enabled=p.copy_enabled,
                           global_kill_switch=st.kill_switch_active(s), exposures=exposures)
    return state, snap


def ny_rollover(now: datetime | None = None) -> datetime:
    """Next 17:00 America/New_York (forex daily rollover) in UTC."""
    from zoneinfo import ZoneInfo

    now = now or datetime.now(timezone.utc)
    ny = now.astimezone(ZoneInfo("America/New_York"))
    roll = ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if ny >= roll:
        roll += timedelta(days=1)
    return roll.astimezone(timezone.utc)


def minutes_to_rollover(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (ny_rollover(now) - now).total_seconds() / 60
