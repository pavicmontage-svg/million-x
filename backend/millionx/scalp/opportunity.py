"""Opportunity Ranking Engine (2.2, §4).

Every candidate gets: expected price move, expected holding time, expected gross profit, entry fee, exit fee,
spread cost, slippage, funding, expected net profit, risk/reward, calibrated probability and data quality.
Probability and expected move come ONLY from a calibration table of historical outcomes of similar setups
(engine x score quintile x regime, discovery period). An AI confidence number is never used.

Ranking key (owner decision): expected NET money per trade = expected net R x risk per trade (EUR).
Win rate is shown but is never a criterion; a low-win-rate setup with large winners ranks above a
high-win-rate setup with small winners whenever its expected net R is higher.

A candidate is ACCEPTED only if: data quality is OK, the calibration cell has >= MIN_N past cases, the
expected NET profit after all costs exceeds the reserve, and the strategy is validated for execution.
Everything else is REJECTED with explicit reasons (shown on the Android dashboard).
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .exits import Costs

MIN_N = 50
RESERVE_BPS = 2.0     # expected net must exceed costs by this margin
QUANTS = 5


def score_bucket(score: float, edges: list[float]) -> int:
    return int(np.searchsorted(edges, score, side="right"))


def build_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: discovery trades with engine, cfg, score, regime, gross_bps, hold_s, mfe_bps, stop_bps.
    -> {key: {...}} with key 'engine|cfg|bucket|regime' and an 'ALL' regime fallback."""
    out: dict[str, Any] = {"edges": {}, "cells": {}}
    by_ec: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_ec.setdefault(f"{r['engine']}|{r['cfg']}", []).append(r)
    for ec, rs in by_ec.items():
        sc = np.array([r["score"] for r in rs])
        edges = list(np.quantile(sc, np.linspace(0, 1, QUANTS + 1)[1:-1])) if len(sc) >= QUANTS * 2 else []
        out["edges"][ec] = edges
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rs:
            b = score_bucket(r["score"], edges)
            groups.setdefault(f"{ec}|{b}|{r['regime']}", []).append(r)
            groups.setdefault(f"{ec}|{b}|ALL", []).append(r)
        for k, g in groups.items():
            gross = np.array([x["gross_bps"] for x in g])
            rr = np.array([x["gross_bps"] / x["stop_bps"] for x in g if x["stop_bps"] > 0])
            out["cells"][k] = {"n": len(g), "p_win": float((gross > 0).mean()), "exp_gross_bps": float(gross.mean()),
                               "exp_gross_r": float(rr.mean()) if len(rr) else 0.0,
                               "avg_win_r": float(rr[rr > 0].mean()) if (rr > 0).any() else 0.0,
                               "avg_loss_r": float(rr[rr <= 0].mean()) if (rr <= 0).any() else 0.0,
                               "exp_move_bps": float(np.mean([x["mfe_bps"] for x in g])),
                               "exp_hold_s": float(np.mean([x["hold_s"] for x in g])),
                               "avg_stop_bps": float(np.mean([x["stop_bps"] for x in g]))}
    return out


def rank(c: dict[str, Any], calib: dict[str, Any], cost: Costs, validated: bool, quality_ok: bool = True,
         quality_note: str = "", maker_confirmed: bool = False, risk_eur: float = 2.5) -> dict[str, Any]:
    """c: engine, cfg, side, score, regime, stop_bps, rr. Returns the full evaluation and ACCEPT/REJECT."""
    ec = f"{c['engine']}|{c['cfg']}"
    b = score_bucket(c["score"], calib["edges"].get(ec, []))
    cell = calib["cells"].get(f"{ec}|{b}|{c['regime']}")
    src = "engine x score x regime"
    if not cell or cell["n"] < MIN_N:
        cell, src = calib["cells"].get(f"{ec}|{b}|ALL"), "engine x score (all regimes)"
    reasons: list[str] = []
    hold = cell["exp_hold_s"] if cell else 300.0
    entry_fee = cost.maker_bps if maker_confirmed else cost.taker_bps
    exit_fee = cost.taker_bps
    spread = (0 if maker_confirmed else cost.spread_bps / 2) + cost.spread_bps / 2
    slip = (0 if maker_confirmed else cost.slippage_bps) + cost.slippage_bps
    funding = cost.funding_bps_per_8h * hold / 28_800
    total = entry_fee + exit_fee + spread + slip + funding
    gross = cell["exp_gross_bps"] if cell else None
    net = gross - total if gross is not None else None
    ev = {"engine": c["engine"], "cfg": c["cfg"], "side": c["side"], "regime": c["regime"], "score": c["score"],
          "calibration": src, "calibration_n": cell["n"] if cell else 0,
          "p_win": cell["p_win"] if cell else None, "expected_move_bps": cell["exp_move_bps"] if cell else None,
          "expected_hold_s": hold, "expected_gross_bps": gross, "entry_fee_bps": entry_fee, "exit_fee_bps": exit_fee,
          "spread_bps": spread, "slippage_bps": slip, "funding_bps": funding, "total_cost_bps": total,
          "expected_net_bps": net, "stop_bps": c["stop_bps"], "rr": c.get("rr"),
          "fee_to_risk": total / c["stop_bps"] if c["stop_bps"] > 0 else None, "data_quality": "OK" if quality_ok else "INCOMPLETE"}
    # money view: R multiples and EUR for the fixed risk per trade
    ev["expected_net_r"] = (net / c["stop_bps"]) if net is not None and c["stop_bps"] > 0 else None
    ev["expected_net_eur"] = ev["expected_net_r"] * risk_eur if ev["expected_net_r"] is not None else None
    ev["avg_win_r"] = cell.get("avg_win_r") if cell else None
    ev["avg_loss_r"] = cell.get("avg_loss_r") if cell else None
    if not quality_ok:
        reasons.append(f"data incomplete: {quality_note}")
    if not cell or cell["n"] < MIN_N:
        reasons.append(f"not calibrated (n={cell['n'] if cell else 0} < {MIN_N} similar past setups)")
    elif net is None or net <= RESERVE_BPS or (ev["expected_net_r"] or 0) <= 0:
        reasons.append(f"expected net {net:+.1f} bps <= reserve {RESERVE_BPS:.1f} (gross {gross:+.1f}, costs {total:.1f})")
    if not validated:
        reasons.append("strategy not validated for execution (research/paper evidence missing)")
    ev["decision"] = "ACCEPT" if not reasons else "REJECT"
    ev["reasons"] = reasons or [f"expected net {ev['expected_net_r']:+.2f} R = {ev['expected_net_eur']:+.2f} EUR per trade "
                                f"(avg win {ev['avg_win_r']:+.2f} R, avg loss {ev['avg_loss_r']:+.2f} R, win rate {ev['p_win']:.0%})"]
    return ev
