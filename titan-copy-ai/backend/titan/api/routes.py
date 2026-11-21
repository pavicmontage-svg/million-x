"""REST API consumed by the Android control centre."""
from __future__ import annotations

import json
import secrets as pysecrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.agents import AGENTS
from ..analytics.metrics import METRIC_FIELDS
from ..audit import audit, verify_chain
from ..backtest.engine import BacktestConfig, run_backtest
from ..backtest.readiness import live_readiness
from ..config import get_settings
from ..copy.signals import SignalIn, ingest_signal, publish
from ..db import after_commit, get_session
from ..discovery.service import ingest, load_history, ranking
from ..discovery.sources import TraderData, parse_curve_csv, parse_trade_csv
from ..events import get_bus
from ..models import (
    AgentPrompt, AgentRun, Allocation, AuditLog, BacktestRun, CopyCapability, Credential, EquitySnapshot, Market,
    Notification, Order, Portfolio, Position, PositionStatus, RiskEvent, Signal, Trader, TraderEquityPoint,
    TraderMetricSnapshot, TraderScore, TraderStatus, TraderTrade, TradingMode,
)
from ..risk.engine import CEILINGS, RiskLimits
from ..security import encrypt_secret, fingerprint, verify_hmac_signature
from ..services import state as st
from ..services.portfolio import minutes_to_rollover, ny_rollover
from .deps import COMMANDS, device
from .web import APIRouter, BaseModel, Body, Depends, Field, File, Form, HTTPException, Request, UploadFile

router = APIRouter(prefix="/api")
auth = [Depends(device)]


def _command(kind: str, **data):
    # published after the request's transaction commits, so the engine sees the rows it refers to
    after_commit(lambda: get_bus().publish(COMMANDS, {"cmd": kind, **data}))


# ================================================================ dashboard
@router.get("/dashboard", dependencies=auth)
def dashboard(s: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    out = []
    for p in s.scalars(select(Portfolio).order_by(Portfolio.id)):
        last = s.scalar(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == p.id).order_by(EquitySnapshot.id.desc()).limit(1))
        per = st.get(s, f"portfolio:{p.id}:periods")
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        m0 = s.scalar(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == p.id, EquitySnapshot.ts >= month_start)
                      .order_by(EquitySnapshot.ts).limit(1))
        eq = last.equity if last else None
        limits = RiskLimits.for_market(p.market.value, p.risk_limits or {})
        day_start = per.get("day_start_equity")
        daily_pnl = (eq - day_start) if eq is not None and day_start else None
        open_pos = s.scalar(select(func.count(Position.id)).where(Position.portfolio_id == p.id,
                                                                  Position.status == PositionStatus.OPEN))
        active_traders = s.scalar(select(func.count(Allocation.id)).where(Allocation.portfolio_id == p.id,
                                                                          Allocation.enabled.is_(True)))
        out.append({
            "id": p.id, "name": p.name, "market": p.market.value, "venue": p.venue, "mode": p.mode.value,
            "simulated": p.mode == TradingMode.PAPER, "currency": p.base_currency, "status": p.status.value,
            "status_reason": p.status_reason, "copy_enabled": p.copy_enabled,
            "balance": last.balance if last else None, "equity": eq,
            "available_margin": (last.equity - last.margin_used) if last else None,
            "daily_pnl": daily_pnl, "monthly_pnl": (eq - m0.equity) if eq is not None and m0 else None,
            "total_pnl": (eq - p.initial_capital) if eq is not None else None,
            "drawdown_pct": last.drawdown_pct if last else None, "open_risk": last.open_risk if last else None,
            "daily_risk_usage_pct": (max(0.0, -daily_pnl) / (day_start * limits.daily_loss_pct / 100) * 100)
            if daily_pnl is not None and day_start else None,
            "active_traders": active_traders, "open_positions": open_pos, "initial_capital": p.initial_capital,
            "snapshot_at": last.ts.isoformat() if last else None,
        })
    engine = st.get(s, st.ENGINE)
    hb = engine.get("heartbeat")
    alive = bool(hb and (now - datetime.fromisoformat(hb)).total_seconds() < 90)
    risk_run = s.scalar(select(AgentRun).where(AgentRun.agent == "risk_management").order_by(AgentRun.id.desc()).limit(1))
    return {
        "portfolios": out,
        "copy_engine": {"running": alive, **engine},
        "kill_switch": st.get(s, st.KILL),
        "market_status": {"utc": now.isoformat(), "forex_rollover_utc": ny_rollover(now).isoformat(),
                          "minutes_to_rollover": round(minutes_to_rollover(now), 1),
                          "forex_weekend": now.weekday() == 5 or (now.weekday() == 6 and now.hour < 21)
                          or (now.weekday() == 4 and now.hour >= 21)},
        "ai_risk_assessment": (risk_run.output.get("llm_review") or {}).get("assessment") if risk_run else None,
        "ai_risk_summary": (risk_run.output.get("llm_review") or {}).get("summary") if risk_run else None,
        "risk_overlay": st.get(s, st.OVERLAY),
        "unread_notifications": s.scalar(select(func.count(Notification.id)).where(Notification.read.is_(False))),
    }


