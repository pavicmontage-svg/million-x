"""Periodic jobs (plain functions).

Run by Celery beat on a server (titan/worker.py) and by the in-process scheduler when the
backend is embedded in the Android app (titan/scheduler.py).
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import get_settings

log = logging.getLogger("titan.jobs")


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


def backup():
    """Database backup, 14 days retention: pg_dump on PostgreSQL, SQLite online backup on the phone."""
    url = get_settings().database_url
    default_dir = str(get_settings().data_dir / "backups") if get_settings().data_dir else "/backups"
    out_dir = os.environ.get("TITAN_BACKUP_DIR", default_dir)
    os.makedirs(out_dir, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    if url.startswith("sqlite"):
        import sqlite3
        path = os.path.join(out_dir, f"titan-{stamp}.sqlite")
        src = sqlite3.connect(url.split("///", 1)[1])
        dst = sqlite3.connect(path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    else:
        path = os.path.join(out_dir, f"titan-{stamp}.dump")
        subprocess.run(["pg_dump", "-Fc", "-f", path, url.replace("postgresql+psycopg://", "postgresql://")], check=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for f in os.listdir(out_dir):
        fp = os.path.join(out_dir, f)
        if f.startswith("titan-") and datetime.fromtimestamp(os.path.getmtime(fp), timezone.utc) < cutoff:
            os.remove(fp)
    return path


def refresh_ctrader_tokens(days_before: float = 7.0) -> int:
    """cTrader access tokens last ~30 days: refresh them a week before they expire."""
    import time

    from .adapters.forex.ctrader_auth import refresh
    from .db import session_scope
    from .models import Credential
    from .security import decrypt_secret, encrypt_secret
    n = 0
    with session_scope() as s:
        for c in s.scalars(select(Credential).where(Credential.venue == "ctrader")):
            aad = f"{c.venue}|{c.environment}|{c.label}"
            data = decrypt_secret(c.ciphertext, aad)
            if not data.get("refresh_token") or float(data.get("expires_at") or 0) - time.time() > days_before * 86400:
                continue
            try:
                data.update(refresh(data["client_id"], data["client_secret"], data["refresh_token"]))
                c.ciphertext = encrypt_secret(data, aad)
                n += 1
            except Exception as e:
                log.warning("cTrader token refresh failed for %s: %s", c.label, e)
                c.verify_result = {**(c.verify_result or {}), "ok": False, "error": f"token refresh failed: {e}"}
    return n
