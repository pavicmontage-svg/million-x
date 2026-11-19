"""Walk-forward validation, robust parameter selection and bot activation.

Anti-overfitting measures:
  * parameters are chosen on the in-sample window only and judged on the next,
    unseen window (out-of-sample),
  * the objective is risk-adjusted (expectancy in R x sqrt(n) minus a drawdown
    penalty, minimum trade count) — not raw profit,
  * a parameter set is scored with its grid neighbours (plateau preference),
  * grids are small (<= 27 combos per bot) to limit data snooping,
  * activation needs OOS + Monte Carlo + paper-trading evidence.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from ..config import ActivationCriteria, Settings
from ..models import Bars, BotStatus, MINUTE_MS, Trade
from ..strategies import BASE_CLASSES, build_bots
from . import metrics, montecarlo
from .engine import BacktestConfig, run

DAY_MS = 86_400_000


def _slice(data: dict[str, Bars], start_ms: int, end_ms: int) -> dict[str, Bars]:
    out = {}
    for s, b in data.items():
        i0 = int(np.searchsorted(b.open_time, start_ms))
        i1 = int(np.searchsorted(b.open_time, end_ms))
        if i1 - i0 > 1000:
            out[s] = b.slice(i0, i1)
    return out


def _grid(cls, max_combos: int = 27) -> list[dict[str, Any]]:
    keys = list(cls.param_grid)
    combos = [dict(zip(keys, v)) for v in itertools.product(*(cls.param_grid[k] for k in keys))]
    return combos[:max_combos] if combos else [{}]


def objective(trades: list[Trade], min_trades: int = 30) -> float:
    if len(trades) < min_trades:
        return -1e9
    r = np.array([t.r_multiple for t in trades])
    eq = np.cumprod(1 + 0.005 * r)
    dd = metrics.max_drawdown(np.concatenate([[1.0], eq]))
    return float(r.mean() * math.sqrt(len(r)) - 3.0 * dd)


def _virtual(data, settings, bot_id, params, warm_start_ms) -> list[Trade]:
    cfg = BacktestConfig(bots=[bot_id] if bot_id != "bot10_adaptive" else None,
                         params={bot_id: params} if params else {}, virtual_only=True, daily_competition=False)
    res = run(data, settings, cfg)
    return [t for t in res.virtual_trades if t.bot_id == bot_id and t.opened_at >= warm_start_ms]


@dataclass
class WFResult:
    bot_id: str
    windows: list[dict[str, Any]]
    oos_trades: list[Trade]
    oos_metrics: dict[str, Any]
    monte_carlo: dict[str, Any]
    chosen_params: dict[str, Any]
    positive_window_share: float
    activation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return metrics.json_safe({"bot_id": self.bot_id, "windows": self.windows, "oos_metrics": self.oos_metrics,
                                  "monte_carlo": self.monte_carlo, "chosen_params": self.chosen_params,
                                  "positive_window_share": self.positive_window_share,
                                  "activation": self.activation})


def walk_forward(data: dict[str, Bars], settings: Settings, bot_id: str, train_days: int = 60,
                 test_days: int = 20, warmup_days: int = 3, max_windows: Optional[int] = None,
                 progress=None) -> WFResult:
    cls = next((c for c in BASE_CLASSES if c.bot_id == bot_id), None)
    grid = _grid(cls) if cls else [{}]
    t0 = min(int(b.open_time[0]) for b in data.values())
    t1 = max(int(b.open_time[-1]) for b in data.values())
    windows, oos, chosen_hist = [], [], []
    start = t0 + warmup_days * DAY_MS
    k = 0
    while start + (train_days + test_days) * DAY_MS <= t1:
        tr_a, tr_b = start, start + train_days * DAY_MS
        te_b = tr_b + test_days * DAY_MS
        train = _slice(data, tr_a - warmup_days * DAY_MS, tr_b)
        scores = []
        for p in grid:
            trs = _virtual(train, settings, bot_id, p, tr_a)
            scores.append(objective(trs))
        # plateau: average with neighbours differing in one parameter
        smoothed = []
        for i, p in enumerate(grid):
            neigh = [scores[j] for j, q in enumerate(grid)
                     if sum(p.get(x) != q.get(x) for x in p) <= 1 and scores[j] > -1e8]
            smoothed.append(np.mean(neigh) if neigh and scores[i] > -1e8 else -1e9)
        best = int(np.argmax(smoothed))
        params = grid[best]
        test = _slice(data, tr_b - warmup_days * DAY_MS, te_b)
        trs = _virtual(test, settings, bot_id, params, tr_b)
        oos.extend(trs)
        m = metrics.compute(trs, 100.0)
        windows.append({"train_from": tr_a, "train_to": tr_b, "test_to": te_b, "params": params,
                        "in_sample_score": scores[best], "oos_trades": m["trades"],
                        "oos_expectancy_r": m["expectancy_r"], "oos_pf": m["profit_factor"]})
        chosen_hist.append(params)
        if progress:
            progress(bot_id, k, windows[-1])
        k += 1
        start += test_days * DAY_MS
        if max_windows and k >= max_windows:
            break
    om = metrics.compute(oos, 100.0)
    mc = montecarlo.simulate([t.r_multiple for t in oos], settings.risk.risk_per_trade)
    pos = [w for w in windows if (w["oos_expectancy_r"] or 0) > 0]
    share = len(pos) / len(windows) if windows else 0.0
    final_params = chosen_hist[-1] if chosen_hist else {}
    act = evaluate_activation(om, mc, share, settings.activation)
    return WFResult(bot_id, windows, oos, om, mc, final_params, share, act)


def evaluate_activation(m: dict[str, Any], mc: dict[str, Any], positive_share: float,
                        c: ActivationCriteria, paper: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    checks = {
        "oos_trades": (m["trades"] >= c.min_oos_trades, f"{m['trades']} >= {c.min_oos_trades}"),
        "profit_factor": ((m["profit_factor"] or 0) >= c.min_profit_factor,
                          f"{m['profit_factor']} >= {c.min_profit_factor}"),
        "expectancy_r": ((m["expectancy_r"] or -1) >= c.min_expectancy_r,
                         f"{m['expectancy_r']} >= {c.min_expectancy_r}"),
        "max_drawdown": (m["max_drawdown"] <= c.max_drawdown, f"{m['max_drawdown']:.3f} <= {c.max_drawdown}"),
        "mc_dd_p95": (mc.get("available") and mc["max_dd_p95"] <= c.mc_dd_p95_max,
                      f"{mc.get('max_dd_p95')} <= {c.mc_dd_p95_max}"),
        "positive_windows": (positive_share >= c.min_positive_wf_windows,
                             f"{positive_share:.2f} >= {c.min_positive_wf_windows}"),
    }
    backtest_ok = all(v[0] for v in checks.values())
    status = BotStatus.BACKTEST_PASSED if backtest_ok else BotStatus.RESEARCH
    if backtest_ok and paper is not None:
        pok = paper.get("trades", 0) >= c.min_paper_trades and (paper.get("profit_factor") or 0) >= c.min_paper_pf
        checks["paper"] = (pok, f"{paper.get('trades')} trades, PF {paper.get('profit_factor')}")
        status = BotStatus.LIVE_ELIGIBLE if pok else BotStatus.PAPER
    return {"status": status.value, "checks": {k: {"pass": bool(v[0]), "detail": v[1]} for k, v in checks.items()}}
