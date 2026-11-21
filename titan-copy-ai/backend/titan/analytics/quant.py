"""Quantitative research tools: expectancy, Monte Carlo, walk-forward stability, correlation."""
from __future__ import annotations

import math

import numpy as np


def r_multiples(profits: list[float], risks: list[float | None]) -> list[float]:
    return [p / r for p, r in zip(profits, risks) if r and r > 0]


def monte_carlo(returns_pct: list[float], n_sims: int = 5000, horizon: int | None = None,
                ruin_dd_pct: float = 10.0, seed: int = 7, block: int = 1) -> dict:
    """Bootstrap per-trade % returns (optionally in blocks to keep streaks) and report the
    distribution of final return and max drawdown. Deterministic with a fixed seed."""
    r = np.asarray(returns_pct, dtype=float) / 100
    if len(r) < 10:
        return {"error": "need at least 10 trades for Monte Carlo"}
    rng = np.random.default_rng(seed)
    h = horizon or len(r)
    nblocks = math.ceil(h / block)
    starts = rng.integers(0, max(1, len(r) - block + 1), size=(n_sims, nblocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_sims, -1)[:, :h]
    paths = np.cumprod(1 + r[idx % len(r)], axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((n_sims, 1)), paths], axis=1), axis=1)[:, 1:]
    dd = ((peak - paths) / peak).max(axis=1) * 100
    final = (paths[:, -1] - 1) * 100
    pct = lambda a, q: float(np.percentile(a, q))  # noqa: E731
    return {
        "simulations": n_sims, "horizon_trades": h, "block": block,
        "final_return_pct": {"p5": pct(final, 5), "p25": pct(final, 25), "median": pct(final, 50),
                             "p75": pct(final, 75), "p95": pct(final, 95)},
        "max_drawdown_pct": {"median": pct(dd, 50), "p90": pct(dd, 90), "p95": pct(dd, 95), "p99": pct(dd, 99)},
        "prob_loss": float((final < 0).mean()),
        "prob_drawdown_exceeds": {"threshold_pct": ruin_dd_pct, "probability": float((dd >= ruin_dd_pct).mean())},
    }


def walk_forward(returns_pct: list[float], n_windows: int = 5, train_frac: float = 0.7) -> dict:
    """Rolling in-sample / out-of-sample comparison of a trader's edge.

    For a copy-trading system the 'strategy' is the trader; walk-forward checks whether the edge
    measured in-sample (expectancy, PF, win rate) persists out-of-sample in successive windows.
    """
    r = np.asarray(returns_pct, dtype=float)
    if len(r) < n_windows * 10:
        return {"error": f"need at least {n_windows * 10} trades"}
    size = len(r) // n_windows
    rows = []
    for w in range(n_windows):
        seg = r[w * size:(w + 1) * size] if w < n_windows - 1 else r[w * size:]
        k = int(len(seg) * train_frac)
        ins, oos = seg[:k], seg[k:]
        rows.append({"window": w + 1, "in_sample": _stats(ins), "out_of_sample": _stats(oos)})
    ins_exp = np.asarray([x["in_sample"]["expectancy"] for x in rows])
    oos_exp = np.asarray([x["out_of_sample"]["expectancy"] for x in rows])
    pos_oos = float((oos_exp > 0).mean())
    efficiency = float(oos_exp.mean() / ins_exp.mean()) if ins_exp.mean() > 0 else None
    return {"windows": rows, "oos_positive_share": pos_oos, "walk_forward_efficiency": efficiency,
            "stable": bool(pos_oos >= 0.6 and (efficiency or 0) >= 0.5)}


def _stats(a: np.ndarray) -> dict:
    wins, losses = a[a > 0], a[a < 0]
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None
    return {"trades": int(len(a)), "expectancy": float(a.mean()) if len(a) else 0.0,
            "win_rate": float(len(wins) / len(a) * 100) if len(a) else 0.0, "profit_factor": pf}


def correlation_matrix(daily_returns: dict[str, dict[str, float]], min_overlap: int = 20) -> dict:
    """Pearson correlation of traders' daily returns on overlapping dates."""
    names = sorted(daily_returns)
    out: dict[str, dict[str, float | None]] = {a: {} for a in names}
    for i, a in enumerate(names):
        for b in names[i:]:
            common = sorted(set(daily_returns[a]) & set(daily_returns[b]))
            if a == b:
                out[a][b] = 1.0
                continue
            if len(common) < min_overlap:
                out[a][b] = out[b][a] = None
                continue
            x = np.asarray([daily_returns[a][d] for d in common])
            y = np.asarray([daily_returns[b][d] for d in common])
            c = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None
            out[a][b] = out[b][a] = c
    return out


def expectancy_report(profits: list[float]) -> dict:
    a = np.asarray(profits, dtype=float)
    if not len(a):
        return {}
    s = _stats(a)
    se = float(a.std(ddof=1) / math.sqrt(len(a))) if len(a) > 1 else None
    s["t_stat"] = float(a.mean() / se) if se else None
    s["significant_95"] = bool(s["t_stat"] is not None and s["t_stat"] > 1.96)
    return s
