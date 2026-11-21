"""Trader discovery + analytics pipeline (DB side).

ingest(TraderData) -> upsert trader, trades, equity points -> metrics snapshot -> behaviour
analysis -> score -> notifications (new trader, rank change, risk increase).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics.behaviour import analyse
from ..analytics.metrics import EquityRow, TradeRow, compute_metrics
from ..analytics.ranking import ENGINE_VERSION, score_trader
from ..audit import audit
from ..models import (
    CopyCapability, Market, Position, PositionStatus, Trader, TraderEquityPoint, TraderMetricSnapshot,
    TraderScore, TraderStatus, TraderTrade,
)
from ..notify import notify
from .sources import TraderData

log = logging.getLogger("titan.discovery")

# Research watchlist requested by the owner. UNVERIFIED candidates, not recommendations.
# No performance data is stored here: metrics stay UNKNOWN until retrieved from a source.
WATCHLIST = [
    ("zulutrade", "Carmine Quaranta real1"), ("zulutrade", "Money Pile"), ("zulutrade", "FIRE GC"),
    ("ctrader_copy", "Alpha Trader"), ("ctrader_copy", "Steady Eddie Algo"),
    ("ctrader_copy", "WL Tactical Growth"), ("ctrader_copy", "Stable Strategy"),
]
NO_API_NOTE = {
    "zulutrade": "ZuluTrade publishes no documented public API. Import the leader's trade history "
                 "(CSV) to evaluate; copying on ZuluTrade itself happens inside ZuluTrade.",
    "ctrader_copy": "cTrader Open API has no strategy-listing endpoint. Pepperstone/cTrader Copy "
                    "strategies can be evaluated from an imported history; copying happens in cTrader Copy "
                    "or from an owner-authorised source account.",
    "mql5": "MQL5 Signals has no public API; subscription copying works only inside MT4/MT5.",
}


def seed_watchlist(s: Session) -> int:
    n = 0
    for platform, name in WATCHLIST:
        if s.scalar(select(Trader).where(Trader.platform == platform, Trader.name == name)):
            continue
        s.add(Trader(platform=platform, name=name, market=Market.FOREX, status=TraderStatus.CANDIDATE,
                     copy_capability=CopyCapability.MONITOR_ONLY, data_status="unverified",
                     data_note=NO_API_NOTE.get(platform),
                     strategy_description=None))
        n += 1
    if n:
        audit(s, "system", "watchlist.seeded", count=n)
    return n


def ingest(s: Session, td: TraderData, market: Market = Market.FOREX) -> tuple[Trader, TraderScore]:
    tr = s.scalar(select(Trader).where(Trader.platform == td.platform, Trader.external_id == td.external_id))
    if tr is None:
        tr = s.scalar(select(Trader).where(Trader.platform == td.platform, Trader.name == td.name))
    is_new = tr is None
    if tr is None:
        tr = Trader(platform=td.platform, external_id=td.external_id, name=td.name, market=market)
        s.add(tr)
    tr.external_id = td.external_id or tr.external_id
    for attr in ("broker", "account_type", "source_url"):
        if getattr(td, attr):
            setattr(tr, attr, getattr(td, attr))
    if td.description:
        tr.strategy_description = td.description
    tr.data_status = "verified" if td.source != "manual_import" else "manual_import"
    tr.data_note = td.note or tr.data_note
    tr.verified_at = datetime.now(timezone.utc)
    if tr.status in (None, TraderStatus.CANDIDATE):
        tr.status = TraderStatus.MONITORING
    s.flush()

    existing = set(s.scalars(select(TraderTrade.external_id).where(TraderTrade.trader_id == tr.id)))
    syms = set(tr.instruments or [])
    for i, t in enumerate(td.trades):
        ext = f"{t.symbol}:{t.open_time.isoformat()}:{t.side}:{t.volume}:{i if t.close_time is None else t.close_time.isoformat()}"
        syms.add(t.symbol)
        if ext in existing:
            continue
        existing.add(ext)
        s.add(TraderTrade(trader_id=tr.id, external_id=ext[:120], symbol=t.symbol, side=t.side, volume=t.volume,
                          open_time=t.open_time, close_time=t.close_time, open_price=t.open_price,
                          close_price=t.close_price, sl=t.sl, profit=t.profit, commission=t.commission, swap=t.swap))
    tr.instruments = sorted(x for x in syms if x)
    have_ts = set(s.scalars(select(TraderEquityPoint.ts).where(TraderEquityPoint.trader_id == tr.id)))
    have_ts = {x if x.tzinfo else x.replace(tzinfo=timezone.utc) for x in have_ts}
    for p in td.curve:
        if p.ts in have_ts:
            continue
        have_ts.add(p.ts)
        s.add(TraderEquityPoint(trader_id=tr.id, ts=p.ts, balance=p.balance, equity=p.equity, growth_pct=p.growth_pct))
    s.flush()
    sc = rescore(s, tr, source=td.source, published=td.published, notify_new=is_new)
    return tr, sc


def load_history(s: Session, trader_id: int) -> tuple[list[TradeRow], list[EquityRow]]:
    trades = [TradeRow(t.symbol, t.side, t.volume, t.open_time, t.close_time, t.open_price, t.close_price,
                       t.profit, t.sl, t.commission, t.swap)
              for t in s.scalars(select(TraderTrade).where(TraderTrade.trader_id == trader_id)
                                 .order_by(TraderTrade.open_time))]
    curve = [EquityRow(p.ts, p.balance, p.equity, p.growth_pct)
             for p in s.scalars(select(TraderEquityPoint).where(TraderEquityPoint.trader_id == trader_id)
                                .order_by(TraderEquityPoint.ts))]
    return trades, curve


def execution_stats(s: Session, trader_id: int) -> dict:
    rows = s.execute(select(Position.slippage_cost, Position.initial_risk, Position.copy_delay_ms)
                     .where(Position.trader_id == trader_id, Position.status == PositionStatus.CLOSED)).all()
    if not rows:
        return {"copied_trades": 0}
    slips = [r[0] / r[1] for r in rows if r[1]]
    delays = sorted(r[2] for r in rows if r[2] is not None)
    return {"copied_trades": len(rows),
            "avg_slippage_r": sum(slips) / len(slips) if slips else None,
            "median_delay_ms": delays[len(delays) // 2] if delays else None}


def rescore(s: Session, tr: Trader, source: str | None = None, published: dict | None = None,
            notify_new: bool = False) -> TraderScore:
    trades, curve = load_history(s, tr.id)
    last_snap = s.scalar(select(TraderMetricSnapshot).where(TraderMetricSnapshot.trader_id == tr.id)
                         .order_by(TraderMetricSnapshot.id.desc()).limit(1))
    if published is None and last_snap is not None:
        published = last_snap.metrics.get("_published", {})
    m = compute_metrics(trades, curve, published=published)
    m["data_verification_timestamp"] = datetime.now(timezone.utc).isoformat()
    src = source or (last_snap.source if last_snap else "unknown")
    s.add(TraderMetricSnapshot(trader_id=tr.id, source=src, metrics={**m, "_published": published or {}}))
    findings = analyse(trades, m)
    res = score_trader(m, findings, execution_stats(s, tr.id), data_source=src,
                       description=tr.strategy_description)
    prev = s.scalar(select(TraderScore).where(TraderScore.trader_id == tr.id).order_by(TraderScore.id.desc()).limit(1))
    sc = TraderScore(trader_id=tr.id, engine_version=ENGINE_VERSION, score=res.score, rejected=res.rejected,
                     components=res.components | {"_coverage": res.coverage, "_eligible": res.eligible},
                     rejections=res.rejections, flags=res.flags, categories=res.categories,
                     explanation=res.explanation)
    s.add(sc)
    tr.category = res.categories[0] if res.categories else None
    if res.rejected and tr.status != TraderStatus.REJECTED:
        tr.status = TraderStatus.REJECTED
    s.flush()
    if notify_new:
        notify(s, "trader_discovered", f"New trader: {tr.name}",
               f"{tr.platform}: score {res.score if res.score is not None else 'not scorable'}"
               f"{' (rejected)' if res.rejected else ''}", trader_id=tr.id)
    elif prev is not None:
        if prev.score is not None and res.score is not None and abs(prev.score - res.score) >= 5:
            notify(s, "trader_rank_changed", f"{tr.name}: score {prev.score:.0f} -> {res.score:.0f}",
                   res.explanation[:300], trader_id=tr.id)
        if res.rejected and not prev.rejected:
            notify(s, "trader_risk_increased", f"{tr.name} rejected", "; ".join(r["message"] for r in res.rejections),
                   severity="warning", trader_id=tr.id)
    audit(s, "agent:trader_intelligence", "trader.scored", f"trader:{tr.id}", score=res.score,
          rejected=res.rejected, coverage=res.coverage)
    return sc


def ranking(s: Session, market: Market | None = None) -> list[dict]:
    latest = (select(TraderScore.trader_id, func.max(TraderScore.id).label("mid"))
              .group_by(TraderScore.trader_id).subquery())
    q = select(Trader, TraderScore).outerjoin(latest, latest.c.trader_id == Trader.id) \
        .outerjoin(TraderScore, TraderScore.id == latest.c.mid)
    if market:
        q = q.where(Trader.market == market)
    rows = []
    for tr, sc in s.execute(q).all():
        rows.append({"trader": tr, "score": sc})
    rows.sort(key=lambda r: (r["score"] is None or r["score"].rejected,
                             -(r["score"].score or -1) if r["score"] else 1))
    return rows