# ================================================================ portfolios
class PortfolioPatch(BaseModel):
    copy_enabled: bool | None = None
    risk_limits: dict | None = None
    status: str | None = None   # only "paused" / "active" from user (other transitions are automatic)


@router.get("/portfolios", dependencies=auth)
def portfolios(s: Session = Depends(get_session)):
    return [_portfolio(p) for p in s.scalars(select(Portfolio).order_by(Portfolio.id))]


def _portfolio(p: Portfolio) -> dict:
    lim = RiskLimits.for_market(p.market.value, p.risk_limits or {})
    return {"id": p.id, "name": p.name, "market": p.market.value, "venue": p.venue, "mode": p.mode.value,
            "currency": p.base_currency, "initial_capital": p.initial_capital, "status": p.status.value,
            "status_reason": p.status_reason, "copy_enabled": p.copy_enabled, "high_water_mark": p.high_water_mark,
            "credential_id": p.credential_id, "limits": lim.__dict__ | {"drawdown_steps": list(lim.drawdown_steps)},
            "ceilings": CEILINGS, "flat_before_rollover": (p.risk_limits or {}).get("flat_before_rollover", True)}


@router.patch("/portfolios/{pid}", dependencies=auth)
def patch_portfolio(pid: int, body: PortfolioPatch, s: Session = Depends(get_session), dev=Depends(device)):
    p = s.get(Portfolio, pid) or _404()
    if body.copy_enabled is not None:
        if body.copy_enabled and p.status.value == "review_required":
            raise HTTPException(409, "maximum drawdown review pending: use /reactivate")
        p.copy_enabled = body.copy_enabled
    if body.risk_limits is not None:
        try:
            tightened = RiskLimits.for_market(p.market.value, body.risk_limits)
        except ValueError as e:
            raise HTTPException(400, str(e))
        keep = {k: getattr(tightened, k) for k in body.risk_limits if hasattr(tightened, k)}
        if "flat_before_rollover" in body.risk_limits:
            keep["flat_before_rollover"] = True if body.risk_limits["flat_before_rollover"] is None else bool(body.risk_limits["flat_before_rollover"])
        p.risk_limits = {**(p.risk_limits or {}), **keep}
    if body.status in ("paused", "active"):
        from ..models import PortfolioStatus
        if p.status.value not in ("active", "paused"):
            raise HTTPException(409, f"status {p.status.value} cannot be changed manually")
        p.status = PortfolioStatus(body.status)
    audit(s, f"device:{dev.id}", "portfolio.updated", f"portfolio:{pid}", **body.model_dump(exclude_none=True))
    return _portfolio(p)


@router.post("/portfolios/{pid}/reactivate", dependencies=auth)
def reactivate(pid: int, confirmation: str = Body(embed=True), dev=Depends(device)):
    from ..copy.monitor import reactivate_portfolio
    try:
        return reactivate_portfolio(pid, f"device:{dev.id}", confirmation)
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.get("/portfolios/{pid}/equity", dependencies=auth)
def equity(pid: int, days: int = 30, s: Session = Depends(get_session)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = s.scalars(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == pid, EquitySnapshot.ts >= since)
                     .order_by(EquitySnapshot.ts))
    pts = [{"t": r.ts.isoformat(), "equity": r.equity, "balance": r.balance, "dd": r.drawdown_pct} for r in rows]
    if len(pts) > 500:   # downsample for the phone
        step = len(pts) // 500 + 1
        pts = pts[::step] + [pts[-1]]
    return pts


# ================================================================ traders
@router.get("/traders", dependencies=auth)
def traders(market: str | None = None, s: Session = Depends(get_session)):
    out = []
    for r in ranking(s, Market(market) if market else None):
        tr, sc = r["trader"], r["score"]
        snap = s.scalar(select(TraderMetricSnapshot).where(TraderMetricSnapshot.trader_id == tr.id)
                        .order_by(TraderMetricSnapshot.id.desc()).limit(1))
        m = snap.metrics if snap else {}
        out.append({"id": tr.id, "name": tr.name, "platform": tr.platform, "market": tr.market.value,
                    "status": tr.status.value, "copy_capability": tr.copy_capability.value,
                    "category": tr.category, "data_status": tr.data_status,
                    "score": sc.score if sc else None, "rejected": sc.rejected if sc else False,
                    "eligible": bool(sc and (sc.components or {}).get("_eligible")),
                    "roi_30d": m.get("roi_30d"), "roi_365d": m.get("roi_365d"),
                    "max_drawdown_pct": m.get("max_equity_drawdown_pct") if m.get("max_equity_drawdown_pct") is not None
                    else m.get("max_balance_drawdown_pct"),
                    "profit_factor": m.get("profit_factor"), "win_rate": m.get("win_rate"),
                    "verified_at": tr.verified_at.isoformat() if tr.verified_at else None})
    return out


