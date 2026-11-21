"""Trader performance metrics computed from source data.

Inputs are the trader's *published* closed trades and/or equity curve. A metric that cannot
be computed from the available data is returned as None (rendered as UNKNOWN) - never estimated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

METRIC_FIELDS = [
    "account_age_days", "roi_7d", "roi_30d", "roi_90d", "roi_180d", "roi_365d",
    "max_equity_drawdown_pct", "max_balance_drawdown_pct", "win_rate", "profit_factor",
    "avg_risk_reward", "avg_trade_duration_h", "trades_per_day", "avg_win", "avg_loss",
    "max_consecutive_losses", "max_leverage", "open_exposure", "current_positions",
    "trading_fees", "slippage", "active_copiers", "last_activity", "total_trades",
    "expectancy", "sharpe", "sortino", "calmar", "recovery_factor", "monthly_consistency",
    "return_volatility_ann", "exposure_concentration", "sl_usage_rate", "net_profit",
    "months_of_history",
]


@dataclass
class TradeRow:
    symbol: str
    side: str
    volume: float | None
    open_time: datetime
    close_time: datetime | None
    open_price: float | None
    close_price: float | None
    profit: float | None
    sl: float | None = None
    commission: float | None = None
    swap: float | None = None


@dataclass
class EquityRow:
    ts: datetime
    balance: float | None = None
    equity: float | None = None
    growth_pct: float | None = None   # cumulative growth % (Myfxbook/Darwinex-style, deposit-adjusted)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def max_drawdown_pct(values: list[float]) -> float | None:
    v = np.asarray([x for x in values if x is not None and x > 0], dtype=float)
    if len(v) < 2:
        return None
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak) * 100)


def index_from_curve(curve: list[EquityRow], field: str) -> list[tuple[datetime, float]]:
    """Deposit-neutral index series: prefer growth%, else raw balance/equity."""
    out = []
    for p in curve:
        if field == "growth" and p.growth_pct is not None:
            out.append((_aware(p.ts), 100.0 * (1 + p.growth_pct / 100)))
        elif field != "growth" and getattr(p, field) is not None:
            out.append((_aware(p.ts), float(getattr(p, field))))
    return sorted(out)


def _roi(series: list[tuple[datetime, float]], days: int, now: datetime) -> float | None:
    if len(series) < 2:
        return None
    start_t = now - timedelta(days=days)
    if series[0][0] > start_t + timedelta(days=max(1, days * 0.05)):
        return None  # history shorter than the window -> UNKNOWN
    before = [v for t, v in series if t <= start_t]
    base = before[-1] if before else series[0][1]
    return (series[-1][1] / base - 1) * 100 if base else None


def _daily_returns(series: list[tuple[datetime, float]]) -> np.ndarray:
    by_day: dict = {}
    for t, v in series:
        by_day[t.date()] = v
    vals = np.asarray([by_day[k] for k in sorted(by_day)], dtype=float)
    if len(vals) < 3:
        return np.asarray([])
    return vals[1:] / vals[:-1] - 1


def compute_metrics(trades: list[TradeRow], curve: list[EquityRow], now: datetime | None = None,
                    published: dict | None = None) -> dict:
    """Return a dict with every METRIC_FIELDS key (None == UNKNOWN).

    `published` holds metrics reported directly by the official source (e.g. Myfxbook drawdown,
    Darwinex copier count). Computed values take precedence only where the source has none.
    """
    now = _aware(now or datetime.now(timezone.utc))
    m: dict = {k: None for k in METRIC_FIELDS}
    closed = [t for t in trades if t.close_time is not None and t.profit is not None]
    closed.sort(key=lambda t: _aware(t.close_time))

    # ---------------- trade statistics
    if closed:
        p = np.asarray([t.profit for t in closed], dtype=float)
        wins, losses = p[p > 0], p[p < 0]
        m["total_trades"] = len(closed)
        m["win_rate"] = float(len(wins) / len(p) * 100)
        m["avg_win"] = float(wins.mean()) if len(wins) else None
        m["avg_loss"] = float(losses.mean()) if len(losses) else None
        if len(losses):
            m["profit_factor"] = float(wins.sum() / -losses.sum()) if len(wins) else 0.0
        if m["avg_win"] is not None and m["avg_loss"]:
            m["avg_risk_reward"] = float(m["avg_win"] / -m["avg_loss"])
        m["expectancy"] = float(p.mean())
        m["net_profit"] = float(p.sum())
        streak = best = 0
        for x in p:
            streak = streak + 1 if x < 0 else 0
            best = max(best, streak)
        m["max_consecutive_losses"] = best
        durs = [(_aware(t.close_time) - _aware(t.open_time)).total_seconds() / 3600 for t in closed]
        m["avg_trade_duration_h"] = float(np.mean(durs))
        first, last = _aware(closed[0].open_time), _aware(closed[-1].close_time)
        span_days = max(1.0, (last - first).total_seconds() / 86400)
        m["trades_per_day"] = float(len(closed) / span_days)
        m["last_activity"] = last.isoformat()
        sym_counts: dict[str, int] = {}
        for t in closed:
            sym_counts[t.symbol] = sym_counts.get(t.symbol, 0) + 1
        shares = np.asarray(list(sym_counts.values()), dtype=float) / len(closed)
        m["exposure_concentration"] = float((shares ** 2).sum())   # Herfindahl index 0..1
        fees = [abs(t.commission or 0) + max(0.0, -(t.swap or 0)) for t in closed
                if t.commission is not None or t.swap is not None]
        if fees:
            m["trading_fees"] = float(sum(fees))
        with_sl_info = [t for t in closed if t.sl is not None]
        if with_sl_info or any(t.sl == 0 for t in closed):
            m["sl_usage_rate"] = float(sum(1 for t in closed if t.sl) / len(closed) * 100)

    # ---------------- equity curve statistics
    growth = index_from_curve(curve, "growth")
    bal = index_from_curve(curve, "balance")
    eq = index_from_curve(curve, "equity")
    idx = growth or eq or bal
    if idx:
        first_t = idx[0][0]
        if closed:
            first_t = min(first_t, _aware(closed[0].open_time))
        m["account_age_days"] = (now - first_t).days
        m["months_of_history"] = round((now - first_t).days / 30.44, 1)
        for d in (7, 30, 90, 180, 365):
            m[f"roi_{d}d"] = _roi(idx, d, now)
        rets = _daily_returns(idx)
        if len(rets) >= 20:
            sd = rets.std(ddof=1)
            m["return_volatility_ann"] = float(sd * math.sqrt(252) * 100)
            if sd > 0:
                m["sharpe"] = float(rets.mean() / sd * math.sqrt(252))
            downside = rets[rets < 0]
            if len(downside) >= 2 and downside.std(ddof=1) > 0:
                dd_dev = math.sqrt(float((downside ** 2).mean()))
                m["sortino"] = float(rets.mean() / dd_dev * math.sqrt(252))
        # monthly consistency: share of calendar months with a positive return
        month_end: dict = {}
        for t, v in idx:
            month_end[(t.year, t.month)] = v
        keys = sorted(month_end)
        if len(keys) >= 3:
            vals = [month_end[k] for k in keys]
            mrets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
            m["monthly_consistency"] = float(sum(1 for r in mrets if r > 0) / len(mrets) * 100)
    elif closed:
        m["account_age_days"] = (now - _aware(closed[0].open_time)).days
        m["months_of_history"] = round(m["account_age_days"] / 30.44, 1)

    if eq:
        m["max_equity_drawdown_pct"] = max_drawdown_pct([v for _, v in eq])
    elif growth and not bal:
        # A deposit-adjusted growth curve sampled at closes measures balance-based drawdown only.
        m["max_balance_drawdown_pct"] = max_drawdown_pct([v for _, v in growth])
    if bal:
        m["max_balance_drawdown_pct"] = max_drawdown_pct([v for _, v in bal])

    dd = m["max_equity_drawdown_pct"] if m["max_equity_drawdown_pct"] is not None else m["max_balance_drawdown_pct"]
    if idx and dd and dd > 0 and m["account_age_days"]:
        total = idx[-1][1] / idx[0][1] - 1
        years = max(m["account_age_days"] / 365.25, 1 / 12)
        ann = (1 + total) ** (1 / years) - 1 if total > -1 else -1
        m["calmar"] = float(ann * 100 / dd)
        m["recovery_factor"] = float(total * 100 / dd)

    # Leverage: needs notional at entry vs balance at that time (only when both are published).
    if closed and bal:
        levs = []
        for t in closed:
            if t.volume and t.open_price:
                b = [v for ts, v in bal if ts <= _aware(t.open_time)]
                if b and b[-1] > 0:
                    levs.append(t.volume * t.open_price / b[-1])
        # Volume units differ per platform (lots vs units); only report when the source gives units.
        if levs and published and published.get("volume_unit") == "units":
            m["max_leverage"] = float(max(levs))

    for k, v in (published or {}).items():
        if k in m and v is not None and m[k] is None:
            m[k] = v
    return m
