"""AGENT 6 — Performance Auditor.

Monitors realised performance per engine and recommends suspension when evidence of deterioration
is statistically meaningful. It has *no* method that increases risk: profitable streaks never raise
size or limits. Parameter changes are never made automatically from small samples.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import stats as sps

from ..backtest.metrics import compute_metrics
from ..core.types import TradeRecord


class PerformanceAuditor:
    name = "performance_auditor"
    title = "Performance Auditor"
    responsibilities = ["Monitor performance", "Detect strategy deterioration", "Analyse win rate, expectancy, profit factor",
                        "Analyse drawdown, Sharpe, Sortino", "Monitor slippage and fees", "Detect anomalies",
                        "Compare actual vs expected execution", "Recommend strategies for suspension"]

    def __init__(self, min_trades: int = 20, recent_window: int = 20, p_threshold: float = 0.10) -> None:
        self.min_trades = min_trades
        self.recent_window = recent_window
        self.p_threshold = p_threshold
        self.last: dict = {}

    def audit(self, trades: list[TradeRecord], equity0: float = 10_000.0) -> dict:
        by_engine: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in trades:
            by_engine[t.engine].append(t)
        engines = {}
        recs = []
        for eng, ts in by_engine.items():
            ts = sorted(ts, key=lambda t: t.closed_ts)
            m = compute_metrics(ts, equity0)
            r = np.array([t.r_multiple for t in ts])
            findings: list[str] = []
            status = "OK"
            if len(r) < self.min_trades:
                status = "INSUFFICIENT_DATA"
                findings.append(f"{len(r)} trades < {self.min_trades}: not statistically reliable")
            else:
                recent = r[-self.recent_window:]
                t_stat, p_two = sps.ttest_1samp(recent, 0.0)
                p_neg = p_two / 2 if t_stat < 0 else 1 - p_two / 2
                if p_neg < self.p_threshold:
                    status = "RECOMMEND_SUSPENSION"
                    findings.append(f"recent {len(recent)} trades mean {recent.mean():+.3f}R, p(mean<0)={p_neg:.3f}")
                if len(r) >= 2 * self.recent_window:
                    older = r[:-self.recent_window]
                    if recent.mean() < older.mean() - 0.5 * older.std(ddof=1):
                        status = "DETERIORATING" if status == "OK" else status
                        findings.append(f"expectancy fell from {older.mean():+.3f}R to {recent.mean():+.3f}R")
                slip = np.array([t.slippage_cost for t in ts]); gross = np.array([abs(t.gross_pnl) for t in ts])
                if len(slip) >= 2 * self.recent_window and slip[-self.recent_window:].mean() > 1.5 * max(slip[:-self.recent_window].mean(), 1e-9):
                    findings.append("slippage rising (recent avg > 1.5× earlier)")
                if m["net_pnl"] < 0 < m["gross_pnl"]:
                    findings.append("profitable before costs, unprofitable after costs")
            # confidence calibration: does higher confidence actually win more?
            calib = None
            conf = np.array([t.confidence for t in ts])
            if len(ts) >= 30 and np.std(conf) > 0:
                rho, p = sps.spearmanr(conf, r)
                calib = {"spearman": round(float(rho), 3), "p": round(float(p), 4)}
                if rho < 0 and p < 0.1:
                    findings.append("confidence score is inversely related to outcomes — recalibration needed")
            engines[eng] = {"status": status, "metrics": m, "findings": findings, "calibration": calib}
            if status in ("RECOMMEND_SUSPENSION", "DETERIORATING"):
                recs.append({"engine": eng, "action": "suspend" if status == "RECOMMEND_SUSPENSION" else "review",
                             "why": findings})
        # correlation between engines' daily results
        daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for t in trades:
            daily[t.engine][str(t.closed_ts // 86_400_000)] += t.net_pnl
        corr = {}
        names = sorted(daily)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                days = sorted(set(daily[a]) | set(daily[b]))
                if len(days) >= 10:
                    x = [daily[a].get(d, 0.0) for d in days]; y = [daily[b].get(d, 0.0) for d in days]
                    if np.std(x) > 0 and np.std(y) > 0:
                        corr[f"{a}/{b}"] = round(float(np.corrcoef(x, y)[0, 1]), 3)
        self.last = {"agent": self.name, "engines": engines, "recommendations": recs, "engine_correlation": corr,
                     "portfolio": compute_metrics(sorted(trades, key=lambda t: t.closed_ts), equity0),
                     "note": "the auditor never increases risk; recommendations require a human decision"}
        return self.last