@router.get("/traders/{tid}", dependencies=auth)
def trader(tid: int, s: Session = Depends(get_session)):
    tr = s.get(Trader, tid) or _404()
    snap = s.scalar(select(TraderMetricSnapshot).where(TraderMetricSnapshot.trader_id == tid)
                    .order_by(TraderMetricSnapshot.id.desc()).limit(1))
    m = {k: (snap.metrics.get(k) if snap else None) for k in METRIC_FIELDS}
    scores = list(s.scalars(select(TraderScore).where(TraderScore.trader_id == tid).order_by(TraderScore.id.desc()).limit(30)))
    curve = [{"t": p.ts.isoformat(), "balance": p.balance, "equity": p.equity, "growth_pct": p.growth_pct}
             for p in s.scalars(select(TraderEquityPoint).where(TraderEquityPoint.trader_id == tid).order_by(TraderEquityPoint.ts))]
    if len(curve) > 600:
        curve = curve[:: len(curve) // 600 + 1] + [curve[-1]]
    trades = [{"symbol": t.symbol, "side": t.side, "volume": t.volume, "open_time": t.open_time.isoformat(),
               "close_time": t.close_time.isoformat() if t.close_time else None, "profit": t.profit, "sl": t.sl}
              for t in s.scalars(select(TraderTrade).where(TraderTrade.trader_id == tid).order_by(TraderTrade.open_time.desc()).limit(100))]
    allocs = [{"portfolio_id": a.portfolio_id, "enabled": a.enabled, "capital_fraction": a.capital_fraction,
               "max_risk_pct": a.max_risk_pct, "stopped_reason": a.stopped_reason}
              for a in s.scalars(select(Allocation).where(Allocation.trader_id == tid))]
    latest = scores[0] if scores else None
    return {
        "id": tr.id, "name": tr.name, "platform": tr.platform, "broker": tr.broker, "trader_id": tr.external_id,
        "account_type": tr.account_type, "market": tr.market.value, "strategy_description": tr.strategy_description,
        "instruments": tr.instruments, "status": tr.status.value, "copy_capability": tr.copy_capability.value,
        "category": tr.category, "data_status": tr.data_status, "data_note": tr.data_note, "source_url": tr.source_url,
        "data_source": snap.source if snap else None,
        "data_verification_timestamp": (snap.metrics.get("data_verification_timestamp") if snap else None),
        "metrics": m,
        "score": latest.score if latest else None, "rejected": latest.rejected if latest else False,
        "components": latest.components if latest else {}, "rejections": latest.rejections if latest else [],
        "flags": latest.flags if latest else [], "categories": latest.categories if latest else [],
        "explanation": latest.explanation if latest else "Not analysed yet: no data has been retrieved for this trader.",
        "score_history": [{"t": x.as_of.isoformat(), "score": x.score} for x in reversed(scores)],
        "equity_curve": curve, "recent_trades": trades, "allocations": allocs,
        "signal_source_configured": bool((tr.signal_source or {}).get("secret_hash")),
        "source_account": {k: (tr.signal_source or {}).get(k) for k in ("type", "credential_id", "environment", "since")}
        if (tr.signal_source or {}).get("type") else None,
    }


@router.post("/traders/import", dependencies=auth)
def import_trader(name: str = Form(...), platform: str = Form(...), market: str = Form("forex"),
                        broker: str | None = Form(None), description: str | None = Form(None),
                        trades_csv: UploadFile | None = File(None), curve_csv: UploadFile | None = File(None),
                        s: Session = Depends(get_session), dev=Depends(device)):
    trades = parse_trade_csv(trades_csv.file.read().decode("utf-8", "replace")) if trades_csv else []
    curve = parse_curve_csv(curve_csv.file.read().decode("utf-8", "replace")) if curve_csv else []
    if not trades and not curve:
        raise HTTPException(400, "no trades or curve rows could be parsed")
    td = TraderData(platform=platform, external_id=f"manual:{name}", name=name, source="manual_import", broker=broker,
                    description=description, trades=trades, curve=curve,
                    note="Manually imported history: verify it against the platform before allocating capital.")
    tr, sc = ingest(s, td, Market(market))
    audit(s, f"device:{dev.id}", "trader.imported", f"trader:{tr.id}", trades=len(trades), curve=len(curve))
    return {"trader_id": tr.id, "score": sc.score, "rejected": sc.rejected, "explanation": sc.explanation}


@router.post("/traders/{tid}/status", dependencies=auth)
def trader_status(tid: int, status: str = Body(embed=True), s: Session = Depends(get_session), dev=Depends(device)):
    tr = s.get(Trader, tid) or _404()
    new = TraderStatus(status)
    if new == TraderStatus.APPROVED:
        sc = s.scalar(select(TraderScore).where(TraderScore.trader_id == tid).order_by(TraderScore.id.desc()).limit(1))
        if sc is None or sc.rejected:
            raise HTTPException(409, "trader is rejected or not analysed; cannot approve")
    tr.status = new
    audit(s, f"device:{dev.id}", "trader.status", f"trader:{tid}", status=status)
    return {"status": tr.status.value}


@router.post("/traders/{tid}/signal-source", dependencies=auth)
def signal_source(tid: int, s: Session = Depends(get_session), dev=Depends(device)):
    """Create (or rotate) the HMAC secret an authorised provider uses to sign webhook signals.
    The secret is returned ONCE; only an encrypted copy is stored."""
    tr = s.get(Trader, tid) or _404()
    secret = pysecrets.token_urlsafe(32)
    tr.signal_source = {"secret_enc": encrypt_secret({"secret": secret}, f"signal|{tid}"),
                        "secret_hash": fingerprint(secret), "created": datetime.now(timezone.utc).isoformat()}
    tr.copy_capability = CopyCapability.AUTHORIZED_SIGNALS
    audit(s, f"device:{dev.id}", "trader.signal_source", f"trader:{tid}", fingerprint=fingerprint(secret))
    return {"webhook_path": f"/api/signals/webhook/{tid}", "secret": secret,
            "signature": "hex(HMAC_SHA256(secret, f'{X-Titan-Timestamp}.{raw_body}')) in X-Titan-Signature"}


class SourceAccountIn(BaseModel):
    credential_id: int
    environment: str = Field("demo", pattern=r"^(demo|live)$")


@router.post("/traders/{tid}/source-account", dependencies=auth)
def source_account(tid: int, body: SourceAccountIn, s: Session = Depends(get_session), dev=Depends(device)):
    """Use one of the owner's connected accounts as this trader's signal source (polled; no webhook needed)"""
    from ..copy.sources import SOURCE_TYPES
    tr = s.get(Trader, tid) or _404()
    c = s.get(Credential, body.credential_id) or _404()
    if c.venue not in SOURCE_TYPES:
        raise HTTPException(400, f"{c.venue} cannot be a signal source (use Binance futures, MT5 or cTrader)")
    market = Market.CRYPTO if c.venue.startswith("binance") else Market.FOREX
    if tr.market != market:
        raise HTTPException(400, "account market does not match the trader's market")
    tr.signal_source = {**(tr.signal_source or {}), "type": c.venue, "credential_id": c.id,
                        "environment": c.environment, "since": datetime.now(timezone.utc).isoformat()}
    tr.copy_capability = CopyCapability.AUTHORIZED_SIGNALS
    st.put(s, f"source:{tid}", {})   # new baseline: positions open right now are not copied
    audit(s, f"device:{dev.id}", "trader.source_account", f"trader:{tid}", credential_id=c.id, venue=c.venue)
    return {"trader_id": tid, "source": c.venue, "note": "positions already open on the source are not copied"}


@router.delete("/traders/{tid}/source-account", dependencies=auth)
def remove_source_account(tid: int, s: Session = Depends(get_session), dev=Depends(device)):
    """Stop polling the trader's source account"""
    tr = s.get(Trader, tid) or _404()
    src = dict(tr.signal_source or {})
    for k in ("type", "credential_id", "environment", "since"):
        src.pop(k, None)
    tr.signal_source = src
    if not src.get("secret_enc"):
        tr.copy_capability = CopyCapability.MONITOR_ONLY
    audit(s, f"device:{dev.id}", "trader.source_account_removed", f"trader:{tid}")
    return {"trader_id": tid}


# ================================================================ allocations
class AllocationIn(BaseModel):
    portfolio_id: int
    trader_id: int
    enabled: bool = True
    capital_fraction: float = Field(1.0, gt=0, le=1)
    max_risk_pct: float = Field(0.5, gt=0, le=0.5)


@router.get("/allocations", dependencies=auth)
def allocations(s: Session = Depends(get_session)):
    return [{"id": a.id, "portfolio_id": a.portfolio_id, "trader_id": a.trader_id, "enabled": a.enabled,
             "capital_fraction": a.capital_fraction, "max_risk_pct": a.max_risk_pct, "stopped_reason": a.stopped_reason,
             "trader_name": s.get(Trader, a.trader_id).name}
            for a in s.scalars(select(Allocation))]


@router.post("/allocations", dependencies=auth)
def upsert_allocation(body: AllocationIn, s: Session = Depends(get_session), dev=Depends(device)):
    p, tr = s.get(Portfolio, body.portfolio_id) or _404(), s.get(Trader, body.trader_id) or _404()
    if p.market != tr.market:
        raise HTTPException(400, "trader market does not match portfolio market")
    if body.enabled:
        if tr.status != TraderStatus.APPROVED:
            raise HTTPException(409, "only APPROVED traders can be copied")
        if tr.copy_capability != CopyCapability.AUTHORIZED_SIGNALS:
            raise HTTPException(409, "this trader has no authorised signal source (monitoring only)")
    a = s.scalar(select(Allocation).where(Allocation.portfolio_id == p.id, Allocation.trader_id == tr.id))
    if a is None:
        a = Allocation(portfolio_id=p.id, trader_id=tr.id)
        s.add(a)
    a.enabled, a.capital_fraction, a.max_risk_pct = body.enabled, body.capital_fraction, body.max_risk_pct
    a.stopped_reason = None if body.enabled else "disabled by user"
    s.flush()
    audit(s, f"device:{dev.id}", "allocation.upsert", f"allocation:{a.id}", **body.model_dump())
    return {"id": a.id}


# ================================================================ signals
@router.post("/signals/webhook/{tid}")
def webhook(tid: int, request: Request, raw_body: bytes = b"", s: Session = Depends(get_session)):
    tr = s.get(Trader, tid)
    if tr is None or not (tr.signal_source or {}).get("secret_enc"):
        raise HTTPException(404, "no authorised signal source")
    from ..security import decrypt_secret
    secret = decrypt_secret(tr.signal_source["secret_enc"], f"signal|{tid}")["secret"]
    body = raw_body
    if not verify_hmac_signature(secret, body, request.headers.get("X-Titan-Timestamp", ""),
                                 request.headers.get("X-Titan-Signature", "")):
        raise HTTPException(401, "bad signature or timestamp")
    data = json.loads(body)
    data["trader_id"] = tid
    sig = SignalIn.model_validate(data)
    row, created = ingest_signal(s, sig, f"webhook:{tid}", payload=data)
    if created:
        rid = row.id
        after_commit(lambda: publish(rid))
    return {"signal_id": row.id, "duplicate": not created}


@router.get("/signals", dependencies=auth)
def signals(limit: int = 100, s: Session = Depends(get_session)):
    return [{"id": g.id, "trader_id": g.trader_id, "kind": g.kind.value, "symbol": g.symbol, "side": g.side,
             "entry": g.entry_price, "sl": g.sl, "tp": g.tp, "status": g.status.value, "outcome": g.outcome,
             "source_ts": g.source_ts.isoformat(), "received_at": g.received_at.isoformat(), "simulated": g.is_simulated}
            for g in s.scalars(select(Signal).order_by(Signal.id.desc()).limit(min(limit, 500)))]


# ================================================================ positions / history
def _pos(p: Position, s: Session) -> dict:
    tr = s.get(Trader, p.trader_id) if p.trader_id else None
    return {"id": p.id, "portfolio_id": p.portfolio_id, "trader": tr.name if tr else None, "symbol": p.symbol,
            "side": p.side, "quantity": p.quantity, "initial_quantity": p.initial_quantity, "entry_price": p.entry_price,
            "sl": p.sl, "tp": p.tp, "initial_risk": p.initial_risk, "status": p.status.value,
            "opened_at": p.opened_at.isoformat(), "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "exit_price": p.exit_price, "realized_pnl": p.realized_pnl, "fees": p.fees, "slippage_cost": p.slippage_cost,
            "r_multiple": p.r_multiple, "close_reason": p.close_reason, "copy_delay_ms": p.copy_delay_ms,
            "liquidation_price": p.liquidation_price, "simulated": p.is_simulated}


@router.get("/positions", dependencies=auth)
def positions(status: str = "open", portfolio_id: int | None = None, limit: int = 200, s: Session = Depends(get_session)):
    q = select(Position).where(Position.status == PositionStatus(status))
    if portfolio_id:
        q = q.where(Position.portfolio_id == portfolio_id)
    q = q.order_by(Position.id.desc()).limit(min(limit, 1000))
    return [_pos(p, s) for p in s.scalars(q)]


@router.post("/positions/{pos_id}/close", dependencies=auth)
def close_position(pos_id: int, s: Session = Depends(get_session), dev=Depends(device)):
    p = s.get(Position, pos_id) or _404()
    if p.status != PositionStatus.OPEN:
        raise HTTPException(409, "position is not open")
    audit(s, f"device:{dev.id}", "position.close_requested", f"position:{pos_id}")
    _command("close_position", position_id=pos_id, actor=f"device:{dev.id}")
    return {"queued": True}


@router.get("/orders", dependencies=auth)
def orders(limit: int = 100, s: Session = Depends(get_session)):
    return [{"id": o.id, "portfolio_id": o.portfolio_id, "client_order_id": o.client_order_id, "purpose": o.purpose,
             "symbol": o.symbol, "side": o.side, "qty": o.quantity, "status": o.status.value, "filled": o.filled_qty,
             "avg_price": o.avg_price, "fee": o.fee, "error": o.error, "created_at": o.created_at.isoformat()}
            for o in s.scalars(select(Order).order_by(Order.id.desc()).limit(min(limit, 500)))]


# ================================================================ risk
@router.get("/risk", dependencies=auth)
def risk(s: Session = Depends(get_session)):
    ports = []
    for p in s.scalars(select(Portfolio)):
        last = s.scalar(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == p.id).order_by(EquitySnapshot.id.desc()).limit(1))
        per = st.get(s, f"portfolio:{p.id}:periods")
        lim = RiskLimits.for_market(p.market.value, p.risk_limits or {})
        eq = last.equity if last else None
        ports.append({**_portfolio(p),
                      "equity": eq, "drawdown_pct": last.drawdown_pct if last else None,
                      "open_risk_pct": (last.open_risk / eq * 100) if last and eq else None,
                      "daily_loss_pct": max(0.0, (per["day_start_equity"] - eq) / per["day_start_equity"] * 100)
                      if eq is not None and per.get("day_start_equity") else None,
                      "weekly_loss_pct": max(0.0, (per["week_start_equity"] - eq) / per["week_start_equity"] * 100)
                      if eq is not None and per.get("week_start_equity") else None,
                      "limits": lim.__dict__ | {"drawdown_steps": list(lim.drawdown_steps)}})
    events = [{"id": e.id, "ts": e.ts.isoformat(), "portfolio_id": e.portfolio_id, "kind": e.kind,
               "severity": e.severity, "detail": e.detail}
              for e in s.scalars(select(RiskEvent).order_by(RiskEvent.id.desc()).limit(100))]
    return {"portfolios": ports, "overlay": st.get(s, st.OVERLAY), "kill_switch": st.get(s, st.KILL), "events": events}


