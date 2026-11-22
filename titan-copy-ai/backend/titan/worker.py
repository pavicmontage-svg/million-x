"""Celery worker + beat (server deployments): wraps the plain jobs in titan/jobs.py.

    celery -A titan.worker worker -B --loglevel=info
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from . import jobs
from .config import get_settings

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
                    "ctrader-tokens": {"task": "titan.worker.refresh_ctrader_tokens", "schedule": 12 * 3600},
                })

refresh_sources = app.task(name="titan.worker.refresh_sources")(jobs.refresh_sources)
rescore_all = app.task(name="titan.worker.rescore_all")(jobs.rescore_all)
run_agent = app.task(name="titan.worker.run_agent")(jobs.run_agent)
market_intelligence = app.task(name="titan.worker.market_intelligence")(jobs.market_intelligence)
weekly_report = app.task(name="titan.worker.weekly_report")(jobs.weekly_report)
backup = app.task(name="titan.worker.backup")(jobs.backup)
refresh_ctrader_tokens = app.task(name="titan.worker.refresh_ctrader_tokens")(jobs.refresh_ctrader_tokens)
