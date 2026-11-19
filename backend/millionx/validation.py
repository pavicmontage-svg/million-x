"""Strategy validation evidence, live eligibility and degradation detection.

A bot can become LIVE_ELIGIBLE only when ALL of these exist:
  * a walk-forward job (out-of-sample + Monte Carlo) whose activation verdict
    was BACKTEST_PASSED, applied by the owner, not older than 90 days;
  * real paper/demo trades (not shadow, not backtest) by this bot meeting the
    paper criteria (count, profit factor, drawdown).
Nothing else (prompts, executives, settings) can set the status directly.
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional

import numpy as np

from .backtest import metrics
from .models import BotStatus, Trade

VALIDATION_MAX_AGE_MS = 90 * 86_400_000


def _trades(store, bot_id: str, modes: tuple[str, ...], virtual: Optional[bool], limit: int = 5000) -> list[Trade]:
    q = f"SELECT * FROM trades WHERE bot_id=? AND mode IN ({','.join('?' * len(modes))})"
    args: list[Any] = [bot_id, *modes]
    if virtual is not None:
        q += " AND virtual=?"
        args.append(int(virtual))
    q += " ORDER BY closed_at DESC LIMIT ?"
    args.append(limit)
    rows = store.q(q, args)
    return [Trade(**{k: r[k] for k in Trade.__dataclass_fields__}) for r in reversed(rows)]


def eligibility(store, settings, bot_id: str, now_ms: Optional[int] = None) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    c = settings.activation
    v = store.kv_get(f"validation:{bot_id}") or {}
    paper = _trades(store, bot_id, ("paper", "demo"), virtual=False)
    pm = metrics.compute(paper, 100.0)
    checks = {
        "walk_forward_passed": (v.get("status") in ("BACKTEST_PASSED", "LIVE_ELIGIBLE"),
                                f"validation record: {v.get('status', 'none')} (job {v.get('job', '-')})"),
        "validation_fresh": (bool(v) and now_ms - int(v.get("ts", 0)) <= VALIDATION_MAX_AGE_MS,
                             "walk-forward evidence not older than 90 days"),
        "paper_trades": (pm["trades"] >= c.min_paper_trades, f"{pm['trades']} >= {c.min_paper_trades}"),
        "paper_profit_factor": ((pm["profit_factor"] or 0) >= c.min_paper_pf,
                                f"{pm['profit_factor']} >= {c.min_paper_pf}"),
        "paper_drawdown": (pm["max_drawdown"] <= c.max_drawdown, f"{pm['max_drawdown']:.3f} <= {c.max_drawdown}"),
    }
    ok = all(x[0] for x in checks.values())
    return {"bot_id": bot_id, "eligible": ok, "paper_metrics": pm,
            "checks": {k: {"pass": bool(x[0]), "detail": x[1]} for k, x in checks.items()}}


def enforce_live_statuses(engine) -> list[str]:
    """Demote any LIVE_ELIGIBLE bot whose evidence no longer holds."""
    demoted = []
    for b, st in list(engine.sup.status.items()):
        if st == BotStatus.LIVE_ELIGIBLE and not eligibility(engine.store, engine.s, b)["eligible"]:
            engine.sup.set_status(b, BotStatus.PAPER, "live eligibility evidence missing or stale")
            engine.save_bot_state(b)
            demoted.append(b)
    if demoted:
        engine.store.audit("quant_research", "live_eligibility_revoked", {"bots": demoted})
    return demoted


def degradation(store, bot_id: str, mode: str, window: int = 30) -> dict[str, Any]:
    """Compare recent results with the validated out-of-sample expectancy.
    Degraded = recent mean R below validated mean R by more than 2 standard errors."""
    v = store.kv_get(f"validation:{bot_id}") or {}
    recent = _trades(store, bot_id, (mode,), virtual=None, limit=window)
    if len(recent) < window:
        return {"bot_id": bot_id, "degraded": False, "reason": f"only {len(recent)} recent trades"}
    r = np.array([t.r_multiple for t in recent])
    se = r.std(ddof=1) / math.sqrt(len(r)) if len(r) > 1 else 0.0
    ref = v.get("oos_expectancy_r")
    if ref is None:
        degraded = bool(r.mean() + 2 * se < 0)
        basis = "no validation record: recent mean R significantly below zero"
    else:
        degraded = bool(r.mean() + 2 * se < float(ref))
        basis = f"validated OOS mean R {float(ref):.3f}"
    return {"bot_id": bot_id, "degraded": degraded, "recent_mean_r": float(r.mean()), "se": float(se),
            "basis": basis, "n": len(r)}


def regime_performance(store, bot_id: str, mode: str) -> list[dict[str, Any]]:
    rows = store.q("SELECT regime, virtual, COUNT(*) n, AVG(r_multiple) exp_r, "
                   "SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END)*1.0/COUNT(*) win_rate, "
                   "SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) gp, "
                   "-SUM(CASE WHEN net_pnl<=0 THEN net_pnl ELSE 0 END) gl "
                   "FROM trades WHERE bot_id=? AND mode=? GROUP BY regime, virtual ORDER BY regime", (bot_id, mode))
    for r in rows:
        r["profit_factor"] = (r["gp"] / r["gl"]) if r["gl"] else None
        r["book"] = "shadow" if r.pop("virtual") else "orders"
    return rows


def return_correlation(store, mode: str, bot_ids: list[str], days: int = 30) -> dict[str, dict[str, Optional[float]]]:
    """Correlation of daily R sums (shadow books) between bots."""
    since = int(time.time() * 1000) - days * 86_400_000
    rows = store.q("SELECT bot_id, closed_at/86400000 d, SUM(r_multiple) r FROM trades WHERE virtual=1 AND mode=? "
                   "AND closed_at>? GROUP BY bot_id, d", (mode, since))
    daysset = sorted({r["d"] for r in rows})
    idx = {d: i for i, d in enumerate(daysset)}
    series = {b: np.zeros(len(daysset)) for b in bot_ids}
    for r in rows:
        if r["bot_id"] in series:
            series[r["bot_id"]][idx[r["d"]]] = r["r"]
    out: dict[str, dict[str, Optional[float]]] = {}
    for a in bot_ids:
        out[a] = {}
        for b in bot_ids:
            x, y = series[a], series[b]
            if len(daysset) < 5 or x.std() == 0 or y.std() == 0:
                out[a][b] = None
            else:
                out[a][b] = float(np.corrcoef(x, y)[0, 1])
    return out