# ================================================================ agents
@router.get("/agents", dependencies=auth)
def agents(s: Session = Depends(get_session)):
    out = []
    for name, a in AGENTS.items():
        runs = s.execute(select(func.count(AgentRun.id), func.avg(AgentRun.duration_ms),
                                func.sum(AgentRun.input_tokens), func.sum(AgentRun.output_tokens))
                         .where(AgentRun.agent == name)).one()
        errors = s.scalar(select(func.count(AgentRun.id)).where(AgentRun.agent == name, AgentRun.error.is_not(None)))
        last = s.scalar(select(AgentRun).where(AgentRun.agent == name).order_by(AgentRun.id.desc()).limit(1))
        versions = [{"version": v.version, "sha256": v.sha256[:12], "created_at": v.created_at.isoformat()}
                    for v in s.scalars(select(AgentPrompt).where(AgentPrompt.agent == name).order_by(AgentPrompt.id))]
        out.append({"name": name, "title": a.title, "version": a.version, "prompt": a.prompt, "prompt_versions": versions,
                    "metrics": {"runs": runs[0], "avg_ms": runs[1], "input_tokens": runs[2] or 0,
                                "output_tokens": runs[3] or 0, "errors": errors},
                    "last_run": _run(last) if last else None})
    return out


def _run(r: AgentRun) -> dict:
    return {"id": r.id, "agent": r.agent, "version": r.version, "ts": r.ts.isoformat(), "subject": r.subject,
            "llm_used": r.llm_used, "llm_model": r.llm_model, "duration_ms": r.duration_ms, "error": r.error,
            "output": r.output}


