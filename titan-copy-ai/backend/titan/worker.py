"""Celery worker + beat: discovery refresh, rescoring, AI agents, reports, backups.

    celery -A titan.worker worker -B --loglevel=info
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from .config import get_settings

log = logging.getLogger("titan.worker")
app = Celery("titan", broker=get_settings().redis_url, backend=get_settings().redis_url)
app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, timezone="UTC",
                beat_schedule={
                    "refresh-sources": {"task": "titan.worker.refresh_sources", "schedule": 6 * 3600},
                    "rescore": {"task": "titan.worker.rescore_all", "schedule": 6 * 3600},
                    "risk-agent": {"task": "titan.worker.run_agent", "schedule": 15 * 60, "args": ("risk_management",)},
                    "execution-agent": {"task": "titan.worker.run_agent", "schedule": 3600, "args": ("execution",)},
                    "market-agent": {"task": "titan.worker.market_intelligence", "schedule": 15 * 60},
                    "audit-agent": {"task": "titan.worker.run_agent", "schedule": 6 * 3600, "args": ("audit_performance",)},
                    "weekly-report": {"task": "titan.worker.weekly_report", "schedule": crontab(hour=18, minute=7, day_of_week=0)},
                    "backup": {"task": "titan.worker.backup", "schedule": crontab(hour=2, minute=17)},
                })


@app.task
def refresh_sources():
    from .db import session_scope
    from .discovery.service import ingest
    from .discovery.sources import DarwinexInfoSource, MyfxbookSource
    from .models import Credential
    from .services.venues import credential

    done = 0
    with session_scope() as s:
        creds = [(c.id, c.venue) for c in s.scalars(select(Credential).where(Credential.venue.in_(["myfxbook", "darwinex"])))]
    for cid, venue in creds:
        try:
            with session_scope() as s:
                data = credential(s, cid)
            if venue == "myfxbook":
                src = MyfxbookSource(data["email"], data["password"])
                for acc in src.list_accounts():
                    td = src.fetch(acc)
                    with session_scope() as s:
                        ingest(s, td)
                    done += 1
                src.logout()
            else:
                src = DarwinexInfoSource(data["token"])
                with session_scope() as s:
                    from .models import Trader
                    tracked = [t.external_id for t in s.scalars(select(Trader).where(Trader.platform == "darwinex"))]
                for product in tracked:
                    td = src.fetch(product)
                    with session_scope() as s:
                        ingest(s, td)
                    done += 1
        except Exception as e:
            log.warning("source %s refresh failed: %s", venue, e)
    return done


@app.task
def rescore_all():
    from .db import session_scope
    from .discovery.service import rescore
    from .models import Trader, TraderTrade

    with session_scope() as s:
        ids = list(s.scalars(select(Trader.id).where(Trader.id.in_(select(TraderTrade.trader_id)))))
    for tid in ids:
        with session_scope() as s:
            rescore(s, s.get(Trader, tid))
    return len(ids)


@app.task
def run_agent(name: str):
    from .agents.agents import AGENTS
    from .db import session_scope
    from .models import Portfolio

    with session_scope() as s:
        if name in ("risk_management",):
            for p in list(s.scalars(select(Portfolio))):
                AGENTS[name].run(s, portfolio_id=p.id)
        else:
            AGENTS[name].run(s)


@app.task
def market_intelligence():
    import httpx

    from .adapters.feeds import BinanceRestFeed
    from .agents.agents import AGENTS
    from .db import session_scope

    candles = {}
    feed = BinanceRestFeed(get_settings().binance_public_data_url)
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
        try:
            candles[sym] = feed.klines(sym, "1h", 300)
        except Exception as e:
            log.warning("klines %s failed: %s", sym, e)
    calendar = None
    url = os.environ.get("TITAN_CALENDAR_URL")   # optional economic calendar JSON feed chosen by the owner
    if url:
        try:
            raw = httpx.get(url, timeout=20).json()
            calendar = [{"time": e.get("date") or e.get("time"), "currency": e.get("country") or e.get("currency"),
                         "impact": e.get("impact"), "title": e.get("title")} for e in raw]
        except Exception as e:
            log.warning("calendar fetch failed: %s", e)
    with session_scope() as s:
        AGENTS["market_intelligence"].run(s, candles=candles, calendar=calendar)


@app.task
def weekly_report():
    from .agents.agents import AGENTS
    from .db import session_scope
    from .notify import notify

    with session_scope() as s:
        out = AGENTS["audit_performance"].run(s, apply=False)
        pf = out.get("profit_factor")
        notify(s, "weekly_report", "Weekly performance report",
               f"Closed trades {out.get('closed_trades')}, realised P&L {out.get('realized_pnl', 0):+.2f}, "
               f"PF {pf:.2f}" if pf else f"Closed trades {out.get('closed_trades')}, realised P&L "
               f"{out.get('realized_pnl', 0):+.2f}", run_id=out.get("_run_id"))


@app.task
def backup():
    """pg_dump to the backup volume; keeps 14 daily dumps."""
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    out_dir = os.environ.get("TITAN_BACKUP_DIR", "/backups")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"titan-{datetime.now(timezone.utc):%Y%m%d-%H%M}.dump")
    subprocess.run(["pg_dump", "-Fc", "-f", path, url], check=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for f in os.listdir(out_dir):
        fp = os.path.join(out_dir, f)
        if f.startswith("titan-") and datetime.fromtimestamp(os.path.getmtime(fp), timezone.utc) < cutoff:
            os.remove(fp)
    return path
