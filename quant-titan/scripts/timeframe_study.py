"""Compare execution timeframes (e.g. 5m vs 3m) on identical instruments, period and costs.

    python scripts/timeframe_study.py --tfs 5m,3m --out data/research/timeframe_study.json

Runs, per timeframe: the full system, VWAP engine alone, and VWAP alone with the cost gate relaxed
(research-only view of the raw VWAP signal). Duration parameters are rescaled per timeframe so
time stops, cooldowns and statistics windows mean the same wall-clock time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quanttitan.backtest.engine import Backtester  # noqa: E402
from quanttitan.config import AppConfig, merge_patch, rescale_timeframe  # noqa: E402
from quanttitan.data.history import HistoryStore  # noqa: E402
from quanttitan.research.lab import ResearchLab  # noqa: E402
from quanttitan.research.stats import bootstrap_ci  # noqa: E402

ONLY_VWAP = {"engines": {"trend": {"enabled": False}, "breakout": {"enabled": False},
                         "order_flow": {"enabled": False}, "stat_arb": {"enabled": False}}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT")
    ap.add_argument("--tfs", default="5m,3m")
    ap.add_argument("--out", default="data/research/timeframe_study.json")
    a = ap.parse_args()
    hs = HistoryStore("data/history")
    syms = a.symbols.split(",")
    funding = {s: hs.load_funding(s) for s in syms}
    funding = {s: f for s, f in funding.items() if f is not None}
    variants = {
        "full_system": {},
        "vwap_only": ONLY_VWAP,
        "vwap_only_relaxed_costs": merge_dict(ONLY_VWAP, {"orchestrator": {"max_cost_r": 0.6, "min_rr_after_costs": 0.5}}),
    }
    report: dict = {"symbols": syms, "results": {}}
    for tf in a.tfs.split(","):
        data = {s: hs.load(s, tf) for s in syms}
        data = {s: c for s, c in data.items() if c is not None}
        start = max(int(c.ts[0]) for c in data.values())
        end = min(int(c.close_ts[-1]) for c in data.values())
        for name, patch in variants.items():
            cfg = merge_patch(rescale_timeframe(AppConfig(), tf), patch)
            t = time.time()
            r = Backtester(cfg, data, funding=funding, label=f"{tf}:{name}").run(start + 7 * 86_400_000, end)
            rs = np.array([x.r_multiple for x in r.trades])
            row = {**r.summary(), "expectancy_ci95": bootstrap_ci(rs) if len(rs) >= 5 else None,
                   "by_engine": r.metrics.get("by_engine"), "decisions": r.decisions["outcomes"],
                   "engine_signals": r.metrics.get("engine_signals"), "runtime_s": round(time.time() - t, 1)}
            if len(r.trades) >= 5:
                row["monte_carlo"] = ResearchLab.monte_carlo(r.trades, r.equity0, n=1000)
            report["results"][f"{tf}:{name}"] = row
            print(json.dumps({k: row[k] for k in ("label", "trades", "net_pnl", "return_pct", "win_rate", "profit_factor",
                                                   "expectancy_r", "max_drawdown_pct", "fees", "slippage")}), flush=True)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("written", a.out)


def merge_dict(x: dict, y: dict) -> dict:
    out = json.loads(json.dumps(x))
    for k, v in y.items():
        out[k] = merge_dict(out.get(k, {}), v) if isinstance(v, dict) else v
    return out


if __name__ == "__main__":
    main()