@router.get("/agents/{name}/runs", dependencies=auth)
def agent_runs(name: str, limit: int = 20, s: Session = Depends(get_session)):
    return [_run(r) for r in s.scalars(select(AgentRun).where(AgentRun.agent == name).order_by(AgentRun.id.desc()).limit(limit))]


@router.post("/agents/{name}/run", dependencies=auth)
def run_agent(name: str, params: dict = Body(default={}), s: Session = Depends(get_session), dev=Depends(device)):
    a = AGENTS.get(name) or _404()
    allowed = {"trader_id", "portfolio_id", "hours"}
    out = a.run(s, **{k: v for k, v in params.items() if k in allowed})
    audit(s, f"device:{dev.id}", "agent.run", name, run_id=out.get("_run_id"))
    return out


# ================================================================ analytics / backtest
@router.get("/analytics/{tid}", dependencies=auth)
def analytics(tid: int, s: Session = Depends(get_session)):
    s.get(Trader, tid) or _404()
    return AGENTS["quant_research"].run(s, use_llm=False, trader_id=tid)


class BacktestIn(BaseModel):
    trader_id: int
    capital: float = 500.0
    currency: str = "EUR"
    delay_ms: float = 1500
    spread_pips: float = 0.5
    commission_per_lot: float = 7.0
    slippage_r: float = 0.02
    partial_fill_prob: float = 0.0
    failure_prob: float = 0.0
    disconnect_prob: float = 0.0
    seed: int = 7
    fx_rates: dict[str, float] | None = None   # optional mids for exact pip values / min-lot checks


