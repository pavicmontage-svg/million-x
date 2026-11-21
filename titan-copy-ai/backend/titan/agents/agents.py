"""The six TITAN agents (deterministic cores; Claude review is layered on by Agent.run)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics.behaviour import analyse
from ..analytics.metrics import compute_metrics
from ..analytics.quant import correlation_matrix, expectancy_report, monte_carlo, walk_forward
from ..analytics.technical import technical_state
from ..audit import verify_chain
from ..discovery.service import load_history, rescore
from ..markets import forex as fx
from ..models import (
    Allocation, Order, OrderStatus, Portfolio, Position, PositionStatus, RiskEvent, Signal, SignalStatus, Trader,
    TraderEquityPoint, TraderScore,
)
from ..services import state as st
from .base import Agent


def _aw(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


# ============================================================================ 1
class TraderIntelligenceAgent(Agent):
    name, title = "trader_intelligence", "Trader Intelligence"

    def subject(self, trader_id: int, **_):
        return f"trader:{trader_id}"

    def analyze(self, s: Session, trader_id: int) -> dict:
        tr = s.get(Trader, trader_id)
        sc = rescore(s, tr)
        trades, curve = load_history(s, trader_id)
        peers = s.execute(select(func.avg(TraderScore.score), func.count(TraderScore.id))
                          .where(TraderScore.score.is_not(None), TraderScore.rejected.is_(False))).one()
        m = compute_metrics(trades, curve)
        emerging = bool(sc.score and sc.score >= 70 and (m.get("months_of_history") or 0) < 12 and not sc.rejected)
        return {"trader": {"id": tr.id, "name": tr.name, "platform": tr.platform, "status": tr.status.value,
                           "copy_capability": tr.copy_capability.value, "data_status": tr.data_status,
                           "note": tr.data_note},
                "metrics": m, "score": sc.score, "rejected": sc.rejected, "rejections": sc.rejections,
                "flags": sc.flags, "categories": sc.categories, "explanation": sc.explanation,
                "peer_avg_score": float(peers[0]) if peers[0] is not None else None, "peer_count": peers[1],
                "emerging_candidate": emerging,
                "recommendation": "reject" if sc.rejected else
                ("eligible" if (sc.components or {}).get("_eligible") else "watch")}


# ============================================================================ 2
class QuantResearchAgent(Agent):
    name, title = "quant_research", "Quantitative Research"

    def subject(self, trader_id: int, **_):
        return f"trader:{trader_id}"

    def analyze(self, s: Session, trader_id: int, n_sims: int = 5000) -> dict:
        trades, curve = load_history(s, trader_id)
        profits = [t.profit for t in trades if t.profit is not None and t.close_time]
        # per-trade % return needs the balance at entry; use the published curve when available
        rets = []
        bal = sorted([(p.ts, p.balance) for p in curve if p.balance], key=lambda x: x[0])
        if bal:
            for t in trades:
                if t.profit is None:
                    continue
                b = [v for ts, v in bal if _aw(ts) <= _aw(t.open_time)]
                if b and b[-1]:
                    rets.append(t.profit / b[-1] * 100)
        out = {"trades": len(profits), "expectancy": expectancy_report(profits) if profits else None,
               "return_basis": "balance-at-entry" if rets else "UNKNOWN (no balance curve published)"}
        if len(rets) >= 10:
            out["monte_carlo"] = monte_carlo(rets, n_sims=n_sims, block=3)
            out["walk_forward"] = walk_forward(rets) if len(rets) >= 50 else {"error": "need >= 50 trades"}
        else:
            out["monte_carlo"] = out["walk_forward"] = {"error": "insufficient per-trade return data"}
        # correlation with other followed traders (daily growth)
        followed = set(s.scalars(select(Allocation.trader_id).where(Allocation.enabled.is_(True)))) | {trader_id}
        daily = {}
        for tid in followed:
            pts = list(s.scalars(select(TraderEquityPoint).where(TraderEquityPoint.trader_id == tid)
                                 .order_by(TraderEquityPoint.ts)))
            vals = {}
            for p in pts:
                v = p.equity or p.balance or (100 * (1 + (p.growth_pct or 0) / 100) if p.growth_pct is not None else None)
                if v:
                    vals[_aw(p.ts).date().isoformat()] = v
            keys = sorted(vals)
            if len(keys) > 20:
                daily[str(tid)] = {k: vals[k] / vals[keys[i - 1]] - 1 for i, k in enumerate(keys) if i}
        out["correlations"] = correlation_matrix(daily).get(str(trader_id)) if str(trader_id) in daily else None
        return out


# ============================================================================ 3
class RiskManagementAgent(Agent):
    name, title = "risk_management", "Risk Management"

    def subject(self, portfolio_id: int, **_):
        return f"portfolio:{portfolio_id}"

    def analyze(self, s: Session, portfolio_id: int, apply: bool = True) -> dict:
        p = s.get(Portfolio, portfolio_id)
        snaps = s.execute(select(Position).where(Position.portfolio_id == portfolio_id,
                                                 Position.status == PositionStatus.CLOSED)
                          .order_by(Position.closed_at.desc()).limit(30)).scalars().all()
        streak = 0
        for pos in snaps:
            if pos.realized_pnl < 0:
                streak += 1
            else:
                break
        open_pos = list(s.scalars(select(Position).where(Position.portfolio_id == portfolio_id,
                                                         Position.status == PositionStatus.OPEN)))
        legs: dict[str, float] = {}
        if p.market.value == "forex":
            for pos in open_pos:
                for c, v in fx.currency_legs(pos.symbol, pos.side, pos.initial_risk or 0).items():
                    legs[c] = legs.get(c, 0) + v
        per_trader: dict[int, list[float]] = {}
        for pos in snaps:
            if pos.r_multiple is not None and pos.trader_id:
                per_trader.setdefault(pos.trader_id, []).append(pos.r_multiple)
        actions = []
        if streak >= 4:
            actions.append({"kind": "risk_multiplier", "key": "global", "value": 0.5, "minutes": 24 * 60,
                            "reason": f"{streak} consecutive losing copied trades"})
        for tid, rs in per_trader.items():
            if len(rs) >= 8 and np.mean(rs) < -0.3:
                actions.append({"kind": "block_trader", "key": str(tid), "value": 1, "minutes": 7 * 24 * 60,
                                "reason": f"copied expectancy {np.mean(rs):.2f}R over {len(rs)} trades"})
        if apply:
            for a in actions:
                if a["kind"] == "risk_multiplier":
                    st.set_multiplier(s, a["value"], a["reason"], a["minutes"], by=self.name)
                else:
                    st.add_block(s, "trader", a["key"], a["reason"], a["minutes"], by=self.name)
        events = [{"ts": e.ts.isoformat(), "kind": e.kind, "detail": e.detail} for e in s.scalars(
            select(RiskEvent).where(RiskEvent.portfolio_id == portfolio_id).order_by(RiskEvent.id.desc()).limit(20))]
        ov = st.current_overlay(s)
        return {"portfolio": {"id": p.id, "name": p.name, "status": p.status.value, "mode": p.mode.value,
                              "limits": p.risk_limits},
                "open_positions": len(open_pos), "open_initial_risk": sum(x.initial_risk or 0 for x in open_pos),
                "currency_legs": legs, "losing_streak": streak, "deterministic_actions": actions,
                "overlay": {"blocked_traders": ov.blocked_traders, "blocked_symbols": ov.blocked_symbols,
                            "blocked_currencies": ov.blocked_currencies, "risk_multiplier": ov.risk_multiplier},
                "recent_risk_events": events, "kill_switch": st.kill_switch_active(s)}


# ============================================================================ 4
class MarketIntelligenceAgent(Agent):
    name, title = "market_intelligence", "Market Intelligence"

    def analyze(self, s: Session, candles: dict[str, list[dict]] | None = None, calendar: list[dict] | None = None,
                apply: bool = True) -> dict:
        tech = {sym: technical_state(c) for sym, c in (candles or {}).items()}
        now = datetime.now(timezone.utc)
        blackouts = []
        for ev in calendar or []:
            try:
                t = datetime.fromisoformat(str(ev["time"]).replace("Z", "+00:00"))
            except Exception:
                continue
            if str(ev.get("impact", "")).lower() in ("high", "3") and -15 <= (t - now).total_seconds() / 60 <= 30:
                blackouts.append({"currency": ev.get("currency"), "title": ev.get("title"), "time": t.isoformat()})
                if apply and ev.get("currency"):
                    mins = (t - now).total_seconds() / 60 + 15
                    st.add_block(s, "currency", ev["currency"], f"high-impact news: {ev.get('title')}", max(1, mins),
                                 by=self.name)
        return {"technical": tech, "news_blackouts": blackouts, "calendar_events": len(calendar or []),
                "calendar_status": "provided" if calendar is not None else "UNKNOWN (no calendar source configured)"}


# ============================================================================ 5
class ExecutionAgent(Agent):
    name, title = "execution", "Execution Engine"

    def analyze(self, s: Session, hours: int = 24) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        sigs = list(s.scalars(select(Signal).where(Signal.received_at >= since)))
        by_status: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for g in sigs:
            by_status[g.status.value] = by_status.get(g.status.value, 0) + 1
            for o in g.outcome or []:
                if o.get("reason"):
                    key = o["reason"].split(":")[0][:60]
                    reasons[key] = reasons.get(key, 0) + 1
        orders = list(s.scalars(select(Order).where(Order.created_at >= since)))
        ostat: dict[str, int] = {}
        for o in orders:
            ostat[o.status.value] = ostat.get(o.status.value, 0) + 1
        pos = list(s.scalars(select(Position).where(Position.opened_at >= since)))
        delays = sorted(p.copy_delay_ms for p in pos if p.copy_delay_ms is not None)
        slip_r = [p.slippage_cost / p.initial_risk for p in pos if p.initial_risk]
        return {"window_hours": hours, "signals": len(sigs), "signal_status": by_status, "top_reject_reasons": reasons,
                "orders": ostat, "unknown_orders": ostat.get(OrderStatus.UNKNOWN.value, 0),
                "copy_delay_ms": {"p50": delays[len(delays) // 2], "p95": delays[int(len(delays) * 0.95) - 1 if len(delays) > 1 else 0],
                                  "max": delays[-1]} if delays else None,
                "avg_entry_slippage_r": float(np.mean(slip_r)) if slip_r else None,
                "engine": st.get(s, st.ENGINE)}


# ============================================================================ 6
class AuditPerformanceAgent(Agent):
    name, title = "audit_performance", "Audit & Performance"

    def subject(self, portfolio_id: int | None = None, **_):
        return f"portfolio:{portfolio_id}" if portfolio_id else "all"

    def analyze(self, s: Session, portfolio_id: int | None = None, apply: bool = True) -> dict:
        q = select(Position)
        if portfolio_id:
            q = q.where(Position.portfolio_id == portfolio_id)
        rows = list(s.scalars(q))
        closed = [p for p in rows if p.status == PositionStatus.CLOSED]
        open_ = [p for p in rows if p.status == PositionStatus.OPEN]
        realized = sum(p.realized_pnl for p in closed)
        fees = sum(p.fees for p in rows)
        slip = sum(p.slippage_cost for p in rows)
        rs = [p.r_multiple for p in closed if p.r_multiple is not None]
        wins = [p.realized_pnl for p in closed if p.realized_pnl > 0]
        losses = [p.realized_pnl for p in closed if p.realized_pnl < 0]
        deteriorated = []
        for tid in {p.trader_id for p in closed if p.trader_id}:
            tr_rs = [p.r_multiple for p in closed if p.trader_id == tid and p.r_multiple is not None]
            if len(tr_rs) < 10:
                continue
            hist = s.scalar(select(TraderScore).where(TraderScore.trader_id == tid).order_by(TraderScore.id.desc()).limit(1))
            recent = float(np.mean(tr_rs[-10:]))
            if recent < -0.25:
                deteriorated.append({"trader_id": tid, "recent_avg_r": recent, "copied_trades": len(tr_rs),
                                     "historical_score": hist.score if hist else None})
                if apply:
                    for a in s.scalars(select(Allocation).where(Allocation.trader_id == tid, Allocation.enabled.is_(True))):
                        a.enabled, a.stopped_reason = False, f"performance deterioration: last 10 copied trades avg {recent:.2f}R"
        return {"scope": portfolio_id or "all", "simulated_share": (sum(1 for p in rows if p.is_simulated) / len(rows)) if rows else None,
                "closed_trades": len(closed), "open_positions": len(open_), "realized_pnl": realized,
                "fees": fees, "slippage_cost": slip, "win_rate": len(wins) / len(closed) * 100 if closed else None,
                "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
                "avg_r": float(np.mean(rs)) if rs else None,
                "estimated_closes": sum(1 for p in closed if p.close_reason == "closed_on_venue"),
                "deteriorated_traders": deteriorated, "audit_chain": verify_chain(s)}


AGENTS: dict[str, Agent] = {a.name: a for a in (TraderIntelligenceAgent(), QuantResearchAgent(), RiskManagementAgent(),
                                                 MarketIntelligenceAgent(), ExecutionAgent(), AuditPerformanceAgent())}
