"""Generate 2.2 report sections from scalp_research.json (no hand-typed numbers).

  python -m millionx.scalp.report --input scalp_research.json --docs ../docs --run "scalp-research run N"
Writes MOMENTUM_SCALPER_REPORT.md and PULLBACK_SCALPER_REPORT.md, and PREPENDS a 2.2 section to
BACKTEST_REPORT.md, WALK_FORWARD_REPORT.md and COST_ANALYSIS.md (earlier versions are kept below it).
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any


def f(x: Any, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


NSYM = {"n": 10}


def head(run: str) -> str:
    return (f"*Automatski generirano iz `{run}` (event-driven 1 s simulacija na aggTrades, {NSYM['n']} instrumenata). "
            "Discovery 2025-01 – 2025-09 (kalibracija i izbor konfiguracije), validacija 2025-10 – 2026-06. "
            "Holdout 2026-07 – 2026-10 nije korišten. Brojevi nisu ručno upisani.*")


def raw_table(o: dict[str, Any], eng: str) -> list[str]:
    L = ["| konfiguracija @ odluka | val n | bruto R | neto R BASE | neto R STRESSED | neto R EXTREME | neto R MAKER | WR | avg win R | avg loss R | PF | hold s | naknade/bruto dobit | neto EUR/dan |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in o["raw"].items():
        if not k.startswith(eng):
            continue
        va = v["validation"]
        b = va["BASE"]
        L.append(f"| {k} | {b.get('n', 0)} | {f(va.get('gross_r'))} | {f(b.get('net_r'))} | {f(va['STRESSED'].get('net_r'))} | "
                 f"{f(va['EXTREME'].get('net_r'))} | {f(va['MAKER'].get('net_r'))} | {f(b.get('win_rate'), 2)} | {f(b.get('avg_win_r'), 2)} | "
                 f"{f(b.get('avg_loss_r'), 2)} | {f(b.get('pf'), 2)} | {f(b.get('avg_hold_s'), 0)} | {f(b.get('fee_to_gross_profit'), 2)} | "
                 f"{f(b.get('net_eur_per_day'), 1)} |")
    return L


def engine_doc(o: dict[str, Any], eng: str, title: str, run: str, logic: str) -> list[str]:
    v = o["validation"].get(eng, {})
    L = [f"# {title} — MillionX 2.2", "", head(run), "", "## Logika (unaprijed registrirana)", "", logic, "",
         f"**Presuda: {o['verdict'].get(eng)}** · konfiguracija odabrana na discoveryju: `{o['chosen'].get(eng)}` · "
         f"prihvaćeno Opportunity Engineom na validaciji: {v.get('accepted', 0)}", "",
         "## Svi kandidati bez filtra (validacija, BASE ako nije drukčije navedeno)", ""] + raw_table(o, eng)
    L += ["", "## Walk-forward (kalibracija se širi kvartal po kvartal)", ""]
    for q in o["walk_forward"].get(eng, []):
        L.append(f"- {q['quarter']}: prihvaćeno {q['accepted']}, neto R {f(q['net_r'])}, neto EUR {f(q['net_eur'], 1)}")
    return L


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--docs", required=True)
    p.add_argument("--run", required=True)
    a = p.parse_args()
    o = json.load(open(a.input))
    NSYM["n"] = len(o.get("symbols") or [])
    docs = a.docs
    A = ("Engine A (Momentum): 30 s brzina ≥ z·σ, relativni volumen ≥ prag, udio agresivnih kupnji/prodaja ≥ prag, proboj 3-min "
         "raspona, rast CVD akceleracije, potvrda 3-min momenta, nije iscrpljen (pomak od 5-min ekstrema ≤ ext_max σ). "
         "Stop = sl_sigma × 1-min σ; cilj 2R/3R/5R; break-even, trailing, exhaustion i order-flow reversal izlazi; max 15 min "
         "(do 45 min samo za poziciju u trailingu). Odluka svake sekunde (1 s) ili samo na zatvaranju 1m/3m/5m bara.")
    B = ("Engine B (Pullback): 1m EMA9/21 trend u smjeru 5m trenda; povlačenje u zonu EMA21/VWAP (dubina u ATR-ovima); "
         "volumen pada tijekom povlačenja; ulaz tek kad se momentum vrati (1 s brzina i CVD u smjeru trenda) i cijena probije "
         "60-sekundni ekstrem — nikad na sam dodir EMA-e. Stop = sl_atr × 1m ATR; ciljevi 3R/5R; isti izlazi kao A.")
    for name, eng, title, logic in (("MOMENTUM_SCALPER_REPORT.md", "A", "MOMENTUM SCALPER REPORT", A),
                                    ("PULLBACK_SCALPER_REPORT.md", "B", "PULLBACK SCALPER REPORT", B)):
        open(os.path.join(docs, name), "w").write("\n".join(engine_doc(o, eng, title, a.run, logic)) + "\n")
    # prepended sections
    bt = ["# 2.2 — Scalping backtest (event-driven 1 s)", "", head(a.run), "",
          f"**Presuda: {o['verdict']}**", "", "## Validacija nakon Opportunity Engine filtra", "",
          "| engines | scenarij | n | neto R | neto EUR | PF | WR | avg win R | avg loss R | max DD EUR | max niz gubitaka | hold s | naknade/bruto |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in o["validation"].items():
        for sc, s in v["scenarios"].items():
            bt.append(f"| {name} | {sc} | {s.get('n', 0)} | {f(s.get('net_r'))} | {f(s.get('net_eur_total'), 1)} | {f(s.get('pf'), 2)} | "
                      f"{f(s.get('win_rate'), 2)} | {f(s.get('avg_win_r'), 2)} | {f(s.get('avg_loss_r'), 2)} | {f(s.get('max_dd_eur'), 1)} | "
                      f"{s.get('max_consecutive_losses', '—')} | {f(s.get('avg_hold_s'), 0)} | {f(s.get('fee_to_gross_profit'), 2)} |")
    bt += ["", "## Portfelj (500 EUR, 2,50 EUR rizika, 2 pozicije, 10 EUR/dan, ISOLATED)", ""]
    for name, pp in o["portfolio"].items():
        for lev, s in pp.items():
            bt.append(f"- {name} {lev}: n={s.get('n', 0)}, neto EUR={f(s.get('net_eur_total'), 1)}, transakcija/dan={f(s.get('trades_per_day'), 2)}, "
                      f"preskočeno={s.get('skipped')}")
    bt += ["", "Detalji po engineu: `MOMENTUM_SCALPER_REPORT.md`, `PULLBACK_SCALPER_REPORT.md`.", "", "---", ""]
    wf = ["# 2.2 — Scalping walk-forward", "", head(a.run), ""]
    for name, w in o["walk_forward"].items():
        wf.append(f"- **{name}**: " + "; ".join(f"{q['quarter']}: n={q['accepted']}, neto R={f(q['net_r'])}, EUR={f(q['net_eur'], 1)}" for q in w))
    wf += ["", "---", ""]
    for fn, sec in (("BACKTEST_REPORT.md", bt), ("WALK_FORWARD_REPORT.md", wf)):
        path = os.path.join(docs, fn)
        old = open(path).read() if os.path.exists(path) else ""
        if old.startswith("# 2.2 —"):
            old = old.split("\n---\n", 1)[1] if "\n---\n" in old else ""
        open(path, "w").write("\n".join(sec) + "\n" + old)
    print("ok")


if __name__ == "__main__":
    main()