@router.post("/backtest", dependencies=auth)
def backtest(body: BacktestIn, s: Session = Depends(get_session)):
    trades, curve = load_history(s, body.trader_id)
    res = run_backtest(trades, BacktestConfig(**body.model_dump(exclude={"trader_id"})), curve=curve)
    row = BacktestRun(kind="copy_backtest", params=body.model_dump(), result=res, is_simulated=True)
    s.add(row)
    s.flush()
    return {"id": row.id, **res}


@router.get("/readiness", dependencies=auth)
def readiness(s: Session = Depends(get_session)):
    return [live_readiness(s, p) for p in s.scalars(select(Portfolio))]


class LiveApproval(BaseModel):
    portfolio_id: int
    confirmation: str
    days: int = Field(7, ge=1, le=30)


@router.post("/live/approve", dependencies=auth)
def approve_live(body: LiveApproval, s: Session = Depends(get_session), dev=Depends(device)):
    p = s.get(Portfolio, body.portfolio_id) or _404()
    r = live_readiness(s, p)
    if not r["ready"]:
        raise HTTPException(409, {"message": "live gate not passed", "checks": r["checks"]})
    phrase = f"I ACCEPT REAL MONEY RISK ON {p.name}"
    if body.confirmation != phrase:
        raise HTTPException(403, f'type exactly: "{phrase}"')
    appr = st.get(s, st.LIVE_APPROVALS, {})
    appr[str(p.id)] = {"by": f"device:{dev.id}", "at": datetime.now(timezone.utc).isoformat(),
                       "expires_at": (datetime.now(timezone.utc) + timedelta(days=body.days)).isoformat()}
    st.put(s, st.LIVE_APPROVALS, appr)
    audit(s, f"device:{dev.id}", "live.approved", f"portfolio:{p.id}", days=body.days)
    return {"approved": True, "expires_at": appr[str(p.id)]["expires_at"],
            "server_live_flag": get_settings().live_trading_enabled}


