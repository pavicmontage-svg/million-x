"""Which execution timeframes produce the best results? (research only — changes nothing live)

    python scripts/multi_tf_study.py --out data/research/multi_tf_study.json

Period A (all timeframes, same window): 8 Jul – 31 Oct 2026 — 1m, 3m, 5m, 15m, 30m, 1h, 4h.
Period B (robustness, 12 months):      8 Nov 2025 – 31 Oct 2026 — 3m, 5m, 15m, 30m, 1h, 4h
                                        (1m omitted: 3M+ bars per run; covered by period A).
15m/30m/1h/4h bars are aggregated exactly from 5m archive bars.

Variants per timeframe:
  shipped  the configuration the bot actually runs (cost gate, risk limits)
  raw      cost gate relaxed and loss limits widened so every engine signal is traded and measured in R
           (signal quality; never a live configuration)
Same instruments, costs (taker fees, modelled slippage + spread, historical funding) and risk-per-trade.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
DAY = 86_400_000
PERIODS = {"A": (1783468800000, 1793491200000), "B": (1762560000000, 1793491200000)}  # 2026-07-08 / 2025-11-08 → 2026-11-01
RAW = {"orchestrator": {"max_cost_r": 0.6, "min_rr_after_costs": 0.5},
       "risk": {"daily_loss_limit_pct": 20.0, "weekly_loss_limit_pct": 40.0, "monthly_drawdown_limit_pct": 60.0,
                "max_consecutive_losses": 50}}


def load(tf: str):
    from quanttitan.data.candles import resample
    from quanttitan.data.history import HistoryStore
    hs = HistoryStore("data/history")
    data = {}
    for s in SYMS:
        if tf in ("1m", "3m", "5m"):
            c = hs.load(s, tf)
        else:
            base = hs.load(s, "5m")
            c = resample(base, tf) if base is not None else None
        if c is not None and len(c):
            data[s] = c
    funding = {s: hs.load_funding(s) for s in SYMS}
    return data, {s: f for s, f in funding.items() if f is not None}


def run(period: str, tf: str, variant: str) -> dict:
    from quanttitan.backtest.engine import Backtester
    from quanttitan.config import AppConfig, merge_patch, rescale_timeframe
    from quanttitan.research.stats import bootstrap_ci
    t0 = time.time()
    data, funding = load(tf)
    start, end = PERIODS[period]
    cfg = rescale_timeframe(AppConfig(), tf)
    if variant == "raw":
        cfg = merge_patch(cfg, RAW)
    r = Backtester(cfg, data, funding=funding, label=f"{period}:{tf}:{variant}").run(start, end)
    tr = sorted(r.trades, key=lambda x: x.closed_ts)
    rs = np.array([t.r_multiple for t in tr])
    months = defaultdict(float)
    for t in tr:
        months[time.strftime("%Y-%m", time.gmtime(t.closed_ts / 1000))] += t.r_multiple
    mid = start + (end - start) // 2
    h1 = [t.r_multiple for t in tr if t.closed_ts < mid]
    h2 = [t.r_multiple for t in tr if t.closed_ts >= mid]
    m = r.metrics
    gross_r = float(sum(t.gross_pnl / t.initial_risk for t in tr if t.initial_risk > 0) / max(len(tr), 1))
    return {
        "period": period, "tf": tf, "variant": variant, "trades": len(tr),
        "return_pct": m.get("return_pct"), "profit_factor": m.get("profit_factor"), "win_rate": m.get("win_rate"),
        "expectancy_r": m.get("expectancy_r"), "gross_expectancy_r": round(gross_r, 4),
        "cost_r_per_trade": round(gross_r - float(rs.mean()), 4) if len(rs) else None,
        "max_drawdown_pct": m.get("max_drawdown_pct"), "sharpe": m.get("sharpe"),
        "fees": m.get("fees"), "slippage": m.get("slippage"), "funding": m.get("funding"),
        "avg_holding_min": m.get("avg_holding_min"),
        "ci95": bootstrap_ci(rs, n=1000) if len(rs) >= 5 else None,
        "months_positive": f"{sum(1 for v in months.values() if v > 0)}/{len(months)}",
        "half1_r": round(float(np.mean(h1)), 4) if h1 else None, "half2_r": round(float(np.mean(h2)), 4) if h2 else None,
        "by_engine": m.get("by_engine"), "by_regime": m.get("by_regime"), "long": m.get("long"), "short": m.get("short"),
        "decisions": r.decisions["outcomes"], "breakers": [e for e in r.breaker_events if e["event"] == "trip"][:5],
        "runtime_s": round(time.time() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/research/multi_tf_study.json")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    jobs = [("A", tf, v) for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h") for v in ("shipped", "raw")]
    jobs += [("B", tf, v) for tf in ("3m", "5m", "15m", "30m", "1h", "4h") for v in ("shipped", "raw")]
    jobs.sort(key=lambda j: {"1m": 0, "3m": 1, "5m": 2}.get(j[1], 3) + (0 if j[0] == "B" else 0.5))  # longest first
    results = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run, *j): j for j in jobs}
        for f in as_completed(futs):
            try:
                res = f.result()
            except Exception as e:  # report, keep going
                res = {"period": futs[f][0], "tf": futs[f][1], "variant": futs[f][2], "error": repr(e)}
            results.append(res)
            print(json.dumps({k: res.get(k) for k in ("period", "tf", "variant", "trades", "return_pct", "profit_factor",
                                                      "expectancy_r", "max_drawdown_pct", "runtime_s", "error")}), flush=True)
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            with open(a.out, "w") as fh:
                json.dump(results, fh, indent=1, default=str)
    print("written", a.out, flush=True)


if __name__ == "__main__":
    main()
