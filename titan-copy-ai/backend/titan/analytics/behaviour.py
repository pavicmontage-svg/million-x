"""Detectors for dangerous trading behaviour in a trader's published history.

Each detector returns a Finding with evidence. Severity "reject" triggers automatic rejection
in the ranking engine; "warn" lowers transparency and is shown to the user.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone

import numpy as np

from .metrics import TradeRow


@dataclass
class Finding:
    code: str
    severity: str     # reject | warn
    message: str
    evidence: dict = field(default_factory=dict)


def _aw(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def detect_martingale(trades: list[TradeRow], min_cases: int = 8) -> Finding | None:
    """Volume escalation after losses (classic martingale / anti-recovery sizing)."""
    seq = sorted([t for t in trades if t.volume and t.profit is not None and t.close_time],
                 key=lambda t: _aw(t.open_time))
    after_loss, escalations, ratios = 0, 0, []
    for prev, nxt in zip(seq, seq[1:]):
        if prev.profit < 0 and _aw(nxt.open_time) >= _aw(prev.close_time):
            after_loss += 1
            r = nxt.volume / prev.volume
            ratios.append(r)
            if r >= 1.5:
                escalations += 1
    if after_loss < min_cases:
        return None
    share = escalations / after_loss
    ev = {"loss_followups": after_loss, "escalations": escalations, "share": round(share, 3),
          "median_ratio": round(float(np.median(ratios)), 3)}
    if share >= 0.4 and np.median(ratios) >= 1.4:
        return Finding("martingale", "reject", "position size is systematically increased after losses", ev)
    if share >= 0.25:
        return Finding("martingale_tendency", "warn", "frequent volume increases after losing trades", ev)
    return None


def _open_overlaps(trades: list[TradeRow]):
    """Yield (trade, list of same-symbol same-side trades still open when it was opened)."""
    seq = sorted([t for t in trades if t.close_time], key=lambda t: _aw(t.open_time))
    for i, t in enumerate(seq):
        concurrent = [o for o in seq[:i] if o.symbol == t.symbol and o.side == t.side
                      and _aw(o.close_time) > _aw(t.open_time)]
        yield t, concurrent


def detect_grid_and_averaging(trades: list[TradeRow]) -> list[Finding]:
    out = []
    adds_against, adds_total, max_stack, no_sl_stacks = 0, 0, 0, 0
    for t, conc in _open_overlaps(trades):
        if not conc:
            continue
        adds_total += 1
        max_stack = max(max_stack, len(conc) + 1)
        # "against": new entry at a worse price than the previous entry (adding to a loser)
        last = conc[-1]
        if t.open_price and last.open_price:
            worse = t.open_price < last.open_price if t.side == "buy" else t.open_price > last.open_price
            if worse:
                adds_against += 1
                if not t.sl and not last.sl:
                    no_sl_stacks += 1
    n = max(1, len([t for t in trades if t.close_time]))
    ev = {"adds": adds_total, "adds_against_position": adds_against, "max_stack": max_stack,
          "unprotected_adds": no_sl_stacks, "share_of_trades": round(adds_against / n, 3)}
    if max_stack >= 5 and adds_against / n >= 0.15:
        out.append(Finding("grid", "reject", f"grid-style stacking of up to {max_stack} same-direction positions "
                                             "added against the market", ev))
    elif adds_against / n >= 0.1 and no_sl_stacks >= 3:
        out.append(Finding("averaging_down", "reject", "repeatedly averages down without a defined stop-loss", ev))
    elif adds_against >= 3:
        out.append(Finding("averaging_tendency", "warn", "sometimes adds to losing positions", ev))
    return out


def detect_accumulation(trades: list[TradeRow], max_concurrent_ok: int = 12) -> Finding | None:
    events = []
    for t in trades:
        if t.close_time:
            events += [(_aw(t.open_time), 1), (_aw(t.close_time), -1)]
    cur = peak = 0
    for _, d in sorted(events, key=lambda e: (e[0], e[1])):
        cur += d
        peak = max(peak, cur)
    if peak > max_concurrent_ok:
        return Finding("position_accumulation", "reject",
                       f"up to {peak} positions open simultaneously (unexplained accumulation)", {"peak": peak})
    return None


def detect_hidden_drawdown(metrics: dict) -> Finding | None:
    eq, bal = metrics.get("max_equity_drawdown_pct"), metrics.get("max_balance_drawdown_pct")
    if eq is None and bal is None:
        return Finding("drawdown_unverifiable", "reject", "no drawdown data is published (hidden/unverifiable)", {})
    if eq is None:
        return Finding("equity_drawdown_unknown", "warn",
                       "only balance drawdown is available; floating (equity) drawdown cannot be verified",
                       {"balance_dd": bal})
    if bal is not None and bal > 0 and eq > 2.5 * bal and eq - bal > 5:
        return Finding("hidden_floating_drawdown", "reject",
                       f"equity drawdown {eq:.1f}% is far above balance drawdown {bal:.1f}% "
                       "(losses are held open)", {"equity_dd": eq, "balance_dd": bal})
    return None


def detect_suspicious_curve(metrics: dict, trades: list[TradeRow]) -> Finding | None:
    wr, aw, al = metrics.get("win_rate"), metrics.get("avg_win"), metrics.get("avg_loss")
    if wr is not None and aw and al and wr >= 85 and -al / aw >= 4:
        return Finding("suspicious_equity_curve", "reject",
                       f"{wr:.0f}% win rate with average loss {-al / aw:.1f}x the average win "
                       "(smooth curve hiding tail risk)", {"win_rate": wr, "loss_to_win": -al / aw})
    p = [t.profit for t in trades if t.profit is not None]
    if len(p) >= 20:
        arr = np.asarray(p, dtype=float)
        top = np.sort(np.abs(arr))[::-1]
        if arr.sum() > 0 and top[0] / max(abs(arr.sum()), 1e-9) > 0.6:
            return Finding("single_trade_dependency", "warn",
                           "more than 60% of net profit comes from a single trade", {"share": float(top[0] / arr.sum())})
    return None


def detect_excessive_leverage(metrics: dict, cap: float = 30.0) -> Finding | None:
    lev = metrics.get("max_leverage")
    if lev is not None and lev > cap:
        return Finding("excessive_leverage", "reject", f"maximum leverage {lev:.1f}x exceeds {cap}x", {"lev": lev})
    return None


def analyse(trades: list[TradeRow], metrics: dict) -> list[Finding]:
    out: list[Finding] = []
    for f in (detect_martingale(trades), detect_accumulation(trades), detect_hidden_drawdown(metrics),
              detect_suspicious_curve(metrics, trades), detect_excessive_leverage(metrics)):
        if f:
            out.append(f)
    out += detect_grid_and_averaging(trades)
    if not trades and metrics.get("roi_30d") is None and metrics.get("months_of_history") is None:
        out.append(Finding("no_verified_history", "reject", "no verified trading history is available", {}))
    return out
