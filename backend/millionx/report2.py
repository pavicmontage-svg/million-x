"""Generate the 2.0 result documents from the aggregated research2.json (no numbers typed by hand).

  python -m millionx.report2 --input research2.json --out-dir ../docs
Writes BACKTEST_REPORT.md, WALK_FORWARD_REPORT.md, COST_ANALYSIS.md, STRATEGY_COMPARISON.md and
PROFITABILITY_REPORT.md. Each file starts with an "auto-generated" header naming the source run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from typing import Any


def f(x: Any, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, bool):
        return "da" if x else "ne"
    if isinstance(x, (int, float)):
        return f"{x:.{nd}f}" if isinstance(x, float) else str(x)
    return str(x)


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def header(title: str, run: str) -> list[str]:
    return [f"# {title}", "", f"*Automatski generirano iz Research Engine 2.0 (`{run}`) — selekcijsko razdoblje "
            "2025-01-01 – 2026-06-30, 10 instrumenata, završni holdout nije korišten. Brojevi nisu ručno upisani.*", ""]


def key(r):
    return f"{r['bot']} {r.get('timeframe')}m"


def backtest(reps, run):
    L = header("BACKTEST REPORT — MillionX 2.0 (OOS, BASE troškovi)", run)
    L += ["| strategija | n | WR | PF | exp R | avg win | avg loss | Sharpe | max DD | MC DD p95 | presuda |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(reps, key=lambda r: -(r["oos"].get("expectancy_r") or -9)):
        o, mc = r["oos"], r.get("monte_carlo") or {}
        L.append(f"| {key(r)} | {o.get('trades')} | {f(o.get('win_rate'), 3)} | {f(o.get('profit_factor'), 2)} | "
                 f"{f(o.get('expectancy_r'))} | {f(o.get('avg_win'), 4)} | {f(o.get('avg_loss'), 4)} | "
                 f"{f(o.get('sharpe'), 2)} | {f(r.get('max_drawdown'))} | {f(mc.get('max_dd_p95'))} | {r['verdict']} |")
    L += ["", "## LONG / SHORT (OOS)", "", "| strategija | LONG n | LONG exp R | SHORT n | SHORT exp R |", "|---|---|---|---|---|"]
    for r in reps:
        ls = r.get("long_short") or {}
        lo, sh = ls.get("LONG", {}), ls.get("SHORT", {})
        L.append(f"| {key(r)} | {lo.get('n', 0)} | {f(lo.get('exp_r'))} | {sh.get('n', 0)} | {f(sh.get('exp_r'))} |")
    L += ["", "## Po režimu (Regime 2.0), samo ćelije s n ≥ 30", ""]
    for r in reps:
        cells = [(k, v) for k, v in (r.get("by_regime") or {}).items() if v["n"] >= 30]
        if cells:
            L.append(f"- **{key(r)}**: " + ", ".join(f"{k} {v['exp_r']:+.3f} R (n={v['n']})" for k, v in cells))
    return L


def walkforward(reps, run):
    L = header("WALK-FORWARD REPORT — ugniježđeni WF (90 d trening / 30 d test, purged 3-fold CV + embargo)", run)
    L += ["| strategija | prozori | pozitivni | DSR | PBO | stabilnost param. | cross-asset exp R | najčešća konfiguracija |",
          "|---|---|---|---|---|---|---|---|"]
    for r in reps:
        w = r.get("windows") or []
        ca = (r.get("cross_asset") or {}).get("exp_r")
        L.append(f"| {key(r)} | {len(w)} | {f(r.get('positive_windows'), 2)} | {f((r.get('deflated_sharpe') or {}).get('dsr'))} | "
                 f"{f((r.get('pbo') or {}).get('pbo'), 2)} | {f(r.get('parameter_stability'), 2)} | {f(ca)} | "
                 f"`{json.dumps(r.get('most_chosen_config'))}` |")
    L += ["", "## Prozori po strategiji (OOS exp R po test prozoru)", ""]
    for r in reps:
        w = r.get("windows") or []
        L.append(f"- **{key(r)}**: " + " · ".join(
            f"{day(x['test'][0])}: {('—' if x['oos_exp_r'] is None else format(x['oos_exp_r'], '+.2f'))} (n={x['oos_n']})"
            for x in w))
    return L


def costs(reps, run):
    L = header("COST ANALYSIS — BASE / STRESSED / EXTREME", run)
    L += ["Scenariji: vidi `RESEARCH_METHODOLOGY.md` §3. STRESSED/EXTREME su ponovno simulirani za konfiguracije "
          "koje je WF stvarno odabrao, na istim test prozorima.", "",
          "| strategija | BASE exp R | STRESSED exp R | EXTREME exp R | Δ R po dodatnom bps | preživi STRESSED |",
          "|---|---|---|---|---|---|"]
    for r in reps:
        c, cs = r.get("cost_scenarios") or {}, r.get("cost_sensitivity") or {}
        L.append(f"| {key(r)} | {f(c.get('BASE'))} | {f(c.get('STRESSED'))} | {f(c.get('EXTREME'))} | "
                 f"{f(cs.get('exp_r_per_extra_bp'), 4)} | {f(cs.get('survives_stressed'))} |")
    L += ["", "Ograničenja: djelomični fillovi nisu modelirani; povijesni order book ne postoji (bot3 = CVD-only); "
          "funding je procjena iz konfigurirane stope."]
    return L


def comparison(reps, sel, run):
    L = header("STRATEGY COMPARISON — v1 vs v2 vs prototipovi, i Strategy Selection Engine", run)
    by = {key(r): r for r in reps}
    L += ["## v1 → v2 (isti timeframe)", "", "| bot | TF | v1 exp R | v2 exp R | v1 n | v2 n |", "|---|---|---|---|---|---|"]
    for r in reps:
        if r["bot"].endswith("_v2"):
            v1 = by.get(f"{r['bot'][:-3]} {r.get('timeframe')}m")
            L.append(f"| {r['bot'][:-3]} | {r.get('timeframe')}m | {f(v1['oos'].get('expectancy_r')) if v1 else '— (v1 nije na tom TF-u)'} | "
                     f"{f(r['oos'].get('expectancy_r'))} | {v1['oos'].get('trades') if v1 else '—'} | {r['oos'].get('trades')} |")
    L += ["", "## Strategy Selection Engine", "",
          f"Ćelije (instrument × režim): {len(sel.get('cells', {}))}, NO TRADE: {sel.get('no_trade_cells')}, "
          f"odabrane strategije: {', '.join(sel.get('chosen_strategies') or []) or 'nijedna'}.", "",
          "| instrument | režim | odluka | najbolji kandidat | n | CI low | razlog |", "|---|---|---|---|---|---|---|"]
    for c in sorted((sel.get("cells") or {}).values(), key=lambda c: (c["symbol"], c["regime"])):
        top = (c.get("candidates") or [{}])[0]
        L.append(f"| {c['symbol']} | {c['regime']} | {c['selected']} | {top.get('strategy', '—')} | {top.get('n', '—')} | "
                 f"{f(top.get('ci95_low'))} | {'; '.join(top.get('reasons') or []) or c.get('why', '')} |")
    return L


def profitability(reps, sel, run):
    cands = [r for r in reps if r["verdict"] == "CANDIDATE_FOR_PAPER"]
    pos = [r for r in reps if (r["oos"].get("expectancy_r") or -1) > 0]
    L = header("PROFITABILITY REPORT — MillionX 2.0", run)
    L += [f"- Testirano kombinacija (strategija × TF): **{len(reps)}**",
          f"- Pozitivan OOS exp R (BASE troškovi): **{len(pos)}** — {', '.join(key(r) for r in pos) or 'nijedna'}",
          f"- Prošlo Activation 2.0 (kandidat za paper): **{len(cands)}** — {', '.join(key(r) for r in cands) or 'nijedna'}",
          f"- Selection Engine: {sel.get('no_trade_cells')} od {len(sel.get('cells', {}))} ćelija = NO TRADE", "",
          "## Koje provjere padaju (broj strategija)", ""]
    cnt: dict[str, int] = {}
    for r in reps:
        for k, v in r["checks"].items():
            if not v["pass"]:
                cnt[k] = cnt.get(k, 0) + 1
    for k, n in sorted(cnt.items(), key=lambda x: -x[1]):
        L.append(f"- {k}: {n} / {len(reps)}")
    return L


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run", default="research2")
    a = p.parse_args()
    d = json.load(open(a.input))
    reps, sel = d["reports"], d.get("selection") or {}
    docs = {"BACKTEST_REPORT.md": backtest(reps, a.run), "WALK_FORWARD_REPORT.md": walkforward(reps, a.run),
            "COST_ANALYSIS.md": costs(reps, a.run), "STRATEGY_COMPARISON.md": comparison(reps, sel, a.run),
            "PROFITABILITY_REPORT.md": profitability(reps, sel, a.run)}
    for name, lines in docs.items():
        with open(os.path.join(a.out_dir, name), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
