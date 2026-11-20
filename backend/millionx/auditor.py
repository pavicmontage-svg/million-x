"""Performance Auditor: continuous, read-only analysis of closed trades.

Real results (virtual=0: demo/live fills on the exchange) and simulated results
(virtual=1: shadow books on real prices) are always reported separately; simulated
profit is never presented as realised profit. Groups: strategy/agent, instrument,
market regime. The AI Independent Trader (bot_id 'ai_trader') is compared with the
bots on the SAME time window and the same instruments, using the same shadow sizing.
"""
from __future__ import annotations

import json
from typing import Any

from .backtest import metrics
from .models import Trade, now_ms

FIELDS = list(Trade.__dataclass_fields__)


def _trades(store, mode: str, virtual: int, since: int) -> list[Trade]:
    rows = store.q("SELECT * FROM trades WHERE mode=? AND virtual=? AND closed_at>=? ORDER BY closed_at",
                   (mode, virtual, since))
    return [Trade(**{k: r[k] for k in FIELDS}) for r in rows]


def _group(trades: list[Trade], key, equity: float) -> dict[str, Any]:
    g: dict[str, list[Trade]] = {}
    for t in trades:
        g.setdefault(str(key(t) or "?"), []).append(t)
    out = {}
    for k, ts in g.items():
        m = metrics.compute(ts, equity)
        out[k] = {x: m.get(x) for x in ("trades", "win_rate", "profit_factor", "expectancy_r", "avg_win", "avg_loss",
                                        "max_drawdown", "sharpe", "sortino", "net_profit")}
    return out


def report(store, mode: str, start_equity: float, days: float = 30.0) -> dict[str, Any]:
    since = now_ms() - int(days * 86_400_000)
    out: dict[str, Any] = {"mode": mode, "days": days, "generated_at": now_ms()}
    for label, virtual, eq in (("real", 0, start_equity), ("simulated", 1, 100.0)):
        ts = _trades(store, mode, virtual, since)
        out[label] = {"note": ("exchange fills (demo = virtual funds on Binance, live = real money)" if virtual == 0
                               else "shadow paper trades at a fixed 0.5 R stake — NOT realised profit"),
                      "overall": metrics.compute(ts, eq) if ts else {"trades": 0},
                      "by_strategy": _group(ts, lambda t: t.bot_id, eq),
                      "by_instrument": _group(ts, lambda t: t.symbol, eq),
                      "by_regime": _group(ts, lambda t: t.regime, eq)}
    # AI trader vs bots on identical windows/instruments (simulated books, same sizing)
    sim = _trades(store, mode, 1, since)
    ai = [t for t in sim if t.bot_id == "ai_trader"]
    if ai:
        t0 = min(t.opened_at for t in ai)
        syms = {t.symbol for t in ai}
        bots = [t for t in sim if t.bot_id != "ai_trader" and t.opened_at >= t0 and t.symbol in syms]
        out["ai_vs_bots"] = {"window_start": t0, "instruments": sorted(syms),
                             "ai_trader": metrics.compute(ai, 100.0),
                             "bots_same_window": _group(bots, lambda t: t.bot_id, 100.0)}
    else:
        out["ai_vs_bots"] = {"note": "no closed AI trader trades yet"}
    return out


def snapshot(store, mode: str, start_equity: float) -> None:
    rep = report(store, mode, start_equity, days=30)
    store.x("INSERT OR REPLACE INTO perf_snapshots(ts,data) VALUES(?,?)",
            (now_ms() // 60_000 * 60_000, json.dumps(metrics.json_safe(rep), default=str)))
    store.x("DELETE FROM perf_snapshots WHERE ts < ?", (now_ms() - 30 * 86_400_000,))
