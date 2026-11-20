"""Research 2.0 results inside the app: import, summary for the Android views, parameter history.

Research runs on CI (real data, hours of compute). Its aggregated output is imported here
either from the snapshot packaged with the backend (`millionx/data/research2_summary.json`,
committed after a CI run) or through POST /api/research2/import. Importing only records
verdicts; it never activates a strategy (activation still needs paper evidence and the owner).
"""
from __future__ import annotations

import json
import os
from typing import Any

from .models import now_ms

SNAPSHOT = os.path.join(os.path.dirname(__file__), "data", "research2_summary.json")
KEEP = ("bot", "timeframe", "configs_tried", "oos", "max_drawdown", "positive_windows", "long_short", "by_regime",
        "by_symbol", "deflated_sharpe", "pbo", "cross_asset", "cost_scenarios", "most_chosen_config",
        "parameter_stability", "checks", "verdict")


def strategy_catalog() -> list[dict[str, Any]]:
    from .strategies import BASE_CLASSES, RESEARCH_CLASSES
    from .strategies.adaptive import AdaptiveAIScalper
    out = []
    for c in list(BASE_CLASSES) + [AdaptiveAIScalper]:
        out.append({"id": c.bot_id, "name": c.name, "version": "1.9.0", "research_only": False})
    for c in RESEARCH_CLASSES:
        out.append({"id": c.bot_id, "name": c.name, "version": getattr(c, "version", "2.0.0-proto"),
                    "research_only": True, "kind": getattr(c, "kind", "prototype")})
    return out


def summarise(agg: dict[str, Any]) -> dict[str, Any]:
    reps = agg.get("reports")
    if not isinstance(reps, list):
        raise ValueError("expected the aggregated research2.json (key 'reports')")
    slim = [{k: r.get(k) for k in KEEP} for r in reps]
    sel = agg.get("selection") or {}
    cells = []
    for c in (sel.get("cells") or {}).values():
        top = (c.get("candidates") or [{}])[0]
        cells.append({"symbol": c.get("symbol"), "regime": c.get("regime"), "selected": c.get("selected"),
                      "why": c.get("why"), "best_candidate": top.get("strategy"), "best_score": top.get("final_score"),
                      "best_ci_low": top.get("ci95_low"), "best_n": top.get("n")})
    return {"reports": slim, "cells": sorted(cells, key=lambda x: (x["symbol"] or "", x["regime"] or "")),
            "chosen": sel.get("chosen_strategies", []), "no_trade_cells": sel.get("no_trade_cells"),
            "run": agg.get("run", ""), "imported_at": now_ms()}


def import_results(store, agg: dict[str, Any], source: str) -> dict[str, Any]:
    s = summarise(agg)
    s["source"] = source
    store.kv_set("research2:report", s)
    best: dict[str, dict[str, Any]] = {}
    for r in s["reports"]:
        b = r["bot"]
        if b not in best or (r["verdict"] == "CANDIDATE_FOR_PAPER" and best[b]["verdict"] != "CANDIDATE_FOR_PAPER"):
            best[b] = r
    for b, r in best.items():
        store.kv_set(f"research2:{b}", {"verdict": r["verdict"], "timeframe": r.get("timeframe"),
                                        "run": s["run"] or source, "ts": now_ms()})
    store.audit("quant_research", "research2_imported",
                {"source": source, "reports": len(s["reports"]), "candidates":
                 sorted(b for b, r in best.items() if r["verdict"] == "CANDIDATE_FOR_PAPER")})
    return {"ok": True, "reports": len(s["reports"]), "cells": len(s["cells"])}


def load_snapshot(store) -> bool:
    """Import the packaged snapshot once (or when a newer one ships with an update)."""
    if not os.path.exists(SNAPSHOT):
        return False
    try:
        agg = json.load(open(SNAPSHOT))
    except (OSError, ValueError):
        return False
    cur = store.kv_get("research2:report") or {}
    if cur.get("run") and cur.get("run") == agg.get("run"):
        return False
    import_results(store, agg, "packaged snapshot")
    return True


def param_history(store, limit: int = 200) -> list[dict[str, Any]]:
    rows = store.q("SELECT ts, actor, action, evidence FROM audit WHERE action IN "
                   "('bot_params_changed','validation_applied','research2_imported') ORDER BY seq DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        try:
            ev = json.loads(r["evidence"] or "{}")
        except ValueError:
            ev = {}
        out.append({"ts": r["ts"], "actor": r["actor"], "action": r["action"], "detail": ev})
    return out


def overview(store, engine, settings) -> dict[str, Any]:
    from .validation import degradation
    rep = store.kv_get("research2:report") or {}
    decay = []
    if engine is not None:
        for bid in list(engine.sup.status):
            d = degradation(store, bid, settings.mode)
            if d.get("degraded"):
                decay.append({"bot": bid, "basis": d.get("basis", ""), "recent_mean_r": d.get("recent_mean_r"), "n": d.get("n")})
    statuses = {b: s.value for b, s in engine.sup.status.items()} if engine is not None else {}
    return {"available": bool(rep), "report": rep, "catalog": strategy_catalog(), "statuses": statuses,
            "decay_warnings": decay, "param_history": param_history(store, 50),
            "note": "Research results come from CI on real Binance data; the final holdout is not used. "
                    "A CANDIDATE_FOR_PAPER verdict is not activation: paper trading >= 30 days and the owner decide."}
