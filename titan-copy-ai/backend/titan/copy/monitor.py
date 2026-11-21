"""Portfolio risk monitor + emergency controls."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..audit import audit
from ..db import session_scope
from ..events import emit
from ..models import Allocation, EquitySnapshot, Portfolio, PortfolioStatus, Position, PositionStatus, RiskEvent
from ..notify import notify
from ..risk.engine import evaluate_portfolio
from ..services import state as st
from ..services.portfolio import build_state, limits_for, minutes_to_rollover
from .engine import CopyEngine, client_id

log = logging.getLogger("titan.monitor")
AUTO_RESUME = {PortfolioStatus.HALTED_DAILY: "day", PortfolioStatus.HALTED_WEEKLY: "week"}


def monitor_portfolio(engine: CopyEngine, pid: int, snapshot: bool = True) -> dict:
    ad = engine.adapters.get(pid)
    if ad is None:
        return {"error": "no adapter"}
    with session_scope() as s:
        p = s.get(Portfolio, pid)
        try:
            state, snap = build_state(s, p, ad)
        except Exception as e:
            _disconnected(s, p, str(e))
            return {"error": str(e)}
        _connected(s, p)
        per = st.get(s, f"portfolio:{pid}:periods")
        # automatic reset of daily / weekly halts when a new period starts
        if p.status in AUTO_RESUME and p.status_changed_at:
            changed = p.status_changed_at if p.status_changed_at.tzinfo else p.status_changed_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            new_period = (changed.date() != now.date()) if p.status == PortfolioStatus.HALTED_DAILY else \
                (changed.isocalendar()[:2] != now.isocalendar()[:2])
            if new_period:
                _set_status(s, p, PortfolioStatus.ACTIVE, f"new {AUTO_RESUME[p.status]}: limit reset")
                state.status = "active"
        limits = limits_for(p)
        action = evaluate_portfolio(state, limits)
        if action.new_status:
            new = PortfolioStatus(action.new_status)
            _set_status(s, p, new, action.reason)
            if new == PortfolioStatus.REVIEW_REQUIRED:
                p.copy_enabled = False
            title = {"daily_loss_limit": "Daily loss limit reached", "weekly_loss_limit": "Weekly loss limit reached",
                     "max_drawdown": "MAXIMUM DRAWDOWN reached - manual review required"}[action.notify_kind]
            notify(s, action.notify_kind, f"{p.name}: {title}", action.reason, severity="critical", portfolio_id=pid)
        if snapshot:
            s.add(EquitySnapshot(portfolio_id=pid, balance=snap.balance, equity=snap.equity, margin_used=snap.margin_used,
                                 unrealized=snap.equity - snap.balance, open_risk=state.open_risk,
                                 drawdown_pct=state.drawdown_pct))
        out = {"equity": snap.equity, "drawdown_pct": state.drawdown_pct, "daily_loss_pct": state.daily_loss_pct,
               "weekly_loss_pct": state.weekly_loss_pct, "open_risk": state.open_risk, "status": p.status.value,
               "day_start_equity": per.get("day_start_equity"), "market": p.market.value}
        flat = p.market.value == "forex" and (p.risk_limits or {}).get("flat_before_rollover", True)
    emit("portfolio", id=pid, **{k: v for k, v in out.items() if k != "market"})
    if flat and minutes_to_rollover() <= 10:
        out["rollover_closed"] = flatten(engine, pid, "forex_rollover_flat", actor="risk_monitor")
    return out


def _set_status(s, p: Portfolio, status: PortfolioStatus, reason: str | None):
    old = p.status
    p.status, p.status_reason, p.status_changed_at = status, reason, datetime.now(timezone.utc)
    s.add(RiskEvent(portfolio_id=p.id, kind=f"status:{status.value}", severity="critical" if status != PortfolioStatus.ACTIVE
                    else "info", detail={"from": old.value if old else None, "reason": reason}))
    audit(s, "agent:risk_management", "portfolio.status", f"portfolio:{p.id}", old=old.value if old else None,
          new=status.value, reason=reason)


def _disconnected(s, p: Portfolio, err: str):
    key = f"portfolio:{p.id}:conn"
    c = st.get(s, key, {})
    if c.get("ok", True):
        st.put(s, key, {"ok": False, "since": datetime.now(timezone.utc).isoformat(), "error": err})
        kind = "api_disconnected" if p.venue.startswith("binance") else "broker_disconnected"
        notify(s, kind, f"{p.name}: venue connection lost", err[:300], severity="critical", portfolio_id=p.id)


def _connected(s, p: Portfolio):
    key = f"portfolio:{p.id}:conn"
    c = st.get(s, key, {})
    if not c.get("ok", True):
        st.put(s, key, {"ok": True, "since": datetime.now(timezone.utc).isoformat()})
        notify(s, "system", f"{p.name}: venue connection restored", "", portfolio_id=p.id)


# ------------------------------------------------------------------ emergency controls
def set_kill_switch(active: bool, reason: str, actor: str) -> None:
    with session_scope() as s:
        st.put(s, st.KILL, {"active": active, "reason": reason, "by": actor, "at": datetime.now(timezone.utc).isoformat()})
        audit(s, actor, "emergency.kill_switch", None, active=active, reason=reason)
        notify(s, "emergency_stop" if active else "system",
               "GLOBAL EMERGENCY STOP ACTIVE" if active else "Emergency stop released", reason,
               severity="critical" if active else "info")


def flatten(engine: CopyEngine, pid: int | None, reason: str, actor: str) -> list[dict]:
    """Close every open copied position (one portfolio or all)."""
    with session_scope() as s:
        q = select(Position.id, Position.portfolio_id).where(Position.status == PositionStatus.OPEN)
        if pid is not None:
            q = q.where(Position.portfolio_id == pid)
        rows = list(s.execute(q).all())
        audit(s, actor, "emergency.flatten", f"portfolio:{pid}" if pid else "all", reason=reason, positions=len(rows))
    out = []
    for pos_id, port in rows:
        o = engine.close_position(pos_id, client_id("flat", pos_id, reason), reason=reason)
        out.append({"position_id": pos_id, "portfolio_id": port, "action": o.action, "reason": o.reason})
    return out


def stop_trader(trader_id: int, reason: str, actor: str, portfolio_id: int | None = None) -> int:
    with session_scope() as s:
        q = select(Allocation).where(Allocation.trader_id == trader_id)
        if portfolio_id:
            q = q.where(Allocation.portfolio_id == portfolio_id)
        n = 0
        for a in s.scalars(q):
            a.enabled, a.stopped_reason = False, reason
            n += 1
        audit(s, actor, "trader.copy_stopped", f"trader:{trader_id}", reason=reason, allocations=n)
        notify(s, "trader_risk_increased", "Stopped copying trader", reason, severity="warning", trader_id=trader_id)
        return n


def reactivate_portfolio(pid: int, actor: str, confirmation: str) -> dict:
    """Manual review step required after the maximum drawdown lockout."""
    with session_scope() as s:
        p = s.get(Portfolio, pid)
        if p.status == PortfolioStatus.REVIEW_REQUIRED and confirmation != f"REVIEWED {p.name}":
            raise PermissionError(f'type exactly "REVIEWED {p.name}" to confirm the manual review')
        old = p.status
        _set_status(s, p, PortfolioStatus.ACTIVE, f"reactivated by {actor} after review")
        p.copy_enabled = True
        out = {"status": p.status.value}
        if old == PortfolioStatus.REVIEW_REQUIRED:
            # Drawdown is re-measured from current equity after an explicit review (otherwise the
            # lockout would re-trigger immediately). Per-trade risk limits are unchanged.
            last = s.scalar(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == pid)
                            .order_by(EquitySnapshot.id.desc()).limit(1))
            if last is not None:
                out["previous_high_water_mark"] = p.high_water_mark
                p.high_water_mark = last.equity
                out["new_high_water_mark"] = last.equity
            audit(s, actor, "portfolio.reviewed", f"portfolio:{pid}", **out)
        return out
