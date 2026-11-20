"""Strategy Selection Engine (MillionX 2.0, phase 5).

Chooses, per (instrument, regime) cell, the strategy with the best *shrunk, decayed,
penalised* out-of-sample expectancy. A cell gets "NO_TRADE" unless a strategy:
  * has at least `min_n` OOS trades in that cell,
  * has a positive lower 95 % confidence bound on its decayed expectancy,
  * keeps a positive score after Bayesian shrinkage and all penalties,
  * passed the Research Engine 2.0 verdict (CANDIDATE_FOR_PAPER) overall.
The engine is deterministic and never reads the final holdout; the LLM has no input.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np

DAY = 86_400_000
NO_TRADE = "NO_TRADE"


@dataclass
class SelectionConfig:
    min_n: int = 30
    prior_strength: float = 50.0      # pseudo-trades pulling the mean toward the prior (0 R)
    prior_mean: float = 0.0
    half_life_days: float = 60.0      # decay of old evidence
    corr_penalty: float = 0.5         # x max daily-R correlation with an already chosen strategy
    dd_penalty: float = 0.5           # x max drawdown (fraction at 0.5 % risk)
    turnover_penalty_r: float = 0.002  # R per trade/day (crowding of the cost budget)
    z: float = 1.96


def decayed_stats(r: np.ndarray, t_ms: np.ndarray, now_ms: int, half_life_days: float) -> tuple[float, float, float]:
    """Weighted mean, its standard error and the effective sample size."""
    if len(r) == 0:
        return float("nan"), float("nan"), 0.0
    age = np.maximum(0, (now_ms - t_ms) / DAY)
    w = 0.5 ** (age / half_life_days)
    sw = w.sum()
    m = float((w * r).sum() / sw)
    n_eff = float(sw ** 2 / (w ** 2).sum())
    var = float((w * (r - m) ** 2).sum() / sw)
    se = math.sqrt(var / n_eff) if n_eff > 1 else float("inf")
    return m, se, n_eff


def shrink(mean: float, n_eff: float, prior_mean: float, k: float) -> float:
    return (n_eff * mean + k * prior_mean) / (n_eff + k)


def _max_dd(r: np.ndarray, risk: float = 0.005) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumprod(np.concatenate([[1.0], 1 + risk * r]))
    peak = np.maximum.accumulate(eq)
    return float(((peak - eq) / peak).max())


def _daily(r: np.ndarray, t_ms: np.ndarray) -> dict[int, float]:
    d: dict[int, float] = defaultdict(float)
    for x, t in zip(r, t_ms):
        d[int(t // DAY)] += float(x)
    return d


def _corr(a: dict[int, float], b: dict[int, float]) -> float:
    days = sorted(set(a) | set(b))
    if len(days) < 20:
        return 0.0
    x = np.array([a.get(k, 0.0) for k in days])
    y = np.array([b.get(k, 0.0) for k in days])
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def select(trades: Iterable[dict[str, Any]], eligible: set[str], now_ms: int,
           cfg: Optional[SelectionConfig] = None) -> dict[str, Any]:
    """trades: dicts with strategy, symbol, regime, r, closed_at (OOS trades only).
    Returns {cells: {(symbol|regime): {...}}, ranked candidates with every component}."""
    cfg = cfg or SelectionConfig()
    by: dict[tuple[str, str, str], list[tuple[float, int]]] = defaultdict(list)
    by_strat: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for t in trades:
        by[(t["symbol"], t["regime"], t["strategy"])].append((float(t["r"]), int(t["closed_at"])))
        by_strat[t["strategy"]].append((float(t["r"]), int(t["closed_at"])))
    daily = {s: _daily(np.array([x[0] for x in v]), np.array([x[1] for x in v])) for s, v in by_strat.items()}
    cells: dict[str, dict[str, Any]] = {}
    keys = sorted({(s, g) for s, g, _ in by})
    for sym, reg in keys:
        cands = []
        for (s2, g2, strat), v in by.items():
            if (s2, g2) != (sym, reg):
                continue
            r = np.array([x[0] for x in v])
            t = np.array([x[1] for x in v])
            m, se, n_eff = decayed_stats(r, t, now_ms, cfg.half_life_days)
            post = shrink(m, n_eff, cfg.prior_mean, cfg.prior_strength)
            lo = m - cfg.z * se
            span_days = max(1.0, (t.max() - t.min()) / DAY) if len(t) > 1 else 1.0
            turnover = len(r) / span_days
            dd = _max_dd(r[np.argsort(t)])
            score = post - cfg.dd_penalty * dd - cfg.turnover_penalty_r * turnover
            reasons = []
            if strat not in eligible:
                reasons.append("not validated (Research 2.0 verdict)")
            if len(r) < cfg.min_n:
                reasons.append(f"sample {len(r)} < {cfg.min_n}")
            if not lo > 0:
                reasons.append("CI lower bound <= 0")
            cands.append({"strategy": strat, "n": len(r), "n_eff": round(n_eff, 1), "mean_r": round(m, 4),
                          "ci95_low": round(lo, 4), "shrunk_r": round(post, 4), "max_dd": round(dd, 4),
                          "turnover_per_day": round(turnover, 2), "score": round(score, 4),
                          "ok": not reasons, "reasons": reasons})
        cands.sort(key=lambda c: c["score"], reverse=True)
        cells[f"{sym}|{reg}"] = {"symbol": sym, "regime": reg, "candidates": cands}
    # correlation penalty: greedy, strongest cells first, against strategies already chosen
    chosen: list[str] = []
    for key in sorted(cells, key=lambda k: -max([c["score"] for c in cells[k]["candidates"]] or [-9])):
        cell = cells[key]
        best = None
        for c in cell["candidates"]:
            pen = cfg.corr_penalty * max([_corr(daily[c["strategy"]], daily[o]) for o in chosen
                                          if o != c["strategy"]] or [0.0])
            c["corr_penalty"] = round(max(0.0, pen), 4)
            c["final_score"] = round(c["score"] - max(0.0, pen), 4)
            if c["ok"] and c["final_score"] > 0 and (best is None or c["final_score"] > best["final_score"]):
                best = c
        cell["selected"] = best["strategy"] if best else NO_TRADE
        cell["why"] = (f"{best['strategy']}: shrunk {best['shrunk_r']:+.3f}R, CI low {best['ci95_low']:+.3f}R, "
                       f"n={best['n']}") if best else "no strategy passes sample, CI and score requirements"
        if best and best["strategy"] not in chosen:
            chosen.append(best["strategy"])
    return {"cells": cells, "chosen_strategies": chosen,
            "no_trade_cells": sum(c["selected"] == NO_TRADE for c in cells.values()), "config": cfg.__dict__}
