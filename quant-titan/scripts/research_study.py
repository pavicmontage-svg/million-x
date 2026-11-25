"""Run the baseline research study on cached historical data and write a JSON report.

    python scripts/research_study.py --symbols BTCUSDT,ETHUSDT --out data/research/study.json

Nothing here touches live configuration. Every number in the report is computed from simulated
trades on real Binance archive data, including fees, slippage and funding.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quanttitan.config import AppConfig, merge_patch  # noqa: E402
from quanttitan.data.history import HistoryStore  # noqa: E402
from quanttitan.orchestrator.edge import EdgeBook  # noqa: E402
from quanttitan.research.lab import ResearchLab  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT")
    ap.add_argument("--history", default="data/history")
    ap.add_argument("--out", default="data/research/study.json")
    ap.add_argument("--walk-forward", action="store_true")
    a = ap.parse_args()
    hs = HistoryStore(a.history)
    syms = a.symbols.split(",")
    data = {s: hs.load(s, "5m") for s in syms}
    funding = {s: hs.load_funding(s) for s in syms}
    data = {s: c for s, c in data.items() if c is not None}
    cfg = AppConfig()
    lab = ResearchLab(cfg, data, {s: f for s, f in funding.items() if f is not None})
    report: dict = {"symbols": list(data), "bars": {s: len(c) for s, c in data.items()},
                    "period": [int(min(c.ts[0] for c in data.values())), int(max(c.ts[-1] for c in data.values()))]}
    t = time.time()
    base = lab.backtest(cfg, label="default")
    report["default"] = {"summary": base.summary(), "decisions": base.decisions, "regimes": base.regimes,
                         "by_engine": base.metrics.get("by_engine"), "engine_signals": base.metrics.get("engine_signals")}
    print("default", base.summary(), f"{time.time() - t:.0f}s", flush=True)
    # experiment: relax the cost filter to see what the engines would do (research only)
    relaxed = merge_patch(cfg, {"orchestrator": {"max_cost_r": 0.6, "min_rr_after_costs": 0.5}})
    r2 = lab.backtest(relaxed, label="relaxed_costs")
    report["relaxed_costs"] = {"summary": r2.summary(), "by_engine": r2.metrics.get("by_engine"),
                               "by_regime": r2.metrics.get("by_regime"), "by_exit": r2.metrics.get("by_exit"),
                               "long": r2.metrics.get("long"), "short": r2.metrics.get("short"),
                               "monte_carlo": lab.monte_carlo(r2.trades, cfg.system.paper_starting_equity)}
    print("relaxed", r2.summary(), flush=True)
    lab.base = relaxed
    report["policy_comparison"] = lab.policy_comparison()
    print("policies", json.dumps(report["policy_comparison"]), flush=True)
    report["train_test"] = lab.train_test(0.6)
    print("train_test", json.dumps(report["train_test"]), flush=True)
    if a.walk_forward:
        eb = EdgeBook()
        report["walk_forward"] = lab.walk_forward({"orchestrator.max_cost_r": [0.4, 0.6]}, train_days=30, test_days=15, edge_book=eb)
        report["edge_book"] = [e.to_dict() for e in eb.all_estimates()]
    report["trials"] = lab.trials
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("written", a.out, flush=True)


if __name__ == "__main__":
    main()
