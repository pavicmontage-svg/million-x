"""Quantitative Research Engine 2.0 (MillionX 2.0, phases 6-9, 11).

Efficient and leak-free design: a strategy's shadow trades are path-dependent only on
its own signals and causal indicators, so each parameter configuration is simulated
ONCE over the selection period; walk-forward windows and CV folds then select trades
by time. Parameter choice in a window uses only trades that were opened AND closed
inside its training ranges (purge + embargo around the test fold).

Pipeline per (strategy, timeframe):
  1. Grid of <= 12 configurations (stable ranges, not one magic point).
  2. Nested walk-forward: outer train 90 d / test 30 d; inner purged 3-fold CV chooses
     the configuration (plateau-smoothed). Only outer-test trades are OOS.
  3. OOS metrics: overall, long/short, regime (Regime 2.0 label at entry), instrument,
     positive windows, drawdown, Monte Carlo DD p95, block-bootstrap CI.
  4. Multiple testing: Deflated Sharpe with the number of configurations tried in the
     whole run; PBO (CSCV) on the daily-R matrix of all configurations.
  5. Cross-asset: choose on majors, evaluate on the other instruments.
  6. Costs: the chosen configurations re-run under STRESSED and EXTREME costs.
  7. Parameter sensitivity: share of neighbours of the most-chosen configuration that
     are also positive.
  8. Activation 2.0 verdict (existing thresholds as a MINIMUM + the new requirements).
The final holdout is never read here.
"""
from __future__ import annotations

import copy
import itertools
import math
from collections import Counter, defaultdict
from typing import Any, Callable, Optional

import numpy as np

from .backtest import metrics, montecarlo, stats2
from .backtest.engine import BacktestConfig, run
from .config import Settings
from .models import Bars, Trade

DAY = 86_400_000
MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")

COSTS = {  # round-trip extras vs BASE in bps are used for the sensitivity slope
    "BASE": {"taker": 0.0005, "slippage_bps": 2.0, "spread_bps": 1.0, "funding_mult": 1.0, "delay": 0, "reject": 0.0},
    "STRESSED": {"taker": 0.0005, "slippage_bps": 5.0, "spread_bps": 3.0, "funding_mult": 2.0, "delay": 1,
                 "reject": 0.02},
    "EXTREME": {"taker": 0.0006, "slippage_bps": 10.0, "spread_bps": 6.0, "funding_mult": 3.0, "delay": 2,
                "reject": 0.05},
}


def _extra_bps(name: str) -> float:
    b, c = COSTS["BASE"], COSTS[name]
    per_side = (c["taker"] - b["taker"]) * 1e4 + (c["slippage_bps"] - b["slippage_bps"]) + \
        (c["spread_bps"] - b["spread_bps"]) / 2
    return 2 * per_side


def simulate(data: dict[str, Bars], bot_id: str, params: dict[str, Any], scenario: str = "BASE",
             strategy_factory: Optional[Callable] = None) -> list[Trade]:
    c = COSTS[scenario]
    s = Settings()
    s.fees = copy.deepcopy(s.fees)
    s.fees.taker = c["taker"]
    s.fees.slippage_bps = c["slippage_bps"]
    s.fees.funding_rate_8h *= c["funding_mult"]
    cfg = BacktestConfig(bots=[bot_id], params={bot_id: params}, virtual_only=True, daily_competition=False,
                         spread_bps=c["spread_bps"], entry_delay_bars=c["delay"], reject_prob=c["reject"])
    res = run(data, s, cfg)
    return [t for t in res.virtual_trades if t.bot_id == bot_id]


def objective(ts: list[Trade], min_trades: int = 20) -> float:
    if len(ts) < min_trades:
        return -1e9
    r = np.array([t.r_multiple for t in ts])
    eq = np.cumprod(1 + 0.005 * r)
    dd = metrics.max_drawdown(np.concatenate([[1.0], eq]))
    return float(r.mean() * math.sqrt(len(r)) - 3.0 * dd)


