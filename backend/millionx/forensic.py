"""Strategy forensics (MillionX 2.0, phase 1): WHY do the strategies lose?

Runs every base strategy on its own (shadow book, fixed 0.5 R stake, real costs)
on real Binance data for several timeframes, then breaks the closed trades down
by side, instrument, timeframe, market regime, exit reason, cost drag, losing
streaks, false signals (MAE/MFE) and overlap with the other strategies.

Read-only with respect to strategy code: this module only measures.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from .backtest import metrics
from .models import Bars, Trade

MIN_N = 30  # minimum trades for a bucket to be called better/worse than another


def _r(ts: Iterable[Trade]) -> np.ndarray:
    return np.array([t.r_multiple for t in ts], dtype=float)


def bucket_stats(ts: list[Trade]) -> dict[str, Any]:
    if not ts:
        return {"n": 0}
    r = _r(ts)
    gross = np.array([t.gross_pnl / t.risk_amount if t.risk_amount else 0.0 for t in ts])
    cost = np.array([(t.fees + t.funding) / t.risk_amount if t.risk_amount else 0.0 for t in ts])
    wins, losses = r[r > 0], r[r <= 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None
    return {"n": len(ts), "exp_r": round(float(r.mean()), 4), "sum_r": round(float(r.sum()), 2),
            "win_rate": round(float((r > 0).mean()), 4), "pf": round(pf, 3) if pf is not None else None,
            "avg_win_r": round(float(wins.mean()), 3) if len(wins) else None,
            "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else None,
            "gross_exp_r": round(float(gross.mean()), 4), "cost_r": round(float(cost.mean()), 4)}


def excursions(t: Trade, b: Bars) -> tuple[float, float, int]:
    """(MFE in R, MAE in R, bars held) from the 1-minute bars of the trade."""
    i0 = int(np.searchsorted(b.open_time, t.opened_at))
    i1 = max(i0 + 1, int(np.searchsorted(b.open_time, t.closed_at)))
    if i0 >= len(b):
        return 0.0, 0.0, 0
    hi, lo = float(b.high[i0:i1].max()), float(b.low[i0:i1].min())
    d = t.risk_amount / t.qty if t.qty else 0.0  # price distance that costs 1 R (incl. costs)
    if d <= 0:
        return 0.0, 0.0, i1 - i0
    if t.side == "LONG":
        return (hi - t.entry_price) / d, (t.entry_price - lo) / d, i1 - i0
    return (t.entry_price - lo) / d, (hi - t.entry_price) / d, i1 - i0


def streaks(ts: list[Trade]) -> dict[str, Any]:
    cur = worst = 0
    runs = []
    for t in sorted(ts, key=lambda x: x.closed_at):
        if t.r_multiple <= 0:
            cur += 1
            worst = max(worst, cur)
        else:
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return {"max_losing_streak": worst, "mean_losing_streak": round(float(np.mean(runs)), 2) if runs else 0,
            "streaks_ge_5": sum(1 for x in runs if x >= 5)}


def analyse(trades: list[Trade], bars: dict[str, Bars]) -> dict[str, Any]:
    out: dict[str, Any] = {"overall": bucket_stats(trades), "streaks": streaks(trades)}
    for name, key in (("by_side", lambda t: t.side), ("by_symbol", lambda t: t.symbol),
                      ("by_regime", lambda t: t.regime or "?"), ("by_exit", lambda t: t.exit_reason),
                      ("by_hour_utc", lambda t: f"{(t.opened_at // 3_600_000) % 24:02d}")):
        g: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            g[str(key(t))].append(t)
        out[name] = {k: bucket_stats(v) for k, v in sorted(g.items())}
    mfe, mae, held, false_sig = [], [], [], 0
    for t in trades:
        b = bars.get(t.symbol)
        if b is None:
            continue
        f, a, h = excursions(t, b)
        mfe.append(f)
        mae.append(a)
        held.append(h)
        if t.exit_reason == "stop_loss" and f < 0.3:
            false_sig += 1
    if mfe:
        out["excursions"] = {"median_mfe_r": round(float(np.median(mfe)), 3),
                             "median_mae_r": round(float(np.median(mae)), 3),
                             "share_mfe_ge_1r": round(float(np.mean(np.array(mfe) >= 1.0)), 3),
                             "median_bars_held": int(np.median(held)),
                             "false_signal_share": round(false_sig / len(trades), 3)}
    out["findings"] = findings(out)
    return out


def findings(a: dict[str, Any]) -> dict[str, Any]:
    """Deterministic diagnosis from the numbers (no judgement calls hidden in prose)."""
    o = a["overall"]
    f: dict[str, Any] = {}
    if not o.get("n"):
        return {"note": "no trades"}
    f["cost_eats_edge"] = bool(o["gross_exp_r"] > 0 >= o["exp_r"])
    f["negative_before_costs"] = bool(o["gross_exp_r"] <= 0)
    buckets = []
    for dim in ("by_side", "by_symbol", "by_regime", "by_exit"):
        for k, s in a.get(dim, {}).items():
            if s.get("n", 0) >= MIN_N:
                buckets.append((dim, k, s))
    if buckets:
        cand = [x for x in buckets if x[0] != "by_exit"] or buckets  # exits are effects, not causes
        worst = min(cand, key=lambda x: x[2]["sum_r"])
        f["largest_loss_source"] = {"dimension": worst[0], "bucket": worst[1], "sum_r": worst[2]["sum_r"],
                                    "n": worst[2]["n"]}
        good = [(d, k, s) for d, k, s in buckets if s["exp_r"] > 0 and d != "by_exit"]
        f["works_best_in"] = [{"dimension": d, "bucket": k, "exp_r": s["exp_r"], "n": s["n"]}
                              for d, k, s in sorted(good, key=lambda x: -x[2]["exp_r"])[:5]]
        f["switch_off_in"] = [{"dimension": d, "bucket": k, "exp_r": s["exp_r"], "n": s["n"]}
                              for d, k, s in buckets if d == "by_regime" and s["exp_r"] < -0.2]
    ex = a.get("excursions", {})
    if ex:
        f["false_signals_high"] = ex.get("false_signal_share", 0) > 0.35
    return f


def overlap(trades_by_bot: dict[str, list[Trade]], bucket_ms: int = 15 * 60_000) -> dict[str, Any]:
    """Redundancy: share of entries at the same symbol/side within the same 15-minute bucket,
    and correlation of daily R between strategies."""
    keys = {b: {(t.symbol, t.side, t.opened_at // bucket_ms) for t in ts} for b, ts in trades_by_bot.items()}
    daily: dict[str, dict[int, float]] = {}
    for b, ts in trades_by_bot.items():
        d: dict[int, float] = defaultdict(float)
        for t in ts:
            d[t.closed_at // 86_400_000] += t.r_multiple
        daily[b] = d
    bots = sorted(trades_by_bot)
    days = sorted({k for d in daily.values() for k in d})
    res = []
    for i, a in enumerate(bots):
        for b in bots[i + 1:]:
            inter = len(keys[a] & keys[b])
            jac = inter / max(1, len(keys[a] | keys[b]))
            corr = None
            if len(days) > 20:
                x = np.array([daily[a].get(k, 0.0) for k in days])
                y = np.array([daily[b].get(k, 0.0) for k in days])
                if x.std() > 0 and y.std() > 0:
                    corr = round(float(np.corrcoef(x, y)[0, 1]), 3)
            res.append({"a": a, "b": b, "shared_entries": inter, "jaccard": round(jac, 4), "daily_r_corr": corr})
    return {"pairs": sorted(res, key=lambda r: -(r["daily_r_corr"] or 0))[:15]}


def to_markdown(rep: dict[str, Any]) -> str:
    L = ["# STRATEGY FORENSIC REPORT (MillionX 2.0 — faza 1, baseline prije izmjena)", "",
         f"Podaci: {', '.join(rep['symbols'])}; razdoblje {rep['period'][0]} – {rep['period'][1]} "
         f"(završni holdout {rep['holdout'][0]} – {rep['holdout'][1]} NIJE korišten). "
         "Svaka strategija trguje sama (shadow knjiga, fiksno 0,5 R, stvarne naknade 0,05 % taker po strani, "
         "slippage 2 bps, funding). R = iznos rizika do stopa uključujući troškove.", "",
         "## Sažetak po strategiji i timeframeu", "",
         "| strategija | tf | n | exp R | PF | WR | bruto exp R | trošak R | max niz gubitaka | lažni signali | MFE med. |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for bot, by_tf in rep["bots"].items():
        for tf, a in by_tf.items():
            o, ex, st = a["overall"], a.get("excursions", {}), a.get("streaks", {})
            if not o.get("n"):
                L.append(f"| {bot} | {tf} | 0 | – | – | – | – | – | – | – | – |")
                continue
            L.append(f"| {bot} | {tf} | {o['n']} | {o['exp_r']} | {o['pf']} | {o['win_rate']} | {o['gross_exp_r']} | "
                     f"{o['cost_r']} | {st.get('max_losing_streak')} | {ex.get('false_signal_share')} | "
                     f"{ex.get('median_mfe_r')} |")
    L += ["", "## Nalazi po strategiji (automatski iz brojeva; bucket mora imati ≥ 30 trgovina)", ""]
    for bot, by_tf in rep["bots"].items():
        L.append(f"### {bot}")
        for tf, a in by_tf.items():
            fd = a.get("findings", {})
            if fd.get("note"):
                L.append(f"- {tf}: {fd['note']}")
                continue
            ls = fd.get("largest_loss_source", {})
            parts = [f"**{tf}**: najveći izvor gubitka = {ls.get('dimension')}:{ls.get('bucket')} "
                     f"({ls.get('sum_r')} R u {ls.get('n')} trgovina)"]
            if fd.get("negative_before_costs"):
                parts.append("negativna i PRIJE troškova (nema sirove prednosti)")
            elif fd.get("cost_eats_edge"):
                parts.append("pozitivna prije troškova, troškovi je pretvaraju u gubitak")
            if fd.get("false_signals_high"):
                parts.append("previše lažnih signala (>35 % stopova bez 0,3 R povoljnog pomaka)")
            if fd.get("works_best_in"):
                parts.append("pozitivno u: " + ", ".join(f"{x['dimension']}:{x['bucket']} ({x['exp_r']} R, n={x['n']})"
                                                         for x in fd["works_best_in"]))
            if fd.get("switch_off_in"):
                parts.append("isključiti u: " + ", ".join(f"{x['bucket']} ({x['exp_r']} R)" for x in fd["switch_off_in"]))
            L.append("- " + "; ".join(parts))
            for dim in ("by_side", "by_exit"):
                L.append(f"  - {dim}: " + ", ".join(f"{k} n={s['n']} {s.get('exp_r')}R"
                                                   for k, s in a.get(dim, {}).items() if s.get("n")))
        L.append("")
    L += ["## Redundancija (zajednički ulazi u istom 15-min intervalu, korelacija dnevnog R)", "",
          "| A | B | zajednički ulazi | Jaccard | korelacija dnevnog R |", "|---|---|---|---|---|"]
    for p in rep.get("overlap", {}).get("pairs", []):
        L.append(f"| {p['a']} | {p['b']} | {p['shared_entries']} | {p['jaccard']} | {p['daily_r_corr']} |")
    L += ["", "## Ograničenja", "- Povijesni order book ne postoji u Binance arhivi: bot3 je u backtestu CVD/taker-flow-only.",
          "- Bot 10 (adaptive) nema vlastite signale; prosljeđuje druge pa se ne testira samostalno.",
          "- MFE/MAE iz 1-minutnih svijeća (unutar svijeće nije poznat redoslijed high/low)."]
    return "\n".join(L)
