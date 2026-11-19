"""Performance metrics computed from real trade records only."""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from ..models import Trade


def max_drawdown(equity: Sequence[float]) -> float:
    e = np.asarray(equity, dtype=float)
    if len(e) == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, 1 - e / peak, 0.0)
    return float(np.nanmax(dd))


def compute(trades: Sequence[Trade], start_equity: float, minutes_span: float | None = None) -> dict[str, Any]:
    n = len(trades)
    out: dict[str, Any] = {"trades": n, "start_equity": start_equity}
    if n == 0:
        out.update(win_rate=None, profit_factor=None, expectancy=None, expectancy_r=None, sharpe=None,
                   sortino=None, max_drawdown=0.0, avg_win=None, avg_loss=None, net_profit=0.0,
                   gross_profit=0.0, gross_loss=0.0, fees=0.0, end_equity=start_equity)
        return out
    pnl = np.array([t.net_pnl for t in trades])
    r = np.array([t.r_multiple for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    eq = start_equity + np.cumsum(pnl)
    rets = pnl / np.concatenate([[start_equity], eq[:-1]])
    sd = rets.std(ddof=1) if n > 1 else 0.0
    dn = rets[rets < 0]
    dsd = math.sqrt((dn ** 2).mean()) if len(dn) else 0.0
    # annualise per-trade ratios with the observed trade frequency
    per_year = None
    if minutes_span and minutes_span > 0:
        per_year = n / (minutes_span / (60 * 24 * 365))
    ann = math.sqrt(per_year) if per_year else 1.0
    out.update(
        win_rate=float((pnl > 0).mean()),
        profit_factor=float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else None),
        expectancy=float(pnl.mean()),
        expectancy_r=float(r.mean()),
        sharpe=float(rets.mean() / sd * ann) if sd > 0 else None,
        sortino=float(rets.mean() / dsd * ann) if dsd > 0 else None,
        max_drawdown=max_drawdown(np.concatenate([[start_equity], eq])),
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        net_profit=float(pnl.sum()),
        gross_profit=gp,
        gross_loss=gl,
        fees=float(sum(t.fees for t in trades)),
        funding=float(sum(t.funding for t in trades)),
        end_equity=float(eq[-1]),
        r_ci95=_ci(r),
    )
    return out


def _ci(x: np.ndarray) -> list[float] | None:
    """95% confidence interval of the mean R (normal approximation)."""
    if len(x) < 10:
        return None
    m, s = x.mean(), x.std(ddof=1) / math.sqrt(len(x))
    return [float(m - 1.96 * s), float(m + 1.96 * s)]


def json_safe(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: json_safe(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return [json_safe(v) for v in d]
    if isinstance(d, float) and (math.isinf(d) or math.isnan(d)):
        return None if math.isnan(d) else ("inf" if d > 0 else "-inf")
    if isinstance(d, (np.floating,)):
        return json_safe(float(d))
    if isinstance(d, (np.integer,)):
        return int(d)
    return d