def _within(ts: list[Trade], ranges: list[tuple[int, int]]) -> list[Trade]:
    return [t for t in ts if any(a <= t.opened_at and t.closed_at <= b for a, b in ranges)]


def _neighbours(grid: list[dict[str, Any]], i: int) -> list[int]:
    p = grid[i]
    return [j for j, q in enumerate(grid) if j != i and sum(p.get(x) != q.get(x) for x in p) == 1]


def nested_wf(trades_by_cfg: list[list[Trade]], grid: list[dict[str, Any]], t0: int, t1: int,
              train_days: int = 90, test_days: int = 30, folds: int = 3, purge_ms: int = DAY,
              embargo_ms: int = DAY, symbols: Optional[set[str]] = None) -> dict[str, Any]:
    windows, oos, chosen = [], [], []
    start = t0
    while start + (train_days + test_days) * DAY <= t1:
        tr_a, tr_b = start, start + train_days * DAY
        te_b = tr_b + test_days * DAY
        cv = stats2.purged_kfold_ranges(tr_a, tr_b - purge_ms, folds, purge_ms, embargo_ms)
        scores = []
        for ts in trades_by_cfg:
            sel = [t for t in ts if symbols is None or t.symbol in symbols]
            fs = [objective(_within(sel, [f["test"]]), min_trades=8) for f in cv]
            scores.append(float(np.mean(fs)) if all(x > -1e8 for x in fs) else -1e9)
        smooth = []
        for i in range(len(grid)):
            nb = [scores[j] for j in _neighbours(grid, i) if scores[j] > -1e8]
            smooth.append((scores[i] + sum(nb)) / (1 + len(nb)) if scores[i] > -1e8 else -1e9)
        best = int(np.argmax(smooth))
        test = [t for t in trades_by_cfg[best] if tr_b <= t.opened_at < te_b]
        oos.extend(test)
        chosen.append(best)
        r = [t.r_multiple for t in test]
        windows.append({"train": [tr_a, tr_b], "test": [tr_b, te_b], "config": best,
                        "oos_n": len(test), "oos_exp_r": float(np.mean(r)) if r else None,
                        "selectable": smooth[best] > -1e8})
        start += test_days * DAY
    return {"windows": windows, "oos": oos, "chosen": chosen}


def regime_labels(data: dict[str, Bars]) -> dict[str, Any]:
    from .regime2 import classify
    btc = data.get("BTCUSDT")
    return {s: classify(b, btc if s != "BTCUSDT" else None) for s, b in data.items()}


def _breakdown(ts: list[Trade], key) -> dict[str, Any]:
    g: dict[str, list[Trade]] = defaultdict(list)
    for t in ts:
        g[str(key(t))].append(t)
    return {k: {"n": len(v), "exp_r": round(float(np.mean([t.r_multiple for t in v])), 4),
                "pf": metrics.compute(v, 100.0).get("profit_factor")} for k, v in sorted(g.items())}


