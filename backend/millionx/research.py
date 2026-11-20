"""Timeframe research with a pre-registered protocol (run on CI, real data).

Protocol (fixed BEFORE looking at results):
  1. Selection period: walk-forward (train 60 d / test 30 d, parameters chosen on
     train only) for every base bot at each timeframe. Only out-of-sample trades count.
  2. A (bot, timeframe) is a candidate only if its OOS result meets the activation
     criteria (trades >= 100, PF >= 1.15, expectancy >= 0.05 R, >= 60 % positive windows).
  3. Holdout period (never used in steps 1-2): candidates are run once with the
     parameters chosen in their last walk-forward window. No re-tuning.
Costs: configured taker fees both sides + slippage + funding estimate.

  python -m millionx.research wf --tf 15 --out wf_15.json
  python -m millionx.research holdout --inputs wf_*.json --out holdout.json
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import time
from datetime import date

from .backtest import metrics
from .backtest.data import ensure_data, load_symbol
from .backtest.walkforward import _virtual, walk_forward
from .config import Settings
from .strategies import BASE_CLASSES

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
SELECT = (date(2025, 1, 1), date(2026, 6, 30))
HOLDOUT = (date(2026, 7, 1), date(2026, 10, 31))
DAY_MS = 86_400_000


def _load(start: date, end: date, data_dir: str) -> dict:
    out = {}
    for s in SYMBOLS:
        ensure_data(s, data_dir, start, end, progress=lambda t: print(t, flush=True))
        out[s] = load_symbol(s, data_dir, start, end)
        print(f"{s}: {len(out[s]):,} candles", flush=True)
    return out


def cmd_wf(a) -> None:
    s = Settings()
    data = _load(*SELECT, a.data_dir)
    res = {}
    for cls in BASE_CLASSES:
        t = time.time()
        warm = max(3, math.ceil(a.tf * getattr(cls, "warmup_bars", 60) / 1440) + 1)
        wf = walk_forward(data, s, cls.bot_id, train_days=60, test_days=30, warmup_days=warm,
                          base_params={"timeframe": a.tf}, max_combos=a.max_combos)
        d = wf.to_dict()
        d.pop("windows", None)
        d["last_params"] = wf.windows[-1]["params"] if wf.windows else None
        d["n_windows"] = len(wf.windows)
        res[cls.bot_id] = d
        om = d["oos_metrics"]
        print(f"tf={a.tf} {cls.bot_id}: n={om.get('trades')} PF={om.get('profit_factor')} "
              f"expR={om.get('expectancy_r')} pos={d['positive_window_share']} -> {d['activation']['status']} "
              f"({time.time() - t:.0f}s)", flush=True)
    with open(a.out, "w") as fh:
        json.dump({"tf": a.tf, "period": [str(x) for x in SELECT], "symbols": SYMBOLS, "results": res}, fh, default=str)


def cmd_holdout(a) -> None:
    s = Settings()
    runs = [json.load(open(f)) for f in sorted(glob.glob(a.inputs))]
    cands = []
    table = []
    for r in runs:
        for bot, d in r["results"].items():
            om = d["oos_metrics"]
            row = {"bot": bot, "tf": r["tf"], "oos_trades": om.get("trades"), "oos_pf": om.get("profit_factor"),
                   "oos_exp_r": om.get("expectancy_r"), "positive_windows": d.get("positive_window_share"),
                   "status": d["activation"]["status"]}
            table.append(row)
            if d["activation"]["status"] in ("BACKTEST_PASSED", "LIVE_ELIGIBLE") and d.get("last_params"):
                cands.append((bot, r["tf"], d["last_params"]))
    hold = []
    if cands:
        data = _load(*HOLDOUT, a.data_dir)
        t0 = min(int(b.open_time[0]) for b in data.values()) + 3 * DAY_MS
        for bot, tf, params in cands:
            trs = _virtual(data, s, bot, params, t0)
            m = metrics.compute(trs, 100.0)
            hold.append({"bot": bot, "tf": tf, "params": params, "trades": m["trades"],
                         "pf": m["profit_factor"], "exp_r": m["expectancy_r"], "win_rate": m["win_rate"]})
            print(f"HOLDOUT {bot} tf={tf}: n={m['trades']} PF={m['profit_factor']} expR={m['expectancy_r']}", flush=True)
    out = {"protocol": __doc__, "selection": [str(x) for x in SELECT], "holdout": [str(x) for x in HOLDOUT],
           "symbols": SYMBOLS, "table": table, "candidates": len(cands), "holdout_results": hold}
    with open(a.out, "w") as fh:
        json.dump(metrics.json_safe(out), fh, default=str, indent=1)
    lines = ["| bot | tf | OOS trades | OOS PF | OOS exp R | positive windows | status |", "|---|---|---|---|---|---|---|"]
    for r in sorted(table, key=lambda x: (x["bot"], x["tf"])):
        lines.append(f"| {r['bot']} | {r['tf']}m | {r['oos_trades']} | {r['oos_pf']} | {r['oos_exp_r']} | "
                     f"{r['positive_windows']} | {r['status']} |")
    lines.append("")
    lines.append(f"Candidates passing OOS criteria: {len(cands)}")
    for h in hold:
        lines.append(f"- HOLDOUT {h['bot']} {h['tf']}m: trades {h['trades']}, PF {h['pf']}, exp R {h['exp_r']}")
    open(a.out.replace(".json", ".md"), "w").write("\n".join(lines))
    print("\n".join(lines))




def cmd_leverage(a) -> None:
    """Same historical signals at fixed 5x / 10x / 15x / 20x (fees, slippage, funding, isolated liquidation)."""
    import copy
    from .backtest.engine import BacktestConfig, run
    from .strategies import ALL_IDS
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    data = _load(start, end, a.data_dir)
    params = {b: {"timeframe": a.tf} for b in ALL_IDS if b != "bot10_adaptive"} if a.tf else {}
    rows = []
    for lev in (5, 10, 15, 20):
        s = Settings()
        s.risk = copy.deepcopy(s.risk)
        s.risk.leverage_tiers = str(lev)
        res = run(data, s, BacktestConfig(start_equity=540.0, params=params))
        m = res.summary()["metrics"]
        liq = sum(1 for t in res.trades if t.exit_reason == "liquidation")
        rows.append({"leverage": lev, "trades": m.get("trades"), "net_profit": m.get("net_profit"),
                     "profit_factor": m.get("profit_factor"), "expectancy_r": m.get("expectancy_r"),
                     "max_drawdown": m.get("max_drawdown_mtm"), "win_rate": m.get("win_rate"),
                     "sharpe": m.get("sharpe"), "liquidations": liq})
        print(rows[-1], flush=True)
    out = {"period": [a.start, a.end], "timeframe": a.tf, "symbols": SYMBOLS, "rows": rows}
    with open(a.out, "w") as fh:
        json.dump(metrics.json_safe(out), fh, default=str, indent=1)
    lines = [f"Leverage comparison {a.start}..{a.end}, timeframe {a.tf or 'bot default'}, 540 USDT, 0.5 % risk",
             "| leverage | trades | net profit | PF | exp R | max DD | win rate | Sharpe | liquidations |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['leverage']}x | {r['trades']} | {r['net_profit']} | {r['profit_factor']} | "
                     f"{r['expectancy_r']} | {r['max_drawdown']} | {r['win_rate']} | {r['sharpe']} | {r['liquidations']} |")
    open(a.out.replace(".json", ".md"), "w").write("\n".join(lines))
    print("\n".join(lines))


SYMBOLS_10 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT",
              "AVAXUSDT", "LTCUSDT"]


def cmd_forensic(a) -> None:
    """Phase 1 of 2.0: per-strategy forensics on real data, holdout untouched."""
    from . import forensic
    s = Settings()
    syms = a.symbols.split(",") if a.symbols else SYMBOLS_10
    global SYMBOLS
    SYMBOLS = syms
    data = _load(*SELECT, a.data_dir)
    rep = {"period": [str(x) for x in SELECT], "holdout": [str(x) for x in HOLDOUT], "symbols": syms, "bots": {}}
    by_bot_default = {}
    for cls in BASE_CLASSES:
        tfs = []
        for tf in (int(cls.default_params.get("timeframe", 5)), 15, 60):
            if tf not in tfs:
                tfs.append(tf)
        rep["bots"][cls.bot_id] = {}
        for tf in tfs:
            t = time.time()
            warm = max(3, math.ceil(tf * getattr(cls, "warmup_bars", 60) / 1440) + 1)
            t0 = min(int(b.open_time[0]) for b in data.values()) + warm * DAY_MS
            trs = _virtual(data, s, cls.bot_id, {"timeframe": tf}, t0)
            rep["bots"][cls.bot_id][f"{tf}m"] = forensic.analyse(trs, data)
            if tf == tfs[0]:
                by_bot_default[cls.bot_id] = trs
            o = rep["bots"][cls.bot_id][f"{tf}m"]["overall"]
            print(f"{cls.bot_id} {tf}m n={o.get('n')} expR={o.get('exp_r')} gross={o.get('gross_exp_r')} "
                  f"({time.time() - t:.0f}s)", flush=True)
    rep["overlap"] = forensic.overlap(by_bot_default)
    with open(a.out, "w") as fh:
        json.dump(metrics.json_safe(rep), fh, default=str)
    open(a.out.replace(".json", ".md"), "w").write(forensic.to_markdown(rep))
    print(forensic.to_markdown(rep)[:3000])


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wf")
    w.add_argument("--tf", type=int, required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--data-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    w.add_argument("--max-combos", type=int, default=12)
    h = sub.add_parser("holdout")
    h.add_argument("--inputs", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--data-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    lv = sub.add_parser("leverage")
    lv.add_argument("--start", default="2025-07-01")
    lv.add_argument("--end", default="2026-10-31")
    lv.add_argument("--tf", type=int, default=0)
    lv.add_argument("--out", required=True)
    lv.add_argument("--data-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    fo = sub.add_parser("forensic")
    fo.add_argument("--out", required=True)
    fo.add_argument("--symbols", default="")
    fo.add_argument("--data-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    a = p.parse_args()
    {"wf": cmd_wf, "holdout": cmd_holdout, "leverage": cmd_leverage, "forensic": cmd_forensic}[a.cmd](a)


if __name__ == "__main__":
    main()
