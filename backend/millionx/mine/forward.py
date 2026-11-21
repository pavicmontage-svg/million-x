"""Forward test of FROZEN candidates on data they have never seen (MillionX 2.4).

  python -m millionx.mine.forward freeze   --mine mine.json --out candidates.json
  python -m millionx.mine.forward evaluate --candidates candidates.json --symbol BTCUSDT --start 2026-07-01 --out fw_BTCUSDT.json
  python -m millionx.mine.forward judge    --candidates candidates.json --inputs "fw_*.json" --out forward.json

* freeze: the top discovery rules of the massive search (both cost scenarios) are written with a SHA-256 hash and a
  timestamp. Nothing about them may change afterwards; the hash is checked at every evaluation.
* evaluate: builds the same causal 1-minute table for a period AFTER the research data (the final holdout
  2026-07 … 2026-10 is used here for the first time, for frozen rules only, then every new week), with 30 days of
  warm-up for the rolling percentiles, and records every candidate's non-overlapping trades.
* judge: a candidate PASSES only with ALL of: >= 200 forward trades, mean net after costs > 0 with one-sided
  p < 0.05 / number of candidates (Bonferroni), positive on >= 6 instruments, positive in >= 2/3 of the
  forward months (at least 3 months), and maximum drawdown of the 1 x notional equity curve < 10 %.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import time
from datetime import date, timedelta
from typing import Any

import numpy as np

from . import search as S


def _hash(c: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()


def freeze(mine: dict[str, Any], per_scenario: int = 200) -> dict[str, Any]:
    cands = []
    for cname, s in mine["scenarios"].items():
        for c in s["top_by_discovery"][:per_scenario]:
            cands.append({"rule": c["rule"], "horizon_min": c["horizon_min"], "direction": c["direction"],
                          "cost_scenario": cname, "cost_bps": mine["costs_bps"][cname]})
    return {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": "massive search run 44934979477",
            "n": len(cands), "sha256": _hash(cands), "candidates": cands}


def evaluate(cfile: dict[str, Any], sym: str, start: date, end: date, kline_dir: str, log=print) -> dict[str, Any]:
    assert _hash(cfile["candidates"]) == cfile["sha256"], "frozen candidate file was modified"
    from ..micro import archive
    import tempfile
    cols, rep = archive.build_symbol(sym, start - timedelta(days=31), end, kline_dir, forward_test_of_frozen=True, log=log)
    with tempfile.TemporaryDirectory() as td:  # same float32 storage round-trip as the research tables
        archive.save(cols, os.path.join(td, sym))
        cols = archive.load(os.path.join(td, sym))
    names, P = S.primitives(cols, log=lambda *_: None)
    pos = {n: i for i, n in enumerate(names)}
    t = cols["open_time"].astype(np.int64)
    close = cols["close"].astype(float)
    t0 = S._ms(str(start))
    live = (t >= t0) & np.isfinite(close)
    out = []
    for c in cfile["candidates"]:
        if any(r not in pos for r in c["rule"]):
            out.append({"missing_primitive": [r for r in c["rule"] if r not in pos]})
            continue
        m = live.copy()
        for r in c["rule"]:
            m &= P[pos[r]]
        h = c["horizon_min"]
        e = S._nonoverlap(np.flatnonzero(m), h)
        e = e[e + h < len(close)]
        d = 1.0 if c["direction"] == "LONG" else -1.0
        net = d * (close[e + h] / close[e] - 1) * 1e4 - c["cost_bps"]
        ok = np.isfinite(net)
        out.append({"t": t[e][ok].tolist(), "net_bps": np.round(net[ok], 3).tolist()})
    return {"symbol": sym, "start": str(start), "end": str(end), "sha256": cfile["sha256"], "results": out,
            "data_quality": {k: v.get("status") for k, v in rep["sources"].items()}}


def judge(cfile: dict[str, Any], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    n_c = len(cfile["candidates"])
    alpha = 0.05 / n_c
    rows = []
    for ci, c in enumerate(cfile["candidates"]):
        allr, allt, per_sym = [], [], {}
        for inp in inputs:
            assert inp["sha256"] == cfile["sha256"], "evaluation of a different candidate set"
            r = inp["results"][ci]
            if "net_bps" not in r:
                continue
            allr += r["net_bps"]
            allt += r["t"]
            if len(r["net_bps"]) >= 10:
                per_sym[inp["symbol"]] = float(np.mean(r["net_bps"]))
        n = len(allr)
        x = np.array(allr)
        mean = float(x.mean()) if n else float("nan")
        sd = float(x.std(ddof=1)) if n > 1 else float("nan")
        z = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
        p = 0.5 * math.erfc(z / math.sqrt(2)) if math.isfinite(z) else 1.0
        months: dict[str, list[float]] = {}
        for ti, ri in zip(allt, allr):
            months.setdefault(time.strftime("%Y-%m", time.gmtime(ti / 1000)), []).append(ri)
        mpos = [float(np.mean(v)) > 0 for v in months.values()]
        order = np.argsort(allt)
        eq = np.cumsum(x[order] / 1e4) if n else np.zeros(1)
        dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()) if n else 0.0
        passed = (n >= 200 and mean > 0 and p < alpha and sum(v > 0 for v in per_sym.values()) >= 6
                  and len(months) >= 3 and sum(mpos) >= math.ceil(2 / 3 * len(months)) and dd < 0.10)
        rows.append({**c, "forward_n": n, "forward_net_bps": mean, "z": z, "p_one_sided": p, "p_bonferroni": min(1.0, p * n_c),
                     "symbols_positive": sum(v > 0 for v in per_sym.values()), "symbols_tested": len(per_sym),
                     "months": {k: round(float(np.mean(v)), 2) for k, v in sorted(months.items())},
                     "max_dd_1x": dd, "trades_per_day": n / max(1, len({time.strftime('%Y-%m-%d', time.gmtime(ti / 1000)) for ti in allt})),
                     "PASS": bool(passed)})
    rows.sort(key=lambda r: (not r["PASS"], -(r["forward_net_bps"] if math.isfinite(r["forward_net_bps"]) else -1e9)))
    period = (min(i["start"] for i in inputs), max(i["end"] for i in inputs)) if inputs else (None, None)
    return {"evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "period": period, "n_candidates": n_c,
            "bonferroni_alpha": alpha, "passed": [r for r in rows if r["PASS"]], "ranking": rows,
            "symbols": sorted(i["symbol"] for i in inputs)}


def to_markdown(j: dict[str, Any]) -> str:
    f = lambda x, nd=2: "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))  # noqa: E731
    L = [f"# Forward test of {j['n_candidates']} frozen candidates — {j['period'][0]} … {j['period'][1]}", "",
         f"Evaluated {j['evaluated_at']} on {', '.join(j['symbols'])}. **PASSED: {len(j['passed'])}**", "",
         "| rule | h | dir | costs | n | net bps | p (Bonf.) | sym + | months | maxDD | trades/day | PASS |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in j["ranking"][:40]:
        L.append(f"| {' & '.join(r['rule'])} | {r['horizon_min']} | {r['direction']} | {r['cost_scenario']} | {r['forward_n']} | {f(r['forward_net_bps'])} | "
                 f"{f(r['p_bonferroni'], 3)} | {r['symbols_positive']}/{r['symbols_tested']} | {r['months']} | {f(r['max_dd_1x'], 3)} | "
                 f"{f(r['trades_per_day'])} | {'YES' if r['PASS'] else ''} |")
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("freeze")
    a1.add_argument("--mine", required=True)
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("evaluate")
    a2.add_argument("--candidates", required=True)
    a2.add_argument("--symbol", required=True)
    a2.add_argument("--start", default="2026-07-01")
    a2.add_argument("--end", default="")
    a2.add_argument("--kline-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    a2.add_argument("--out", required=True)
    a3 = sub.add_parser("judge")
    a3.add_argument("--candidates", required=True)
    a3.add_argument("--inputs", required=True)
    a3.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "freeze":
        json.dump(freeze(json.load(open(a.mine))), open(a.out, "w"), indent=1)
    elif a.cmd == "evaluate":
        end = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=2)  # archive files lag ~1 day
        json.dump(evaluate(json.load(open(a.candidates)), a.symbol, date.fromisoformat(a.start), end, a.kline_dir),
                  open(a.out, "w"))
    else:
        j = judge(json.load(open(a.candidates)), [json.load(open(x)) for x in sorted(glob.glob(a.inputs))])
        json.dump(j, open(a.out, "w"), default=str)
        open(a.out.replace(".json", ".md"), "w").write(to_markdown(j))
        print(to_markdown(j))


if __name__ == "__main__":
    main()