def evaluate(data: dict[str, Bars], bot_id: str, grid: list[dict[str, Any]], period: tuple[int, int],
             n_trials_total: int, regimes: Optional[dict[str, Any]] = None, crit=None,
             strategy_factory: Optional[Callable] = None, log=print) -> dict[str, Any]:
    from .config import ActivationCriteria
    crit = crit or ActivationCriteria()
    t0, t1 = period
    by_cfg = []
    for i, p in enumerate(grid):
        by_cfg.append(simulate(data, bot_id, p, "BASE", strategy_factory))
        log(f"  {bot_id} cfg {i + 1}/{len(grid)} {p}: {len(by_cfg[-1])} trades")
    wf = nested_wf(by_cfg, grid, t0, t1)
    oos = sorted(wf["oos"], key=lambda t: t.closed_at)
    m = metrics.compute(oos, 100.0)
    r = [t.r_multiple for t in oos]
    eq = np.cumprod(1 + 0.005 * np.array(r)) if r else np.array([1.0])
    dd = metrics.max_drawdown(np.concatenate([[1.0], eq]))
    pos = [w for w in wf["windows"] if w["oos_n"]]
    pos_share = float(np.mean([(w["oos_exp_r"] or 0) > 0 for w in pos])) if pos else 0.0
    mc = montecarlo.simulate(r, 0.005) if len(r) >= 20 else {"available": False}
    boot = stats2.block_bootstrap(r) if len(r) >= 20 else {}
    dsr = stats2.deflated_sharpe(r, n_trials_total)
    days = np.arange(t0 // DAY, t1 // DAY + 1)
    mat = np.zeros((len(days), len(grid)))
    for j, ts in enumerate(by_cfg):
        for t in ts:
            k = t.closed_at // DAY - days[0]
            if 0 <= k < len(days):
                mat[k, j] += t.r_multiple
    pbo = stats2.pbo_cscv(mat) if len(grid) >= 2 else {"pbo": None, "reason": "single configuration"}
    # cross-asset: choose on majors, evaluate on the rest
    minors = {s for s in data if s not in MAJORS}
    xa = None
    if minors and any(s in data for s in MAJORS):
        wfm = nested_wf(by_cfg, grid, t0, t1, symbols=set(MAJORS))
        xoos = [t for w, c in zip(wfm["windows"], wfm["chosen"])
                for t in by_cfg[c] if t.symbol in minors and w["test"][0] <= t.opened_at < w["test"][1]]
        xa = {"chosen_on": list(MAJORS), "tested_on": sorted(minors), "n": len(xoos),
              "exp_r": round(float(np.mean([t.r_multiple for t in xoos])), 4) if xoos else None}
    # cost scenarios for the configurations actually chosen
    scen: dict[str, float] = {"BASE": float(np.mean(r)) if r else float("nan")}
    for sc in ("STRESSED", "EXTREME"):
        sims = {c: simulate(data, bot_id, grid[c], sc, strategy_factory) for c in set(wf["chosen"])}
        ts = [t for w, c in zip(wf["windows"], wf["chosen"]) for t in sims[c]
              if w["test"][0] <= t.opened_at < w["test"][1]]
        scen[sc] = float(np.mean([t.r_multiple for t in ts])) if ts else float("nan")
        log(f"  {bot_id} {sc}: n={len(ts)} expR={scen[sc]:.4f}")
    cs = stats2.cost_sensitivity(scen, {k: _extra_bps(k) for k in COSTS})
    # parameter sensitivity of the most chosen configuration
    top = Counter(wf["chosen"]).most_common(1)[0][0] if wf["chosen"] else 0
    sel_ranges = [(t0, t1)]
    nb = _neighbours(grid, top)
    nb_pos = [float(np.mean([t.r_multiple for t in _within(by_cfg[j], sel_ranges)] or [0])) > 0 for j in nb]
    stability = float(np.mean(nb_pos)) if nb_pos else None
    # regime / side / instrument breakdowns of the OOS trades
    if regimes is None:
        regime_of = lambda t: t.regime or "?"
    else:
        def regime_of(t):
            rg = regimes.get(t.symbol)
            if rg is None:
                return "?"
            b = data[t.symbol]
            i = int(np.searchsorted(b.open_time, t.opened_at)) - 1
            return rg.at_1m(max(0, i))[0]
    by_regime = _breakdown(oos, regime_of)
    good_regimes = [k for k, v in by_regime.items() if v["n"] >= 30 and v["exp_r"] > 0]
    checks = {
        "oos_trades": (m["trades"] >= crit.min_oos_trades, m["trades"]),
        "profit_factor": ((m.get("profit_factor") or 0) >= crit.min_profit_factor, m.get("profit_factor")),
        "expectancy_r": ((m.get("expectancy_r") or -9) >= crit.min_expectancy_r, m.get("expectancy_r")),
        "positive_windows": (pos_share >= crit.min_positive_wf_windows, round(pos_share, 3)),
        "max_drawdown": (dd <= crit.max_drawdown, round(dd, 4)),
        "mc_dd_p95": ((mc.get("max_dd_p95") if mc.get("available", True) else 9) <= crit.mc_dd_p95_max,
                      mc.get("max_dd_p95")),
        "net_ev_after_costs_ci": ((boot.get("exp_r_ci95") or [-1])[0] > 0, boot.get("exp_r_ci95")),
        "deflated_sharpe": ((dsr.get("dsr") or 0) >= 0.95, dsr.get("dsr")),
        "pbo": ((pbo.get("pbo") if pbo.get("pbo") is not None else 1) <= 0.5, pbo.get("pbo")),
        "parameter_stability": ((stability or 0) >= 0.5, stability),
        "cross_asset": ((xa or {}).get("exp_r") is not None and (xa or {}).get("exp_r", -1) > 0, (xa or {}).get("exp_r")),
        "multi_regime": (len(good_regimes) >= 2, good_regimes),
        "stressed_costs": (cs["survives_stressed"], scen.get("STRESSED")),
    }
    passed = all(v[0] for v in checks.values())
    return {"bot": bot_id, "grid": grid, "configs_tried": len(grid),
            "oos": {k: m.get(k) for k in ("trades", "win_rate", "profit_factor", "expectancy_r", "sharpe",
                                          "avg_win", "avg_loss")},
            "max_drawdown": dd, "positive_windows": pos_share, "windows": wf["windows"],
            "long_short": _breakdown(oos, lambda t: t.side), "by_regime": by_regime,
            "by_symbol": _breakdown(oos, lambda t: t.symbol), "monte_carlo": mc, "bootstrap": boot,
            "deflated_sharpe": dsr, "pbo": pbo, "cross_asset": xa, "cost_scenarios": scen,
            "cost_sensitivity": cs, "most_chosen_config": grid[top] if grid else {},
            "parameter_stability": stability,
            "oos_trades": [{"strategy": bot_id, "symbol": t.symbol, "side": t.side, "regime": regime_of(t),
                            "r": round(t.r_multiple, 4), "opened_at": t.opened_at, "closed_at": t.closed_at}
                           for t in oos],
            "checks": {k: {"pass": bool(v[0]), "value": v[1]} for k, v in checks.items()},
            "verdict": "CANDIDATE_FOR_PAPER" if passed else "RESEARCH"}


def grid_from(spec: dict[str, list[Any]], fixed: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    keys = list(spec)
    combos = [dict(zip(keys, v)) for v in itertools.product(*(spec[k] for k in keys))]
    if len(combos) > limit:  # evenly thin the grid; keep it bounded (multiple-testing control)
        idx = np.linspace(0, len(combos) - 1, limit).round().astype(int)
        combos = [combos[i] for i in sorted(set(idx))]
    return [{**c, **fixed} for c in combos]


# ---------------------------------------------------------------- CLI (run on CI, real data)
def run_plan() -> list[tuple[str, int, list[dict[str, Any]]]]:
    """Every (strategy, timeframe, grid) of the 2.0 run. Fixed before results are seen; the
    total number of configurations is the trial count used by the Deflated Sharpe ratio."""
    from .strategies import BASE_CLASSES, RESEARCH_CLASSES
    from .strategies.adaptive import AdaptiveAIScalper
    plan = []
    for cls in list(BASE_CLASSES) + [AdaptiveAIScalper] + list(RESEARCH_CLASSES):
        d = int(cls.default_params.get("timeframe", 5))
        research_only = cls in RESEARCH_CLASSES and cls.bot_id.startswith("proto")
        tfs = [d] if research_only else ([d, 15] if d != 15 else [15, 60])
        for tf in tfs:
            grid = grid_from(dict(getattr(cls, "param_grid", {}) or {}), {"timeframe": tf}) or [{"timeframe": tf}]
            plan.append((cls.bot_id, tf, grid))
    return plan


def _cmd_run(a) -> None:
    import json
    import time
    from datetime import date
    from . import research as R
    from .backtest.metrics import json_safe
    R.SYMBOLS = a.symbols.split(",") if a.symbols else R.SYMBOLS_10
    plan = run_plan()
    n_total = sum(len(g) for _, _, g in plan)
    mine = [x for x in plan if x[0] == a.bot]
    if not mine:
        raise SystemExit(f"unknown bot {a.bot}")
    data = R._load(*R.SELECT, a.data_dir)
    regimes = regime_labels(data)
    t0 = min(int(b.open_time[0]) for b in data.values()) + 7 * DAY  # indicator warm-up
    t1 = max(int(b.open_time[-1]) for b in data.values())
    out = {"bot": a.bot, "selection": [str(x) for x in R.SELECT], "holdout_untouched": [str(x) for x in R.HOLDOUT],
           "symbols": R.SYMBOLS, "n_trials_total": n_total, "results": []}
    for bot, tf, grid in mine:
        t = time.time()
        rep = evaluate(data, bot, grid, (t0, t1), n_total, regimes=regimes)
        rep["timeframe"] = tf
        out["results"].append(rep)
        print(f"{bot} {tf}m -> {rep['verdict']} OOS n={rep['oos']['trades']} expR={rep['oos']['expectancy_r']} "
              f"({time.time() - t:.0f}s)", flush=True)
    with open(a.out, "w") as fh:
        json.dump(json_safe(out), fh, default=str)


def _fmt(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _cmd_aggregate(a) -> None:
    import glob
    import json
    from . import selection
    reps = []
    for p in sorted(glob.glob(a.inputs)):
        reps.extend(json.load(open(p))["results"])
    lines = ["# Research Engine 2.0 — results (selection period only, holdout untouched)", "",
             "| strategy | TF | OOS n | PF | exp R | pos. windows | max DD | DSR | PBO | STRESSED R | EXTREME R | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    trades, eligible = [], set()
    for r in reps:
        o, c = r["oos"], r["cost_scenarios"]
        lines.append(f"| {r['bot']} | {r.get('timeframe')}m | {o['trades']} | {_fmt(o.get('profit_factor'), 2)} | "
                     f"{_fmt(o.get('expectancy_r'))} | {_fmt(r['positive_windows'], 2)} | {_fmt(r['max_drawdown'], 3)} | "
                     f"{_fmt(r['deflated_sharpe'].get('dsr'))} | {_fmt(r['pbo'].get('pbo'), 2)} | "
                     f"{_fmt(c.get('STRESSED'))} | {_fmt(c.get('EXTREME'))} | {r['verdict']} |")
        key = f"{r['bot']}@{r.get('timeframe')}"
        if r["verdict"] == "CANDIDATE_FOR_PAPER":
            eligible.add(key)
        trades.extend({**t, "strategy": key} for t in r.get("oos_trades", []))
    now = max([t["closed_at"] for t in trades] or [0])
    sel = selection.select(trades, eligible, now)
    lines += ["", f"Candidates for paper trading: {len(eligible)} of {len(reps)}.",
              f"Selection Engine: {len(sel['cells'])} instrument/regime cells, "
              f"{sel['no_trade_cells']} NO_TRADE, chosen: {', '.join(sel['chosen_strategies']) or 'none'}.", "",
              "## Failed checks per strategy", ""]
    for r in reps:
        bad = [k for k, v in r["checks"].items() if not v["pass"]]
        lines.append(f"- {r['bot']} {r.get('timeframe')}m: " + (", ".join(bad) if bad else "all checks passed"))
    with open(a.out, "w") as fh:
        json.dump({"reports": [{k: v for k, v in r.items() if k != "oos_trades"} for r in reps],
                   "selection": sel}, fh, default=str)
    open(a.out.replace(".json", ".md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    import argparse
    import os
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--bot", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--symbols", default="")
    r.add_argument("--data-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    g = sub.add_parser("aggregate")
    g.add_argument("--inputs", required=True)
    g.add_argument("--out", required=True)
    sub.add_parser("plan")
    a = p.parse_args()
    if a.cmd == "plan":
        import json
        print(json.dumps(sorted({b for b, _, _ in run_plan()})))
        return
    {"run": _cmd_run, "aggregate": _cmd_aggregate}[a.cmd](a)


if __name__ == "__main__":
    main()