# ================================================================ connections (credentials)
class CredentialIn(BaseModel):
    venue: str = Field(pattern=r"^(binance_spot|binance_usdm|binance_coinm|ctrader|mt5|myfxbook|darwinex)$")
    environment: str = Field(pattern=r"^(demo|live)$")
    label: str = Field(min_length=1, max_length=120)
    fields: dict


REQUIRED = {"binance_spot": {"api_key", "api_secret"}, "binance_usdm": {"api_key", "api_secret"},
            "binance_coinm": {"api_key", "api_secret"},
            "ctrader": {"client_id", "client_secret", "access_token", "account_id"},
            "mt5": {"url", "secret"}, "myfxbook": {"email", "password"}, "darwinex": {"token"}}


@router.get("/connections", dependencies=auth)
def connections(s: Session = Depends(get_session)):
    return [{"id": c.id, "venue": c.venue, "environment": c.environment, "label": c.label, "fingerprint": c.fingerprint,
             "created_at": c.created_at.isoformat(), "verified_at": c.verified_at.isoformat() if c.verified_at else None,
             "verify_result": c.verify_result,
             "portfolios": [p.name for p in s.scalars(select(Portfolio).where(Portfolio.credential_id == c.id))]}
            for c in s.scalars(select(Credential).order_by(Credential.id))]


@router.post("/connections", dependencies=auth)
def add_connection(body: CredentialIn, s: Session = Depends(get_session), dev=Depends(device)):
    missing = REQUIRED[body.venue] - set(body.fields)
    if missing:
        raise HTTPException(400, f"missing fields: {sorted(missing)}")
    if body.environment == "live" and not get_settings().live_trading_enabled and body.venue.startswith(("binance", "ctrader", "mt5")):
        raise HTTPException(409, "live trading is disabled on this server; store demo credentials only")
    key_material = str(body.fields.get("api_key") or body.fields.get("client_id") or body.fields.get("email")
                       or body.fields.get("url") or body.fields.get("token"))
    c = Credential(venue=body.venue, environment=body.environment, label=body.label,
                   ciphertext=encrypt_secret(body.fields, f"{body.venue}|{body.environment}|{body.label}"),
                   fingerprint=fingerprint(key_material))
    s.add(c)
    s.flush()
    audit(s, f"device:{dev.id}", "credential.added", f"credential:{c.id}", venue=c.venue, env=c.environment,
          fingerprint=c.fingerprint)
    _command("verify_credential", credential_id=c.id)
    return {"id": c.id, "fingerprint": c.fingerprint}


@router.post("/connections/{cid}/verify", dependencies=auth)
def verify_connection(cid: int, s: Session = Depends(get_session)):
    s.get(Credential, cid) or _404()
    _command("verify_credential", credential_id=cid)
    return {"queued": True}


class AttachIn(BaseModel):
    credential_id: int
    market: str
    name: str
    venue: str
    capital: float


@router.post("/connections/{cid}/portfolio", dependencies=auth)
def attach_portfolio(cid: int, body: AttachIn, s: Session = Depends(get_session), dev=Depends(device)):
    c = s.get(Credential, cid) or _404()
    if c.venue not in ("binance_spot", "binance_usdm", "binance_coinm", "ctrader", "mt5"):
        raise HTTPException(400, "credential is not an execution venue")
    mode = TradingMode.LIVE if c.environment == "live" else TradingMode.DEMO
    p = Portfolio(name=body.name, market=Market(body.market), venue=c.venue, mode=mode,
                  base_currency="USDT" if c.venue.startswith("binance") else "EUR", initial_capital=body.capital,
                  high_water_mark=body.capital, credential_id=c.id, copy_enabled=False, risk_limits={})
    s.add(p)
    s.flush()
    audit(s, f"device:{dev.id}", "portfolio.created", f"portfolio:{p.id}", venue=c.venue, mode=mode.value)
    _command("reload_adapters")
    return _portfolio(p)


