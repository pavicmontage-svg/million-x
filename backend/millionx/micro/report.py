"""Generate the 2.1 result documents from CI outputs (no hand-typed numbers).

  python -m millionx.micro.report --research micro_research.json --quality-dir /tmp/md --out-dir ../docs
Writes DATA_QUALITY_REPORT, FUNDING/OPEN_INTEREST/LIQUIDATION/ORDER_BOOK/CVD/MICROSTRUCTURE research reports.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
from typing import Any

TOPIC = {"FUNDING_RESEARCH_REPORT.md": ("Funding Rate", ("A", "E"), "funding"),
         "OPEN_INTEREST_RESEARCH_REPORT.md": ("Open Interest", ("A", "B", "E"), "open_interest"),
         "ORDER_BOOK_RESEARCH_REPORT.md": ("Order Book (bookDepth ±%)", ("F",), "order_book_depth"),
         "CVD_RESEARCH_REPORT.md": ("Cumulative Volume Delta", ("B", "D"), "agg_trades")}


def f(x: Any, nd: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x)


def head(title: str, run: str) -> list[str]:
    return [f"# {title}", "", f"*Automatski generirano iz CI izlaza (`{run}`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 "
            "(samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. "
            "Brojevi nisu ručno upisani.*", ""]


def table(rows: list[dict[str, Any]]) -> list[str]:
    L = ["| uvjet | h | smjer | n (val) | bruto bps | neto bps BASE | 95 % CI | FDR q | STRESSED bps | WR | PF | /dan/instr. | instr. + | kandidat |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["condition"], r["horizon_min"])):
        v = r["validation"]
        ci = r.get("validation_ci95_bps") or [None, None]
        L.append(f"| {r['condition']} | {r['horizon_min']}m | {r['direction_from_discovery'][:6]} | {v.get('n', 0)} | "
                 f"{f(r.get('validation_gross_bps'))} | {f(v.get('exp_bps'))} | {f(ci[0])} … {f(ci[1])} | {f(r.get('fdr_q'), 3)} | "
                 f"{f(r.get('stressed_exp_bps'))} | {f(v.get('win_rate'), 2)} | {f(v.get('pf'), 2)} | {f(v.get('per_day'), 2)} | "
                 f"{r['symbols_positive']}/{r['symbols_tested']} | {'DA' if r['edge_candidate'] else 'ne'} |")
    return L


def quality_doc(q: dict[str, Any], run: str) -> list[str]:
    L = head("DATA QUALITY REPORT — MillionX 2.1 (arhiva, 2024-12-01 – 2026-06-30)", run)
    L += ["Provjere po izvoru i instrumentu (Data Quality Engine, `micro/quality.py`): praznine, duplikati, redoslijed, "
          "nevaljale vrijednosti, abnormalni skokovi, pokrivenost; za aggTrades i konzistentnost s kline taker volumenom.", "",
          "| instrument | izvor | status | pokrivenost | praznine | nedostaje točaka | duplikati | van reda | nevaljalo | abnormalno | nedostaje dana |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sym in sorted(q):
        for src, x in q[sym]["sources"].items():
            L.append(f"| {sym} | {src} | {x.get('status')} | {f(x.get('coverage'), 4)} | {x.get('gaps', '—')} | "
                     f"{x.get('missing_points', '—')} | {x.get('duplicates', '—')} | {x.get('out_of_order', '—')} | "
                     f"{x.get('invalid_values', '—')} | {x.get('abnormal_values', '—')} | {len(x.get('missing_days') or [])} |")
    L += ["", "## aggTrades vs kline taker-buy volumen (ista minuta)", "",
          "| instrument | uspoređeno minuta | medijan relativne razlike | udio unutar 1 % |", "|---|---|---|---|"]
    for sym in sorted(q):
        c = (q[sym]["sources"].get("agg_trades") or {}).get("consistency_vs_kline_taker_buy") or {}
        L.append(f"| {sym} | {c.get('minutes_compared', '—')} | {f(c.get('median_rel_diff'), 6)} | {f(c.get('share_within_1pct'), 4)} |")
    L += ["", "## Mapiranje simbola", ""]
    bad = {s: q[s].get("symbol_mapping") for s in q if q[s].get("symbol_mapping")}
    L.append("Nema redova tuđeg simbola u datotekama." if not bad else json.dumps(bad))
    L += ["", "## Funding intervali (sati) viđeni u arhivi", ""]
    for sym in sorted(q):
        fs = q[sym]["sources"].get("funding") or {}
        L.append(f"- {sym}: {fs.get('intervals_h')} ({fs.get('settlements')} namirenja)")
    return L


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--research", required=True)
    p.add_argument("--quality-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run", default="micro-research")
    a = p.parse_args()
    o = json.load(open(a.research))
    q = {}
    for path in glob.glob(os.path.join(a.quality_dir, "*USDT.json")):
        d = json.load(open(path))
        q[d["symbol"]] = d
    docs: dict[str, list[str]] = {"DATA_QUALITY_REPORT.md": quality_doc(q, a.run)}
    res = o["results"]
    for name, (title, combos, src) in TOPIC.items():
        L = head(f"{title.upper()} RESEARCH REPORT — MillionX 2.1", a.run)
        L += [f"Kombinacije koje koriste ovaj izvor: {', '.join(combos)}. Kvaliteta izvora po instrumentu: `DATA_QUALITY_REPORT.md` (izvor `{src}`).", ""]
        L += table([r for r in res if r["combo"] in combos])
        docs[name] = L
    L = head("LIQUIDATION RESEARCH REPORT — MillionX 2.1", a.run)
    L += ["**Status: INCOMPLETE.**", "", o["status"]["C"]["reason"] + ".", "",
          "- Istraživanje C (Liquidations + Order Book Imbalance) nije provedeno; likvidacijski dio E također.",
          "- Likvidacije se NE procjenjuju iz cijene, volumena ni OI-ja (to bi bili izmišljeni podaci).",
          "- Od 2.1 MillionX snima likvidacije uživo (`micro/collector.py`, `!forceOrder` stream); Binance objavljuje najviše "
          "1 likvidaciju po simbolu u sekundi, pa je snimljeni volumen **donja granica**.",
          "- Klasifikacija (long/short squeeze, kaskada, sweep, post-liquidation reversal) definirat će se i testirati tek kad "
          "snimljeni uzorak bude dovoljno velik (cilj: ≥ 3 mjeseca, ≥ 100 događaja po klasi), uz isti protokol (discovery/validacija, FDR)."]
    docs["LIQUIDATION_RESEARCH_REPORT.md"] = L
    L = head("MICROSTRUCTURE RESEARCH REPORT — MillionX 2.1 (A–F)", a.run)
    L += [f"Testova (uvjet × horizont): **{o['tests']}**, Benjamini–Hochberg FDR preko svih. Troškovi po round-tripu: "
          f"BASE {o['costs_bps']['BASE']} bps, STRESSED {o['costs_bps']['STRESSED']} bps (+1 min latencije).", "",
          f"**Kandidati za prednost: {len(o['edge_candidates'])}** — {', '.join(o['edge_candidates']) or 'nijedan'}", ""]
    for k, v in o["status"].items():
        L.append(f"- **{k}: {v['status']}** — {v['reason']}")
    L += [""] + table(res)
    L += ["", "## Izvori po instrumentu", ""] + [f"- {s}: {json.dumps(v)}" for s, v in o["sources"].items()]
    docs["MICROSTRUCTURE_RESEARCH_REPORT.md"] = L
    for name, lines in docs.items():
        open(os.path.join(a.out_dir, name), "w").write("\n".join(lines) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
