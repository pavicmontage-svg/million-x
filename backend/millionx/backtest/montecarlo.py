"""Monte Carlo analysis of a trade sequence (in R multiples).

Used for (a) drawdown confidence bands, (b) risk of ruin and (c) the
"probability of reaching the goal" shown in the app. All inputs are real
trade results; with no trades the answer is "unknown", never a guess.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import numpy as np


def simulate(r: Sequence[float], risk_fraction: float = 0.005, n_trades: Optional[int] = None,
             runs: int = 5000, start_equity: float = 1.0, goal_multiple: Optional[float] = None,
             ruin_dd: float = 0.5, seed: int = 42) -> dict[str, Any]:
    r = np.asarray(r, dtype=float)
    if len(r) < 20:
        return {"available": False, "reason": f"only {len(r)} trades; need >= 20 for a meaningful simulation"}
    rng = np.random.default_rng(seed)
    n = n_trades or len(r)
    finals = np.empty(runs)
    dds = np.empty(runs)
    hit_goal = 0
    ruined = 0
    chunk = 500
    for start in range(0, runs, chunk):
        m = min(chunk, runs - start)
        samp = rng.choice(r, size=(m, n), replace=True)
        growth = np.clip(1 + risk_fraction * samp, 1e-9, None)
        log_eq = np.cumsum(np.log(growth), axis=1)
        eq = start_equity * np.exp(log_eq)
        peak = np.maximum.accumulate(np.concatenate([np.full((m, 1), start_equity), eq], axis=1), axis=1)[:, 1:]
        dd = 1 - eq / peak
        finals[start:start + m] = eq[:, -1]
        dds[start:start + m] = dd.max(axis=1)
        if goal_multiple:
            hit_goal += int((eq.max(axis=1) >= start_equity * goal_multiple).sum())
        ruined += int((dd.max(axis=1) >= ruin_dd).sum())
    out = {
        "available": True, "runs": runs, "trades_per_run": n, "risk_fraction": risk_fraction,
        "source_trades": int(len(r)), "mean_r": float(r.mean()),
        "final_equity_p5": float(np.percentile(finals, 5)), "final_equity_p50": float(np.percentile(finals, 50)),
        "final_equity_p95": float(np.percentile(finals, 95)),
        "max_dd_p50": float(np.percentile(dds, 50)), "max_dd_p95": float(np.percentile(dds, 95)),
        "max_dd_p99": float(np.percentile(dds, 99)),
        "prob_ruin": ruined / runs, "ruin_threshold_dd": ruin_dd,
    }
    if goal_multiple:
        out["goal_multiple"] = goal_multiple
        out["prob_goal"] = hit_goal / runs
    return out


def goal_probability(r: Sequence[float], start_eur: float, goal_eur: float, trades_per_day: float,
                     days: int, risk_fraction: float, runs: int = 2000) -> dict[str, Any]:
    """Probability of reaching goal_eur within `days`, from validated R results."""
    n = int(max(1, round(trades_per_day * days)))
    res = simulate(r, risk_fraction, n_trades=min(n, 50_000), runs=runs, start_equity=start_eur,
                   goal_multiple=goal_eur / start_eur)
    res["days"] = days
    res["trades_per_day"] = trades_per_day
    res["needed_doublings"] = math.log2(goal_eur / start_eur)
    if n > 50_000:
        res["note"] = "trade count capped at 50,000 for the simulation"
    return res