@router.delete("/connections/{cid}", dependencies=auth)
def delete_connection(cid: int, s: Session = Depends(get_session), dev=Depends(device)):
    c = s.get(Credential, cid) or _404()
    if s.scalar(select(Portfolio.id).where(Portfolio.credential_id == cid)):
        raise HTTPException(409, "credential is used by a portfolio")
    s.delete(c)
    audit(s, f"device:{dev.id}", "credential.deleted", f"credential:{cid}")
    return {"deleted": True}


# ================================================================ notifications / settings / audit
@router.get("/notifications", dependencies=auth)
def notifications(limit: int = 100, after_id: int = 0, s: Session = Depends(get_session)):
    return [{"id": n.id, "ts": n.ts.isoformat(), "kind": n.kind, "severity": n.severity, "title": n.title,
             "body": n.body, "data": n.data, "read": n.read}
            for n in s.scalars(select(Notification).where(Notification.id > after_id)
                               .order_by(Notification.id.desc()).limit(min(limit, 500)))]


@router.post("/notifications/read", dependencies=auth)
def mark_read(ids: list[int] = Body(embed=True), s: Session = Depends(get_session)):
    for n in s.scalars(select(Notification).where(Notification.id.in_(ids))):
        n.read = True
    return {"ok": True}


@router.post("/devices/fcm", dependencies=auth)
def fcm(token: str = Body(embed=True), s: Session = Depends(get_session), dev=Depends(device)):
    dev.fcm_token = token
    return {"ok": True}


@router.get("/settings", dependencies=auth)
def settings_view():
    cfg = get_settings()
    from ..agents.base import llm_available
    return {"env": cfg.env, "live_trading_enabled": cfg.live_trading_enabled, "llm_available": llm_available(),
            "llm_model": cfg.llm_model, "max_signal_age_forex_s": cfg.max_signal_age_forex_s,
            "max_signal_age_crypto_s": cfg.max_signal_age_crypto_s, "fcm_configured": bool(cfg.fcm_project_id),
            "forex_initial_capital_eur": cfg.forex_initial_capital_eur,
            "crypto_initial_capital_usdt": cfg.crypto_initial_capital_usdt}


@router.get("/audit", dependencies=auth)
def audit_log(limit: int = 200, s: Session = Depends(get_session)):
    return [{"id": a.id, "ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity,
             "detail": a.detail, "hash": a.hash[:16]}
            for a in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)))]


@router.get("/audit/verify", dependencies=auth)
def audit_verify(s: Session = Depends(get_session)):
    return verify_chain(s)


# ================================================================ emergency controls
@router.get("/emergency", dependencies=auth)
def emergency(s: Session = Depends(get_session)):
    return {"kill_switch": st.get(s, st.KILL), "open_positions": s.scalar(select(func.count(Position.id)).where(
        Position.status == PositionStatus.OPEN)), "portfolios": [{"id": p.id, "name": p.name, "status": p.status.value,
                                                                   "copy_enabled": p.copy_enabled}
                                                                  for p in s.scalars(select(Portfolio))]}


@router.post("/emergency/stop", dependencies=auth)
def emergency_stop(reason: str = Body("manual emergency stop", embed=True), dev=Depends(device)):
    from ..copy.monitor import set_kill_switch
    set_kill_switch(True, reason, f"device:{dev.id}")
    return {"kill_switch": True}


@router.post("/emergency/release", dependencies=auth)
def emergency_release(confirmation: str = Body(embed=True), dev=Depends(device)):
    if confirmation != "RELEASE EMERGENCY STOP":
        raise HTTPException(403, 'type exactly "RELEASE EMERGENCY STOP"')
    from ..copy.monitor import set_kill_switch
    set_kill_switch(False, "released by owner", f"device:{dev.id}")
    return {"kill_switch": False}


@router.post("/emergency/flatten", dependencies=auth)
def emergency_flatten(confirmation: str = Body(embed=True), portfolio_id: int | None = Body(None, embed=True),
                      s: Session = Depends(get_session), dev=Depends(device)):
    if confirmation != "CLOSE ALL":
        raise HTTPException(403, 'type exactly "CLOSE ALL"')
    audit(s, f"device:{dev.id}", "emergency.flatten_requested", f"portfolio:{portfolio_id}" if portfolio_id else "all")
    _command("flatten", portfolio_id=portfolio_id, actor=f"device:{dev.id}")
    return {"queued": True}


@router.post("/emergency/trader/{tid}/stop", dependencies=auth)
def emergency_trader(tid: int, reason: str = Body("stopped by owner", embed=True), dev=Depends(device)):
    from ..copy.monitor import stop_trader
    return {"allocations_disabled": stop_trader(tid, reason, f"device:{dev.id}")}


def _404():
    raise HTTPException(404, "not found")
