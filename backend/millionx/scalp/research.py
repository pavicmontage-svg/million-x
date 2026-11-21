"""Event-driven 1-second scalping research (2.2, §10–§11).

STEP 1 (CI, one job per instrument):  python -m millionx.scalp.research run --symbol BTCUSDT --out sc_BTCUSDT.npz
  Streams every aggTrades day of 2024-12-31 … 2026-06-30 (the holdout is refused), builds 1 s bars, computes the
  shared features, runs engines A and B for every pre-registered config at decision timeframes 1 s / 1m / 3m / 5m,
  and simulates each candidate second by second under BASE, STRESSED (+ outages), EXTREME and MAKER-entry costs.
  Each candidate stores the feature snapshot the bot saw at the decision second.

STEP 2 (CI):  python -m millionx.scalp.research aggregate --inputs "sc_*.npz" --out scalp_research.json
  * calibration and config choice on DISCOVERY (2025-01-01 … 2025-09-30) only;
  * the Opportunity Engine filter (expected net R > 0 after costs, >= 50 similar past setups) applied out of sample;
  * VALIDATION 2025-10-01 … 2026-06-30, plus walk-forward by quarter with an expanding calibration window;
  * portfolio: 500 EUR, 0.5 % risk (2.50 EUR), max 2 positions, 10 EUR daily loss, ISOLATED, leverage 5/10/15/20x
    (a position whose margin does not fit is skipped — this is where leverage matters for tight scalping stops);
  * money first (owner): ranking and acceptance by expected net R / EUR; win rate is reported only.
Engine C needs a real order book at 1 s and is evaluated only on recorded live data (paper), not here.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from . import engines as E
from . import exits as X
from . import features as F
from . import opportunity as O

DAY = 86_400_000
START, END = date(2024, 12, 31), date(2026, 6, 30)
HOLDOUT = date(2026, 7, 1)
DISC = ("2025-01-01", "2025-10-01")
VALID = ("2025-10-01", "2026-07-01")
QUARTERS = [("2025-10-01", "2026-01-01"), ("2026-01-01", "2026-04-01"), ("2026-04-01", "2026-07-01")]
CTX = 14_400          # seconds of previous-day context for features
TFS = (1, 60, 180, 300)
SNAP = ("v30z", "relvol30", "taker30", "cvd_acc", "break_up", "break_dn", "ext_up", "ext_dn", "trend5", "mom3m",
        "ret15m", "sigma300")
SCN = ("BASE", "STRESSED", "EXTREME", "MAKER")
RISK_EUR, CAPITAL_EUR, DAILY_LOSS_EUR, MAX_POS = 2.5, 500.0, 10.0, 2
MMR = 0.004


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def outages_for(day: date, seconds: int, seed: int) -> np.ndarray:
    """Deterministic simulated exchange outages: on ~1 in 3 days one 2–5 minute window."""
    rng = np.random.default_rng(seed * 100_003 + day.toordinal())
    m = np.zeros(seconds, bool)
    if rng.random() < 1 / 3:
        a = int(rng.integers(0, seconds - 400))
        m[a:a + int(rng.integers(120, 300))] = True
    return m


def candidates_for_day(sec: F.Sec, f: dict[str, np.ndarray], lo_idx: int, hi_idx: int) -> list[dict[str, Any]]:
    out = []
    sbar = (sec.t // 1000) + 1
    for eng, fn in (("A", E.engine_a), ("B", E.engine_b)):
        for ci, p in enumerate(E.GRID[eng]):
            cands = fn(f, sec.c, p)
            for tf in TFS:
                last = -10**9
                for i, side, stop, score in sorted(cands):
                    if not (lo_idx <= i < hi_idx) or (tf > 1 and sbar[i] % tf != 0):
                        continue  # tf > 1: decisions only at the close of a tf bar
                    if i - last < max(E.COOLDOWN_S, tf):
                        continue
                    last = i
                    out.append({"engine": eng, "cfg": ci, "tf": tf, "i": i, "side": side, "stop_bps": stop,
                                "rr": p["rr"], "score": score})
    return out


def run_symbol(sym: str, out: str, start: date = START, end: date = END, log=print, max_days: Optional[int] = None,
               loader=None) -> int:
    if end >= HOLDOUT:
        raise ValueError("the final holdout (from 2026-07-01) is not used")
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    if max_days:
        days = days[:max_days]
    seed = sum(map(ord, sym))
    cols: dict[str, list] = {k: [] for k in ("t", "engine", "cfg", "tf", "side", "stop_bps", "rr", "score", "regime",
                                             "price", "maker_share")}
    for k in SNAP:
        cols[f"f_{k}"] = []
    for s in SCN:
        for k in ("gross", "net", "r", "hold", "mfe", "mae", "reason"):
            cols[f"{s}_{k}"] = []
    prev: Optional[F.Sec] = None
    pending: Optional[tuple[date, F.Sec]] = None
    n_days = 0

    load = loader or archive_loader(sym)

    def process(day: date, cur: F.Sec, nxt: Optional[F.Sec]) -> None:
        parts = ([prev.slice(len(prev) - CTX, len(prev))] if prev is not None else []) + [cur] + \
                ([nxt.slice(0, X.MAX_HOLD_WINNER_S + 60)] if nxt is not None else [])
        s = F.Sec.concat(parts)
        f = F.compute(s)
        a = CTX if prev is not None else 0
        b = a + len(cur)
        out_mask = np.concatenate([outages_for(day - timedelta(days=1), 86_400, seed)[-CTX:] if prev is not None else np.zeros(0, bool),
                                   outages_for(day, 86_400, seed)[:len(cur)],
                                   outages_for(day + timedelta(days=1), 86_400, seed)[:len(s) - b]])
        for cd in candidates_for_day(s, f, a + 14_400 if prev is None else a, b):
            i = cd["i"]
            res = {}
            for sc in ("BASE", "STRESSED", "EXTREME"):
                res[sc] = X.simulate(i, cd["side"], cd["stop_bps"], cd["rr"], s.c, s.h, s.l, f["v30z"], f["cvd10"],
                                     X.SCENARIOS[sc], out_mask if sc != "BASE" else None)
            qty = (RISK_EUR * 1.08 / (cd["stop_bps"] / 1e4)) / s.c[i]
            mf = X.maker_fill(i, cd["side"], s.c, s.h, s.l, s.buy, s.sell, qty)
            res["MAKER"] = X.simulate(i, cd["side"], cd["stop_bps"], cd["rr"], s.c, s.h, s.l, f["v30z"], f["cvd10"],
                                      X.SCENARIOS["BASE"], None, maker_fill_s=mf[0]) if mf else None
            if res["BASE"] is None:
                continue
            cols["t"].append(int(s.t[i]))
            for k in ("engine", "cfg", "tf", "side", "stop_bps", "rr", "score"):
                cols[k].append(cd[k])
            cols["regime"].append(str(f["regime"][i]))
            cols["price"].append(float(s.c[i]))
            cols["maker_share"].append(float(mf[1]) if mf else 0.0)
            for k in SNAP:
                cols[f"f_{k}"].append(float(f[k][i]) if np.isfinite(f[k][i]) else np.nan)
            for sc in SCN:
                r = res[sc]
                for k, rk in (("gross", "gross_bps"), ("net", "net_bps"), ("r", "r"), ("hold", "hold_s"),
                              ("mfe", "mfe_bps"), ("mae", "mae_bps"), ("reason", "reason")):
                    cols[f"{sc}_{k}"].append(r[rk] if r else (np.nan if k != "reason" else "no_fill" if sc == "MAKER" else "none"))

    t0 = time.time()
    for day in days:
        cur = load(day, float(pending[1].c[-1]) if pending else (float(prev.c[-1]) if prev is not None else np.nan))
        if cur is None:
            log(f"{sym} {day}: no aggTrades (gap recorded)")
            if pending:
                process(pending[0], pending[1], None)
                prev, pending = pending[1], None
            continue
        if pending:
            process(pending[0], pending[1], cur)
            prev = pending[1]
            n_days += 1
        pending = (day, cur)
        if n_days and n_days % 30 == 0:
            log(f"{sym}: {n_days} days, {len(cols['t'])} candidates, {time.time() - t0:.0f}s")
    if pending:
        process(pending[0], pending[1], None)
        n_days += 1
    arr = {k: np.array(v, dtype=object if k in ("engine", "regime") or k.endswith("_reason") else None) for k, v in cols.items()}
    arr = {k: (v.astype(str) if v.dtype == object else v) for k, v in arr.items()}
    np.savez_compressed(out, symbol=np.array(sym), days=np.array(n_days), **arr)
    log(f"{sym}: done, {n_days} days, {len(cols['t'])} candidates")
    return len(cols["t"])


def archive_loader(sym: str):
    """aggTrades day -> 1 s bars, from the public Binance archive."""
    import requests
    from ..micro.archive import BASE as ARCH, _csv, _get, _ms as ams
    sess = requests.Session()

    def load(day: date, prev_close: float) -> Optional[F.Sec]:
        c = _get(sess, f"{ARCH}/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip")
        if c is None:
            return None
        df = _csv(c)
        if not isinstance(df.columns[0], str):
            df.columns = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time",
                          "is_buyer_maker"][:df.shape[1]]
        d0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
        return F.from_trades(d0, ams(df["transact_time"]), df["price"].astype(float).to_numpy(),
                             df["quantity"].astype(float).to_numpy(),
                             df["is_buyer_maker"].astype(str).str.lower().eq("true").to_numpy(), prev_close)
    return load


# ------------------------------------------------------------------------------------------- aggregation
def _load(pattern: str) -> dict[str, np.ndarray]:
    parts = []
    for p in sorted(glob.glob(pattern)):
        with np.load(p, allow_pickle=False) as z:
            n = len(z["t"])
            d = {k: z[k] for k in z.files if k not in ("symbol", "days")}
            d["symbol"] = np.full(n, str(z["symbol"]))
            parts.append(d)
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]} if parts else {}


def _stats(r: np.ndarray, net_bps: np.ndarray, gross_bps: np.ndarray, hold: np.ndarray, hours: float) -> dict[str, Any]:
    if len(r) == 0:
        return {"n": 0}
    w, l_ = r[r > 0], r[r <= 0]
    eq = np.concatenate([[0.0], np.cumsum(r * RISK_EUR)])
    dd = float((np.maximum.accumulate(eq) - eq).max())
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    fees = gross_bps - net_bps
    pos_gross = gross_bps[gross_bps > 0].sum()
    from ..backtest import stats2
    boot = stats2.block_bootstrap(r, risk_fraction=0.005) if len(r) >= 30 else {}
    return {"n": int(len(r)), "net_r": float(r.mean()), "net_eur_total": float(r.sum() * RISK_EUR),
            "pf": float(w.sum() / -l_.sum()) if len(l_) and l_.sum() < 0 else None, "win_rate": float((r > 0).mean()),
            "avg_win_r": float(w.mean()) if len(w) else None, "avg_loss_r": float(l_.mean()) if len(l_) else None,
            "max_dd_eur": dd, "max_consecutive_losses": int(streak), "avg_hold_s": float(hold.mean()),
            "net_eur_per_hour_active": float(r.sum() * RISK_EUR / max(1e-9, hold.sum() / 3600)),
            "net_eur_per_day": float(r.sum() * RISK_EUR / max(1e-9, hours / 24)),
            "fee_to_gross_profit": float(fees.sum() / pos_gross) if pos_gross > 0 else None,
            "ci95_net_r": boot.get("exp_r_ci95"), "p_net_le_0": boot.get("p_exp_le_0")}


def _rows(D, idx, sc="BASE"):
    return [{"engine": D["engine"][i], "cfg": int(D["cfg"][i]), "score": float(D["score"][i]), "regime": D["regime"][i],
             "gross_bps": float(D[f"{sc}_gross"][i]), "hold_s": float(D[f"{sc}_hold"][i]), "mfe_bps": float(D[f"{sc}_mfe"][i]),
             "stop_bps": float(D["stop_bps"][i])} for i in idx]


def portfolio(D: dict[str, np.ndarray], idx: np.ndarray, sc: str, lev: float) -> dict[str, Any]:
    """Chronological portfolio: max 2 open, 10 EUR daily loss stop, margin must fit (ISOLATED, liq buffer 3x stop)."""
    order = idx[np.argsort(D["t"][idx], kind="stable")]
    open_pos: list[tuple[int, float]] = []   # (exit time ms, margin EUR)
    day_loss: dict[int, float] = {}
    taken, skipped = [], {"max_positions": 0, "margin": 0, "daily_loss": 0, "liquidation_buffer": 0}
    for i in order:
        t = int(D["t"][i])
        open_pos = [p for p in open_pos if p[0] > t]
        dkey = t // DAY
        if day_loss.get(dkey, 0.0) >= DAILY_LOSS_EUR:
            skipped["daily_loss"] += 1
            continue
        if len(open_pos) >= MAX_POS:
            skipped["max_positions"] += 1
            continue
        stop = D["stop_bps"][i] / 1e4
        if (1 / lev - MMR) < 3 * stop:
            skipped["liquidation_buffer"] += 1
            continue
        notional = RISK_EUR / stop
        margin = notional / lev
        if margin > CAPITAL_EUR - sum(p[1] for p in open_pos):
            skipped["margin"] += 1
            continue
        r = float(D[f"{sc}_r"][i])
        if not np.isfinite(r):
            continue
        open_pos.append((t + int(D[f"{sc}_hold"][i]) * 1000, margin))
        if r < 0:
            day_loss[dkey] = day_loss.get(dkey, 0.0) - r * RISK_EUR
        taken.append(i)
    taken = np.array(taken, dtype=np.int64)
    if len(taken) == 0:
        return {"n": 0, "skipped": skipped}
    hours = (D["t"][taken].max() - D["t"][taken].min()) / 3.6e6
    st = _stats(D[f"{sc}_r"][taken], D[f"{sc}_net"][taken], D[f"{sc}_gross"][taken], D[f"{sc}_hold"][taken], max(hours, 1))
    st["skipped"] = skipped
    st["trades_per_day"] = float(len(taken) / max(1.0, hours / 24))
    return st


def aggregate(pattern: str, log=print) -> dict[str, Any]:
    D = _load(pattern)
    if not D:
        raise SystemExit("no inputs")
    t = D["t"]
    d0, d1, v0, v1 = _ms(DISC[0]), _ms(DISC[1]), _ms(VALID[0]), _ms(VALID[1])
    disc = (t >= d0) & (t < d1)
    val = (t >= v0) & (t < v1)
    vhours = (v1 - v0) / 3.6e6
    out: dict[str, Any] = {"protocol": __doc__, "n_candidates": int(len(t)), "symbols": sorted(set(D["symbol"])),
                           "raw": {}, "chosen": {}, "validation": {}, "walk_forward": {}, "portfolio": {}}
    # 1) raw (unfiltered) candidates: every engine x cfg x tf, discovery and validation, BASE costs
    for eng in ("A", "B"):
        for cfg in range(len(E.GRID[eng])):
            for tf in TFS:
                m = (D["engine"] == eng) & (D["cfg"] == cfg) & (D["tf"] == tf)
                key = f"{eng}{cfg}@{tf}s"
                out["raw"][key] = {}
                for name, mm in (("discovery", m & disc), ("validation", m & val)):
                    ix = np.flatnonzero(mm)
                    out["raw"][key][name] = {sc: _stats(D[f"{sc}_r"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                        D[f"{sc}_net"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                        D[f"{sc}_gross"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                        D[f"{sc}_hold"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                        vhours if name == "validation" else (d1 - d0) / 3.6e6)
                                             for sc in SCN}
                    out["raw"][key][name]["gross_r"] = float(np.nanmean(D["BASE_gross"][ix] / D["stop_bps"][ix])) if len(ix) else None
    # 2) per engine: choose cfg x tf on DISCOVERY by expected net R (BASE); never look at validation
    for eng in ("A", "B"):
        best = max(((k, v["discovery"]["BASE"].get("net_r", -9)) for k, v in out["raw"].items()
                    if k.startswith(eng) and v["discovery"]["BASE"].get("n", 0) >= 100), key=lambda x: x[1], default=None)
        out["chosen"][eng] = best[0] if best else None
    # 3) Opportunity Engine filter calibrated on discovery, applied out of sample
    def accepted(mask_eval: np.ndarray, mask_cal: np.ndarray, eng_keys: dict[str, Optional[str]], sc: str = "BASE") -> np.ndarray:
        acc = []
        for eng, key in eng_keys.items():
            if not key:
                continue
            cfg, tf = int(key[1]), int(key.split("@")[1][:-1])
            sel = (D["engine"] == eng) & (D["cfg"] == cfg) & (D["tf"] == tf)
            cal = O.build_calibration(_rows(D, np.flatnonzero(sel & mask_cal)))
            for i in np.flatnonzero(sel & mask_eval):
                ev = O.rank({"engine": eng, "cfg": cfg, "side": int(D["side"][i]), "score": float(D["score"][i]),
                             "regime": D["regime"][i], "stop_bps": float(D["stop_bps"][i]), "rr": float(D["rr"][i])},
                            cal, X.SCENARIOS["BASE"], validated=True)
                if ev["decision"] == "ACCEPT":
                    acc.append(i)
        return np.array(sorted(acc), dtype=np.int64)

    for combo in (("A",), ("B",), ("A", "B")):
        name = "+".join(combo)
        keys = {e: out["chosen"][e] for e in combo}
        ix = accepted(val, disc, keys)
        out["validation"][name] = {"accepted": int(len(ix)),
                                   "scenarios": {sc: _stats(D[f"{sc}_r"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                            D[f"{sc}_net"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                            D[f"{sc}_gross"][ix][np.isfinite(D[f"{sc}_r"][ix])],
                                                            D[f"{sc}_hold"][ix][np.isfinite(D[f"{sc}_r"][ix])], vhours)
                                                 for sc in SCN}}
        out["portfolio"][name] = {f"{lev}x": portfolio(D, ix, "BASE", lev) for lev in (5, 10, 15, 20)}
        out["portfolio"][name]["STRESSED@20x"] = portfolio(D, ix, "STRESSED", 20)
        # walk-forward: expanding calibration (discovery + earlier validation quarters)
        wf = []
        for qa, qb in QUARTERS:
            a, b = _ms(qa), _ms(qb)
            cal_mask = (t >= d0) & (t < a)
            ixq = accepted((t >= a) & (t < b), cal_mask, keys)
            rq = D["BASE_r"][ixq]
            wf.append({"quarter": qa, "accepted": int(len(ixq)), "net_r": float(np.nanmean(rq)) if len(ixq) else None,
                       "net_eur": float(np.nansum(rq) * RISK_EUR)})
        out["walk_forward"][name] = wf
        log(f"{name}: accepted {len(ix)} on validation")
    # 4) verdict: existing activation minimums (v2.0) — never lowered
    verdict = {}
    for name, v in out["validation"].items():
        b = v["scenarios"]["BASE"]
        s_ = v["scenarios"]["STRESSED"]
        ok = (b.get("n", 0) >= 100 and (b.get("pf") or 0) >= 1.15 and (b.get("net_r") or -9) >= 0.05
              and (b.get("ci95_net_r") or [-1])[0] > 0 and (s_.get("net_r") or -9) > 0
              and sum(1 for q in out["walk_forward"][name] if (q["net_r"] or -1) > 0) >= 2)
        verdict[name] = "CANDIDATE_FOR_PAPER" if ok else "NO_TRADE"
    out["verdict"] = verdict
    # packaged for the live scanner: chosen configs, verdicts and the DISCOVERY calibration of the chosen configs
    calib = {"edges": {}, "cells": {}}
    for eng, key in out["chosen"].items():
        if not key:
            continue
        cfg, tf = int(key[1]), int(key.split("@")[1][:-1])
        sel = (D["engine"] == eng) & (D["cfg"] == cfg) & (D["tf"] == tf) & disc
        c = O.build_calibration(_rows(D, np.flatnonzero(sel)))
        calib["edges"].update(c["edges"])
        calib["cells"].update(c["cells"])
    out["config"] = {"chosen": out["chosen"], "verdict": verdict, "calibration": calib}
    return out


def to_markdown(o: dict[str, Any]) -> str:
    f = lambda x, nd=3: "—" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))  # noqa: E731
    L = ["# Scalping research 2.2 (event-driven, 1 s, aggTrades)", "",
         f"Candidates: {o['n_candidates']} on {len(o['symbols'])} instruments. Chosen on discovery: {o['chosen']}.",
         f"**Verdict: {o['verdict']}**", "", "## Validation (Opportunity Engine filter, calibrated on discovery)", "",
         "| engines | scenario | n | net R | net EUR | PF | WR | avg win R | avg loss R | max DD EUR | max loss streak | hold s | fee/gross | CI net R |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in o["validation"].items():
        for sc, s in v["scenarios"].items():
            L.append(f"| {name} | {sc} | {s.get('n', 0)} | {f(s.get('net_r'))} | {f(s.get('net_eur_total'), 1)} | {f(s.get('pf'), 2)} | "
                     f"{f(s.get('win_rate'), 2)} | {f(s.get('avg_win_r'), 2)} | {f(s.get('avg_loss_r'), 2)} | {f(s.get('max_dd_eur'), 1)} | "
                     f"{s.get('max_consecutive_losses', '—')} | {f(s.get('avg_hold_s'), 0)} | {f(s.get('fee_to_gross_profit'), 2)} | {s.get('ci95_net_r')} |")
    L += ["", "## Raw candidates (no filter), BASE costs: discovery → validation net R (gross R)", "",
          "| engine cfg @ tf | disc n | disc net R | val n | val net R | val gross R | val net EUR/day | val MAKER net R |", "|---|---|---|---|---|---|---|---|"]
    for k, v in o["raw"].items():
        d, va = v["discovery"]["BASE"], v["validation"]["BASE"]
        L.append(f"| {k} | {d.get('n', 0)} | {f(d.get('net_r'))} | {va.get('n', 0)} | {f(va.get('net_r'))} | {f(v['validation'].get('gross_r'))} | "
                 f"{f(va.get('net_eur_per_day'), 2)} | {f(v['validation']['MAKER'].get('net_r'))} |")
    L += ["", "## Portfolio (500 EUR, 2.50 EUR risk, 2 positions, 10 EUR/day)", ""]
    for name, p in o["portfolio"].items():
        for lev, s in p.items():
            L.append(f"- {name} {lev}: n={s.get('n', 0)}, net EUR={f(s.get('net_eur_total'), 1)}, trades/day={f(s.get('trades_per_day'), 2)}, "
                     f"max DD EUR={f(s.get('max_dd_eur'), 1)}, skipped={s.get('skipped')}")
    L += ["", "## Walk-forward (expanding calibration)", ""]
    for name, w in o["walk_forward"].items():
        L.append(f"- {name}: " + "; ".join(f"{q['quarter']}: n={q['accepted']} netR={f(q['net_r'])} EUR={f(q['net_eur'], 1)}" for q in w))
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--symbol", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--max-days", type=int, default=0)
    g = sub.add_parser("aggregate")
    g.add_argument("--inputs", required=True)
    g.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "run":
        run_symbol(a.symbol, a.out, max_days=a.max_days or None)
    else:
        from ..backtest.metrics import json_safe
        o = aggregate(a.inputs)
        json.dump(json_safe(o), open(a.out, "w"), default=str)
        json.dump(json_safe(o["config"]), open(os.path.join(os.path.dirname(a.out) or ".", "scalp_config.json"), "w"), default=str)
        open(a.out.replace(".json", ".md"), "w").write(to_markdown(o))
        print(to_markdown(o))


if __name__ == "__main__":
    main()
