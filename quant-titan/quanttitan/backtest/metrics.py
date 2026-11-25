"""Performance metrics. All figures are computed from actual trade records — never estimated."""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..core.types import TradeRecord

DAY_MS = 86_400_000


def _ratio(a: float, b: float) -> float:
    return a / b if b else (math.inf if a > 0 else 0.0)


def equity_curve(trades: list[TradeRecord], equity0: float) -> tuple[np.ndarray, np.ndarray]:
    ts = np.array([t.closed_ts for t in trades], dtype=np.int64)
    eq = equity0 + np.cumsum([t.net_pnl for t in trades]) if trades else np.array([])
    return ts, np.asarray(eq, dtype=float)


def max_drawdown(eq: np.ndarray, equity0: float) -> tuple[float, float]:
    """Return (max drawdown %, max drawdown in currency) of an equity series starting at equity0."""
    if len(eq) == 0:
        return 0.0, 0.0
    full = np.concatenate([[equity0], eq])
    peak = np.maximum.accumulate(full)
    dd = peak - full
    i = int(np.argmax(dd))
    return float(dd[i] / peak[i] * 100) if peak[i] > 0 else 0.0, float(dd[i])


def daily_returns(trades: list[TradeRecord], equity0: float, start_ms: int | None = None, end_ms: int | None = None) -> np.ndarray:
    """Daily returns from closed-trade PnL (days with no trades count as 0% so Sharpe is not inflated)."""
    if not trades:
        return np.array([])
    by_day: dict[int, float] = defaultdict(float)
    for t in trades:
        by_day[t.closed_ts // DAY_MS] += t.net_pnl
    d0 = (start_ms // DAY_MS) if start_ms else min(by_day)
    d1 = (end_ms // DAY_MS) if end_ms else max(by_day)
    eq = equity0
    out = []
    for d in range(d0, d1 + 1):
        pnl = by_day.get(d, 0.0)
        out.append(pnl / eq if eq > 0 else 0.0)
        eq += pnl
    return np.array(out)


def sharpe_sortino(rets: np.ndarray, periods: float = 365.0) -> tuple[float, float]:
    if len(rets) < 2 or np.std(rets, ddof=1) == 0:
        return 0.0, 0.0
    mu, sd = rets.mean(), rets.std(ddof=1)
    down = rets[rets < 0]
    dsd = math.sqrt((down ** 2).sum() / len(rets)) if len(down) else 0.0
    sharpe = mu / sd * math.sqrt(periods)
    sortino = mu / dsd * math.sqrt(periods) if dsd > 0 else (math.inf if mu > 0 else 0.0)
    return float(sharpe), float(sortino)


def _subset(trades: list[TradeRecord]) -> dict:
    if not trades:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0}
    pnl = np.array([t.net_pnl for t in trades]); r = np.array([t.r_multiple for t in trades])
    gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {"trades": len(trades), "net_pnl": round(float(pnl.sum()), 4), "win_rate": round(float((pnl > 0).mean()), 4),
            "expectancy_r": round(float(r.mean()), 4), "profit_factor": round(float(_ratio(gp, gl)), 4)}


def compute_metrics(trades: list[TradeRecord], equity0: float, *, start_ms: int | None = None,
                    end_ms: int | None = None, breakdowns: bool = True) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy_usdt": 0.0, "expectancy_r": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "sortino": 0.0,
                "calmar": 0.0, "fees": 0.0, "funding": 0.0, "slippage": 0.0, "return_pct": 0.0}
    trades = sorted(trades, key=lambda t: t.closed_ts)
    pnl = np.array([t.net_pnl for t in trades])
    r = np.array([t.r_multiple for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    _, eq = equity_curve(trades, equity0)
    dd_pct, dd_abs = max_drawdown(eq, equity0)
    rets = daily_returns(trades, equity0, start_ms, end_ms)
    sharpe, sortino = sharpe_sortino(rets)
    days = max(len(rets), 1)
    total_ret = (eq[-1] - equity0) / equity0
    ann = (1 + total_ret) ** (365 / days) - 1 if total_ret > -1 else -1.0
    hold = np.array([(t.closed_ts - t.opened_ts) / 60_000 for t in trades])
    m = {
        "trades": n,
        "net_pnl": round(float(pnl.sum()), 4),
        "gross_pnl": round(float(sum(t.gross_pnl for t in trades)), 4),
        "return_pct": round(float(total_ret * 100), 4),
        "win_rate": round(float(len(wins) / n), 4),
        "profit_factor": round(float(_ratio(wins.sum(), -losses.sum())), 4),
        "expectancy_usdt": round(float(pnl.mean()), 4),
        "expectancy_r": round(float(r.mean()), 4),
        "r_std": round(float(r.std(ddof=1)), 4) if n > 1 else 0.0,
        "avg_win": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "avg_win_r": round(float(r[r > 0].mean()), 4) if (r > 0).any() else 0.0,
        "avg_loss_r": round(float(r[r < 0].mean()), 4) if (r < 0).any() else 0.0,
        "max_drawdown_pct": round(dd_pct, 4),
        "max_drawdown_usdt": round(dd_abs, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4) if math.isfinite(sortino) else None,
        "calmar": round(float(ann * 100 / dd_pct), 4) if dd_pct > 0 else None,
        "annualized_return_pct": round(float(ann * 100), 4),
        "avg_holding_min": round(float(hold.mean()), 2),
        "fees": round(float(sum(t.fees for t in trades)), 4),
        "funding": round(float(sum(t.funding for t in trades)), 4),
        "slippage": round(float(sum(t.slippage_cost for t in trades)), 4),
        "days": days,
    }
    if breakdowns:
        m["long"] = _subset([t for t in trades if t.direction == "LONG"])
        m["short"] = _subset([t for t in trades if t.direction == "SHORT"])
        for key, attr in (("by_regime", "regime"), ("by_symbol", "symbol"), ("by_engine", "engine"), ("by_exit", "exit_reason")):
            groups: dict[str, list] = defaultdict(list)
            for t in trades:
                groups[getattr(t, attr) or "—"].append(t)
            m[key] = {k: _subset(v) for k, v in sorted(groups.items())}
        hours: dict[str, list] = defaultdict(list)
        for t in trades:
            hours[f"{(t.opened_ts // 3_600_000) % 24:02d}"].append(t)
        m["by_hour_utc"] = {k: _subset(v) for k, v in sorted(hours.items())}
    return m
