"""Research Engine 2.0 statistics (multiple-testing aware).

* purged_kfold_ranges: time-ordered K folds; training excludes the test fold, a purge
  window before it (trades still open into the test period) and an embargo after it.
* deflated_sharpe: Bailey & Lopez de Prado (2014) probability that the true Sharpe is
  > 0 after selecting the best of `n_trials` strategies (corrects for multiple testing,
  skewness and kurtosis of returns).
* pbo_cscv: Probability of Backtest Overfitting by combinatorially symmetric cross-
  validation (Bailey et al. 2015) on a T x N matrix of per-period returns of N configs.
* block_bootstrap: expectancy CI and drawdown distribution preserving short-range
  dependence of the trade sequence.
* cost_sensitivity: how much expectancy falls per extra basis point of cost.
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Optional, Sequence

import numpy as np

EULER = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    # Acklam's approximation (sufficient for DSR thresholds)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p = min(max(p, 1e-12), 1 - 1e-12)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def purged_kfold_ranges(t0: int, t1: int, k: int, purge_ms: int, embargo_ms: int) -> list[dict[str, Any]]:
    edges = np.linspace(t0, t1, k + 1).astype(np.int64)
    out = []
    for i in range(k):
        a, b = int(edges[i]), int(edges[i + 1])
        train = []
        if a - purge_ms > t0:
            train.append((t0, a - purge_ms))
        if b + embargo_ms < t1:
            train.append((b + embargo_ms, t1))
        out.append({"test": (a, b), "train": train})
    return out


def sharpe(x: Sequence[float]) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.mean() / x.std(ddof=1)) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0


def deflated_sharpe(returns: Sequence[float], n_trials: int, sr_trials_var: Optional[float] = None) -> dict[str, Any]:
    """Probability that the observed (per-trade) Sharpe is above the best-of-N noise level."""
    r = np.asarray(returns, dtype=float)
    T = len(r)
    if T < 10:
        return {"dsr": None, "reason": "fewer than 10 observations"}
    sr = sharpe(r)
    sk = float(((r - r.mean()) ** 3).mean() / r.std() ** 3) if r.std() > 0 else 0.0
    ku = float(((r - r.mean()) ** 4).mean() / r.std() ** 4) if r.std() > 0 else 3.0
    var = sr_trials_var if sr_trials_var is not None else 1.0 / T
    n = max(1, int(n_trials))
    if n > 1:
        sr0 = math.sqrt(var) * ((1 - EULER) * _norm_ppf(1 - 1 / n) + EULER * _norm_ppf(1 - 1 / (n * math.e)))
    else:
        sr0 = 0.0
    denom = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr))
    z = (sr - sr0) * math.sqrt(T - 1) / denom
    return {"dsr": round(_norm_cdf(z), 4), "sharpe_per_trade": round(sr, 4), "benchmark_sr0": round(sr0, 4),
            "n_trials": n, "T": T, "skew": round(sk, 3), "kurtosis": round(ku, 3)}


def pbo_cscv(perf: np.ndarray, s: int = 8) -> dict[str, Any]:
    """perf: T x N per-period returns of N configurations (T split into s blocks)."""
    perf = np.asarray(perf, dtype=float)
    T, N = perf.shape
    if N < 2 or T < s * 2:
        return {"pbo": None, "reason": "need >= 2 configurations and enough periods"}
    blocks = np.array_split(np.arange(T), s)
    logits = []
    for comb in itertools.combinations(range(s), s // 2):
        is_idx = np.concatenate([blocks[i] for i in comb])
        oos_idx = np.concatenate([blocks[i] for i in range(s) if i not in comb])
        is_perf = perf[is_idx].mean(axis=0)
        oos_perf = perf[oos_idx].mean(axis=0)
        best = int(np.argmax(is_perf))
        rank = (oos_perf < oos_perf[best]).sum() + 0.5 * ((oos_perf == oos_perf[best]).sum() - 1)
        w = (rank + 1) / (N + 1)
        logits.append(math.log(w / (1 - w)))
    lg = np.array(logits)
    return {"pbo": round(float((lg <= 0).mean()), 4), "combinations": len(lg), "configs": N}


def block_bootstrap(r: Sequence[float], risk_fraction: float = 0.005, runs: int = 2000, block: int = 10,
                    seed: int = 7) -> dict[str, Any]:
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n < 20:
        return {"reason": "fewer than 20 trades"}
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    exp, dds = [], []
    for _ in range(runs):
        starts = rng.integers(0, max(1, n - block + 1), nb)
        seq = np.concatenate([r[a:a + block] for a in starts])[:n]
        exp.append(seq.mean())
        eq = np.cumprod(1 + risk_fraction * seq)
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        dds.append(float((1 - np.concatenate([[1.0], eq]) / peak).max()))
    exp, dds = np.array(exp), np.array(dds)
    return {"exp_r_ci95": [round(float(np.percentile(exp, 2.5)), 4), round(float(np.percentile(exp, 97.5)), 4)],
            "p_exp_le_0": round(float((exp <= 0).mean()), 4), "dd_p95": round(float(np.percentile(dds, 95)), 4)}


def cost_sensitivity(scenario_exp: dict[str, float], extra_bps: dict[str, float]) -> dict[str, Any]:
    """Expectancy change per extra basis point of round-trip cost between scenarios."""
    base = scenario_exp.get("BASE")
    out = {}
    for k, v in scenario_exp.items():
        if k == "BASE" or base is None or not extra_bps.get(k):
            continue
        out[k] = round((v - base) / extra_bps[k], 5)
    return {"exp_r_per_extra_bp": out, "survives_stressed": (scenario_exp.get("STRESSED") or -1) > 0}
