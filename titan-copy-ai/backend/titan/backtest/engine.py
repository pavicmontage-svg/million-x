"""Copy-trading backtester: replays a trader's real historical trades through TITAN's copy rules.

Every result is SIMULATED. The replay is R-based (outcome of each source trade measured in units of
its own stop distance), so it needs no invented historical FX rates. Costs are converted to R
using the trade's own stop distance in pips; when the instrument's pip value in the account
currency cannot be derived exactly (cross rates at that time are unknown), the cost model falls
back to `commission_r` and the result is flagged `cost_model: approximate`.

Simulated frictions: signal delay (entry drift drawn from the distribution of slippage_r scaled by
sqrt(delay)), spread, commission, adverse slippage, partial fills, venue rejections, network
failures and broker disconnections (missed trades), and TITAN's risk rules (0.5% risk, daily
loss limit, drawdown de-risking, 10% max-drawdown lockout, no trade without a stop-loss).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass

import numpy as np

from ..analytics.metrics import EquityRow, TradeRow
from ..analytics.quant import monte_carlo, walk_forward
from ..markets import forex as fx
from ..risk.engine import RiskLimits


@dataclass
class BacktestConfig:
    capital: float = 500.0
    currency: str = "EUR"
    market: str = "forex"
    delay_ms: float = 1500
    spread_pips: float = 0.5
    commission_per_lot: float = 7.0
    commission_r: float = 0.03
    slippage_r: float = 0.02
    partial_fill_prob: float = 0.0
    failure_prob: float = 0.0
    disconnect_prob: float = 0.0
    seed: int = 7
    fx_rates: dict | None = None     # optional mids for exact pip values, e.g. {"EURUSD": 1.08}


def _r_outcome(t: TradeRow) -> float | None:
    if not (t.sl and t.open_price and t.close_price):
        return None
    dist = abs(t.open_price - t.sl)
    if dist <= 0:
        return None
    d = 1 if t.side == "buy" else -1
    return d * (t.close_price - t.open_price) / dist


def run_backtest(trades: list[TradeRow], cfg: BacktestConfig, curve: list[EquityRow] | None = None) -> dict:
    rng = random.Random(cfg.seed)
    lim = RiskLimits.for_market(cfg.market)
    seq = sorted([t for t in trades if t.close_time], key=lambda t: t.open_time)
    eq, peak = cfg.capital, cfg.capital
    day, day_start = None, cfg.capital
    curve_out, rs, pnl_list = [], [], []
    stats = {"source_trades": len(seq), "executed": 0, "skipped_no_stop_loss": 0, "rejected_daily_limit": 0,
             "rejected_max_drawdown": 0, "rejected_min_volume": 0, "venue_rejections": 0, "missed_disconnect": 0,
             "partial_fills": 0, "fees": 0.0, "slippage_cost": 0.0}
    approximate = False
    locked = False
    for t in seq:
        r = _r_outcome(t)
        if r is None:
            stats["skipped_no_stop_loss"] += 1
            continue
        dkey = t.open_time.date()
        if dkey != day:
            day, day_start = dkey, eq
        if locked:
            stats["rejected_max_drawdown"] += 1
            continue
        if (day_start - eq) / day_start * 100 >= lim.daily_loss_pct:
            stats["rejected_daily_limit"] += 1
            continue
        if rng.random() < cfg.disconnect_prob:
            stats["missed_disconnect"] += 1
            continue
        if rng.random() < cfg.failure_prob:
            stats["venue_rejections"] += 1
            continue
        dd = (peak - eq) / peak * 100
        mult = 1.0
        for th, m in sorted(lim.drawdown_steps):
            if dd >= th:
                mult = min(mult, m)
        budget = eq * lim.risk_per_trade_pct / 100 * mult
        budget = min(budget, day_start * lim.daily_loss_pct / 100 - (day_start - eq))
        if budget <= 0:
            stats["rejected_daily_limit"] += 1
            continue
        dist = abs(t.open_price - t.sl)
        # --- costs in R
        cost_r, fill_frac = 0.0, 1.0
        lots = None
        try:
            sp = fx.spec(t.symbol)
            spread_r = cfg.spread_pips * sp.pip_size / dist
            if cfg.fx_rates:
                vpu = fx.value_per_price_unit(sp, 1.0, cfg.currency, cfg.fx_rates)
                raw_lots = budget / (dist * vpu)
                lots = fx.floor_step(raw_lots, sp.volume_step)
                if lots < sp.min_volume:
                    stats["rejected_min_volume"] += 1
                    continue
                fill_frac = lots / raw_lots   # volume rounding reduces risk below budget
                comm = fx.convert(cfg.commission_per_lot * lots, "USD", cfg.currency, cfg.fx_rates)
                comm_r = comm / (lots * dist * vpu)
            else:
                comm_r = cfg.commission_r
                approximate = True
        except KeyError:
            spread_r, comm_r = 0.0, cfg.commission_r
            approximate = True
        delay_r = abs(rng.gauss(0, cfg.slippage_r * math.sqrt(max(cfg.delay_ms, 1) / 1000)))
        cost_r = spread_r + comm_r + delay_r
        if rng.random() < cfg.partial_fill_prob:
            fill_frac *= rng.uniform(0.4, 0.9)
            stats["partial_fills"] += 1
        risk_used = budget * fill_frac
        pnl = (r - cost_r) * risk_used
        stats["fees"] += comm_r * risk_used
        stats["slippage_cost"] += (delay_r + spread_r) * risk_used
        eq += pnl
        peak = max(peak, eq)
        stats["executed"] += 1
        rs.append(r - cost_r)
        pnl_list.append(pnl)
        curve_out.append({"t": t.close_time.isoformat(), "equity": round(eq, 4)})
        if (peak - eq) / peak * 100 >= lim.max_drawdown_pct:
            locked = True
    eqs = np.asarray([cfg.capital] + [c["equity"] for c in curve_out])
    pk = np.maximum.accumulate(eqs)
    max_dd = float(((pk - eqs) / pk).max() * 100) if len(eqs) > 1 else 0.0
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]
    rets = np.diff(eqs) / eqs[:-1] if len(eqs) > 2 else np.asarray([])
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252)) if len(rets) > 2 and rets.std(ddof=1) > 0 else None
    down = rets[rets < 0]
    sortino = float(rets.mean() / math.sqrt((down ** 2).mean()) * math.sqrt(252)) if len(down) > 1 else None
    eligible = stats["source_trades"] - stats["skipped_no_stop_loss"]
    return {
        "simulated": True, "config": asdict(cfg), "cost_model": "approximate" if approximate else "exact",
        "stats": stats,
        "metrics": {
            "net_profit": float(eq - cfg.capital), "final_equity": float(eq),
            "return_pct": float((eq / cfg.capital - 1) * 100),
            "profit_factor": float(sum(wins) / -sum(losses)) if losses else None,
            "max_drawdown_pct": max_dd, "sharpe_per_trade_ann": sharpe, "sortino_per_trade_ann": sortino,
            "win_rate": len(wins) / len(pnl_list) * 100 if pnl_list else None,
            "avg_r_multiple": float(np.mean(rs)) if rs else None,
            "execution_accuracy": stats["executed"] / eligible if eligible else None,
            "copy_delay_ms": cfg.delay_ms,
            "avg_slippage_r": stats["slippage_cost"] / max(1, stats["executed"]) / max(1e-9, cfg.capital * 0.005),
            "max_drawdown_lockout": locked,
        },
        "stability": walk_forward(rs) if len(rs) >= 50 else {"error": "need >= 50 executed trades"},
        "monte_carlo": monte_carlo([x * lim.risk_per_trade_pct for x in rs], n_sims=3000, block=3) if len(rs) >= 10
        else {"error": "need >= 10 executed trades"},
        "equity_curve": curve_out[:: max(1, len(curve_out) // 500)],
    }
