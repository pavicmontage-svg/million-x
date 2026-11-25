"""Statistical tools for robustness and overfitting control."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps

EULER_GAMMA = 0.5772156649015329


def probabilistic_sharpe(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & López de Prado): P(true per-period Sharpe > benchmark) given sample moments."""
    r = np.asarray(returns, float)
    r = r[np.isfinite(r)]
    t = len(r)
    if t < 3 or r.std(ddof=1) == 0:
        return 0.0
    sr = r.mean() / r.std(ddof=1)
    skew = float(sps.skew(r))
    kurt = float(sps.kurtosis(r, fisher=False))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return float(sps.norm.cdf((sr - sr_benchmark) * math.sqrt(t - 1) / denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum per-period Sharpe among n_trials independent strategies with zero true Sharpe."""
    if n_trials <= 1:
        return 0.0
    z1 = sps.norm.ppf(1 - 1 / n_trials)
    z2 = sps.norm.ppf(1 - 1 / (n_trials * math.e))
    return math.sqrt(max(sr_variance, 0.0)) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(returns: np.ndarray, n_trials: int, sr_variance: float | None = None) -> dict:
    """Deflated Sharpe Ratio: PSR against the Sharpe expected from the best of ``n_trials`` random
    strategies. DSR < 0.95 means the result is not distinguishable from selection luck."""
    r = np.asarray(returns, float)
    r = r[np.isfinite(r)]
    t = len(r)
    if sr_variance is None:
        sr_variance = 1.0 / max(t, 1)  # variance of the Sharpe estimator under the null
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    sr = float(r.mean() / r.std(ddof=1)) if t > 2 and r.std(ddof=1) > 0 else 0.0
    return {"sharpe_per_period": sr, "sr_benchmark": sr0, "n_trials": n_trials,
            "dsr": probabilistic_sharpe(r, sr0), "psr_vs_zero": probabilistic_sharpe(r, 0.0), "observations": t}


def bootstrap_ci(values: np.ndarray, stat=np.mean, n: int = 2000, alpha: float = 0.05, seed: int = 0) -> dict:
    v = np.asarray(values, float)
    if len(v) < 5:
        return {"estimate": float(stat(v)) if len(v) else 0.0, "lo": float("nan"), "hi": float("nan"), "p_le_zero": float("nan")}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(n, len(v)))
    boots = np.apply_along_axis(stat, 1, v[idx])
    return {"estimate": float(stat(v)), "lo": float(np.quantile(boots, alpha / 2)), "hi": float(np.quantile(boots, 1 - alpha / 2)),
            "p_le_zero": float((boots <= 0).mean())}


def information_coefficient(feature: np.ndarray, forward_return: np.ndarray) -> dict:
    """Spearman rank correlation between a feature and the subsequent return (both aligned per bar)."""
    f = np.asarray(feature, float); r = np.asarray(forward_return, float)
    m = np.isfinite(f) & np.isfinite(r)
    if m.sum() < 30:
        return {"ic": float("nan"), "p": float("nan"), "n": int(m.sum())}
    ic, p = sps.spearmanr(f[m], r[m])
    return {"ic": float(ic), "p": float(p), "n": int(m.sum())}


def overfit_ratio(in_sample: float, out_of_sample: float) -> float:
    """OOS / IS performance ratio (e.g. of expectancy). < 0.5 suggests overfitting."""
    if in_sample <= 0:
        return float("nan")
    return out_of_sample / in_sample
