"""Massive strategy search (MillionX 2.4) with multiple-testing control.

Every rule = AND of 2 or 3 primitive conditions from a fixed library (~90 primitives built from price,
volume, CVD, large trades, open interest, funding/premium, order-book depth, volatility, VWAP, time of day),
traded LONG or SHORT for a fixed horizon h ∈ {1, 5, 15, 60} minutes, entry at the decision bar's close,
non-overlapping trades. Costs per round trip: TAKER 14 bps, MAKER 6 bps (maker is optimistic: it assumes
fills — anything that only works with maker costs must still be confirmed by a fill-aware simulation).

STEP 1 (CI, per instrument):  python -m millionx.mine.search run --stem /tmp/md/BTCUSDT --out mine_BTCUSDT.npz
  stores, for every rule x horizon, the count / sum / sum of squares of forward returns (bps) separately for
  DISCOVERY (2025-01 … 2025-09) and VALIDATION (2025-10 … 2026-06).
STEP 2 (CI):  python -m millionx.mine.search aggregate --inputs "mine_*.npz" --out mine.json
  * direction per rule x horizon from the DISCOVERY mean (pooled over instruments);
  * top candidates chosen on DISCOVERY only;
  * VALIDATION judged with Bonferroni / Benjamini–Hochberg over ALL variants searched (rules x horizons x
    directions x cost scenarios), plus per-instrument consistency (>= 6/10 positive) and positive net in each
    validation quarter.
The final holdout (2026-07 … 2026-10) is not ingested; survivors are frozen first and tested on it once later.
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import os
from typing import Any, Callable

import numpy as np

DISC = ("2025-01-01", "2025-10-01")
VALID = ("2025-10-01", "2026-07-01")
QUARTERS = [("2025-10-01", "2026-01-01"), ("2026-01-01", "2026-04-01"), ("2026-04-01", "2026-07-01")]
HORIZONS = (1, 5, 15, 60)
COSTS = {"TAKER": 14.0, "MAKER": 6.0}
PCT_WIN = 30 * 1440


def _ms(s: str) -> int:
    return int(np.datetime64(s).astype("datetime64[ms]").astype(np.int64))


def _rsum(x, n):
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))])
    out = np.full(len(x), np.nan)
    out[n - 1:] = c[n:] - c[:-n]
    return out


def _chg(x, n):
    out = np.full(len(x), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n:] = x[n:] / x[:-n] - 1
    return out


def _pct(x: np.ndarray, sample: int = 5) -> np.ndarray:
    """Causal rolling percentile (30 days), computed every `sample` minutes and held (causal)."""
    from ..regime2 import rolling_percentile
    idx = np.arange(0, len(x), sample)
    p = rolling_percentile(np.asarray(x, float)[idx], PCT_WIN // sample, min_count=500)
    return p[np.arange(len(x)) // sample]


def features(c: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    close = c["close"].astype(float)
    vol = c["volume"].astype(float)
    lr = np.log(close)
    F: dict[str, np.ndarray] = {}
    for n in (1, 5, 15, 60, 240):
        r = np.full(len(close), np.nan)
        r[n:] = lr[n:] - lr[:-n]
        F[f"ret{n}"] = r
    rng = (c["high"] - c["low"]) / close
    F["range15"] = _rsum(rng, 15)
    F["relvol15"] = _rsum(vol, 15) / np.maximum(1e-12, _rsum(vol, 1440) / 96)
    delta = 2 * c["taker_buy_volume"].astype(float) - vol
    F["cvd15"] = _rsum(delta, 15) / np.maximum(1e-12, _rsum(vol, 15))
    F["cvd60"] = _rsum(delta, 60) / np.maximum(1e-12, _rsum(vol, 60))
    if "agg_large_buy" in c:
        ld = np.nan_to_num(c["agg_large_buy"].astype(float)) - np.nan_to_num(c["agg_large_sell"].astype(float))
        F["large15"] = _rsum(ld, 15) / np.maximum(1e-12, _rsum(vol, 15))
    pv = _rsum(close * vol, 1440)
    F["vwapdev"] = close / (pv / np.maximum(1e-12, _rsum(vol, 1440))) - 1
    if "oi" in c:
        oi = c["oi"].astype(float)
        oi[~(oi > 0)] = np.nan
        F["oi15"] = _chg(oi, 15)
        F["oi60"] = _chg(oi, 60)
        F["oi240"] = _chg(oi, 240)
    for k in ("premium", "top_ls_pos", "taker_ls_vol", "ls_accounts"):
        if k in c:
            F[k] = c[k].astype(float)
    if "mark" in c:
        F["basis"] = c["mark"].astype(float) / close - 1
    if "depth_m1" in c:
        b, a = c["depth_m1"].astype(float), c["depth_p1"].astype(float)
        F["imb1"] = (b - a) / (b + a)
        F["depth1"] = b + a
    if "depth_m0_2" in c:
        b, a = c["depth_m0_2"].astype(float), c["depth_p0_2"].astype(float)
        F["imb02"] = (b - a) / (b + a)
    return F


def primitives(c: dict[str, np.ndarray], log: Callable = print) -> tuple[list[str], np.ndarray]:
    """Library of boolean conditions (all causal). Percentile thresholds 10/25/75/90 % of the last 30 days."""
    F = features(c)
    names, masks = [], []
    for k, x in F.items():
        p = _pct(x)
        for lab, m in (("<=p10", p <= 0.10), ("<=p25", p <= 0.25), (">=p75", p >= 0.75), (">=p90", p >= 0.90)):
            names.append(f"{k}{lab}")
            masks.append(np.nan_to_num(m, nan=0).astype(bool))
        log(f"  feature {k}")
    hour = (c["open_time"] // 3_600_000) % 24
    for h0 in range(0, 24, 4):
        names.append(f"utc{h0:02d}-{h0 + 4:02d}")
        masks.append((hour >= h0) & (hour < h0 + 4))
    dow = ((c["open_time"] // 86_400_000) + 3) % 7  # 0 = Monday
    names.append("weekend")
    masks.append(dow >= 5)
    return names, np.vstack(masks)


def rule_list(names: list[str]) -> list[tuple[int, ...]]:
    """All pairs and triples of primitives from DIFFERENT features (deterministic order)."""
    feat = [n.split("<=")[0].split(">=")[0] if ("<=" in n or ">=" in n) else n.split("-")[0] for n in names]
    feat = ["time" if f.startswith("utc") else f for f in feat]
    idx = range(len(names))
    rules = [r for r in itertools.combinations(idx, 2) if feat[r[0]] != feat[r[1]]]
    rules += [r for r in itertools.combinations(idx, 3) if len({feat[i] for i in r}) == 3]
    return rules


def _nonoverlap(idx: np.ndarray, h: int) -> np.ndarray:
    if len(idx) == 0:
        return idx
    first = idx[np.r_[True, (idx[1:] // h) != (idx[:-1] // h)]]   # first entry in each h-block
    return first[np.r_[True, np.diff(first) >= h]]                 # then enforce spacing >= h (only grows gaps)


def run_symbol(stem: str, out: str, log: Callable = print, max_rules: int = 0) -> int:
    from ..micro import archive
    c = archive.load(stem)
    t = c["open_time"].astype(np.int64)
    close = c["close"].astype(float)
    names, P = primitives(c, log)
    rules = rule_list(names)
    if max_rules:
        rules = rules[:max_rules]
    fwd = {}
    for h in HORIZONS:
        r = np.full(len(close), np.nan)
        r[:-h] = (close[h:] / close[:-h] - 1) * 1e4
        fwd[h] = r
    periods = {"disc": (_ms(DISC[0]), _ms(DISC[1])), "q1": (_ms(QUARTERS[0][0]), _ms(QUARTERS[0][1])),
               "q2": (_ms(QUARTERS[1][0]), _ms(QUARTERS[1][1])), "q3": (_ms(QUARTERS[2][0]), _ms(QUARTERS[2][1]))}
    pmask = {k: (t >= a) & (t < b) & np.isfinite(close) for k, (a, b) in periods.items()}
    R, H, K = len(rules), len(HORIZONS), len(periods)
    N = np.zeros((R, H, K), np.int32)
    S = np.zeros((R, H, K), np.float32)
    Q = np.zeros((R, H, K), np.float32)
    keys = list(periods)
    valid_any = np.zeros(len(t), bool)
    for v in pmask.values():
        valid_any |= v
    for ri, rule in enumerate(rules):
        m = P[rule[0]] & P[rule[1]]
        if len(rule) == 3:
            m &= P[rule[2]]
        m &= valid_any
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            continue
        for hi, h in enumerate(HORIZONS):
            e = _nonoverlap(idx, h)
            r = fwd[h][e]
            ok = np.isfinite(r)
            e, r = e[ok], r[ok]
            for ki, k in enumerate(keys):
                sel = pmask[k][e]
                rr = r[sel]
                N[ri, hi, ki] = len(rr)
                S[ri, hi, ki] = rr.sum()
                Q[ri, hi, ki] = (rr * rr).sum()
        if ri % 20000 == 0:
            log(f"  rules {ri}/{R}")
    np.savez_compressed(out, N=N, S=S, Q=Q, names=np.array(names), rules=np.array([r + (-1,) * (3 - len(r)) for r in rules]),
                        horizons=np.array(HORIZONS), periods=np.array(keys), symbol=np.array(os.path.basename(stem)))
    log(f"{os.path.basename(stem)}: {R} rules x {H} horizons")
    return R


def aggregate(pattern: str, top_k: int = 200, log: Callable = print) -> dict[str, Any]:
    files = sorted(glob.glob(pattern))
    Z = [np.load(f, allow_pickle=False) for f in files]
    names = [str(x) for x in Z[0]["names"]]
    rules = Z[0]["rules"]
    for z in Z[1:]:
        assert list(map(str, z["names"])) == names and np.array_equal(z["rules"], rules), "rule sets differ between instruments"
    syms = [str(z["symbol"]) for z in Z]
    N = np.stack([z["N"] for z in Z]).astype(np.float64)   # (sym, R, H, K)
    S = np.stack([z["S"] for z in Z]).astype(np.float64)
    Qs = np.stack([z["Q"] for z in Z]).astype(np.float64)
    k = {p: i for i, p in enumerate(map(str, Z[0]["periods"]))}
    R, H = N.shape[1], N.shape[2]
    n_variants = R * H * 2 * len(COSTS)   # rules x horizons x directions x cost scenarios
    # discovery (pooled): direction and t-statistic of the NET mean
    nd, sd, qd = N[:, :, :, k["disc"]].sum(0), S[:, :, :, k["disc"]].sum(0), Qs[:, :, :, k["disc"]].sum(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        md = sd / nd
        vd = qd / nd - md ** 2
    direction = np.where(md >= 0, 1.0, -1.0)
    out: dict[str, Any] = {"protocol": __doc__, "symbols": syms, "n_rules": int(R), "n_variants_searched": int(n_variants),
                           "costs_bps": COSTS, "scenarios": {}}
    for cname, cost in COSTS.items():
        net_d = direction * md - cost
        se_d = np.sqrt(np.maximum(vd, 1e-12) / np.maximum(nd, 1))
        t_d = np.where(nd >= 300, net_d / se_d, -np.inf)
        flat = np.argsort(t_d, axis=None)[::-1][:top_k]
        cands = []
        for f_ in flat:
            ri, hi = np.unravel_index(f_, t_d.shape)
            if not np.isfinite(t_d[ri, hi]):
                continue
            dirn = direction[ri, hi]
            vk = [k["q1"], k["q2"], k["q3"]]
            nv = N[:, ri, hi, vk].sum()
            sv = (dirn * S[:, ri, hi, vk]).sum()
            qv = Qs[:, ri, hi, vk].sum()
            mv = sv / nv if nv else float("nan")
            sdv = math.sqrt(max(qv / nv - mv ** 2, 1e-12)) if nv else float("nan")
            net_v = mv - cost
            z = net_v / (sdv / math.sqrt(nv)) if nv else float("nan")
            p_one = 0.5 * math.erfc(z / math.sqrt(2)) if np.isfinite(z) else 1.0
            per_sym = []
            for si in range(len(syms)):
                n_ = N[si, ri, hi, vk].sum()
                per_sym.append((dirn * S[si, ri, hi, vk].sum() / n_ - cost) if n_ >= 20 else None)
            qnet = []
            for q in vk:
                n_ = N[:, ri, hi, q].sum()
                qnet.append(float(dirn * S[:, ri, hi, q].sum() / n_ - cost) if n_ else None)
            rule = [names[j] for j in rules[ri] if j >= 0]
            cands.append({"rule": rule, "horizon_min": int(HORIZONS[hi]), "direction": "LONG" if dirn > 0 else "SHORT",
                          "disc_n": int(nd[ri, hi]), "disc_net_bps": float(net_d[ri, hi]), "disc_t": float(t_d[ri, hi]),
                          "val_n": int(nv), "val_gross_bps": float(mv), "val_net_bps": float(net_v), "val_z": float(z),
                          "p_one_sided": p_one, "p_bonferroni": min(1.0, p_one * n_variants),
                          "val_trades_per_day": float(nv / 273), "quarters_net_bps": qnet,
                          "symbols_positive": int(sum(1 for x in per_sym if x is not None and x > 0)),
                          "symbols_tested": int(sum(1 for x in per_sym if x is not None))})
        # Benjamini–Hochberg over ALL variants: rank of each candidate's p among n_variants (others assumed >= them)
        ps = sorted((c_["p_one_sided"], i) for i, c_ in enumerate(cands))
        for rank, (p, i) in enumerate(ps, start=1):
            cands[i]["q_bh_all_variants"] = min(1.0, p * n_variants / rank)
        for c_ in cands:
            c_["edge"] = bool(c_["p_bonferroni"] < 0.05 and c_["val_net_bps"] > 0 and c_["symbols_positive"] >= 6
                              and all(q is not None and q > 0 for q in c_["quarters_net_bps"]))
        cands.sort(key=lambda x: -x["val_net_bps"])
        out["scenarios"][cname] = {"top_by_discovery": cands,
                                   "edges": [c_ for c_ in cands if c_["edge"]],
                                   "best_validation_net_bps": max((c_["val_net_bps"] for c_ in cands), default=None),
                                   "share_positive_validation": float(np.mean([c_["val_net_bps"] > 0 for c_ in cands])) if cands else None}
        log(f"{cname}: {len(out['scenarios'][cname]['edges'])} edges among top {len(cands)}")
    return out


def to_markdown(o: dict[str, Any]) -> str:
    f = lambda x, nd=2: "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))  # noqa: E731
    L = [f"# Massive strategy search — {o['n_rules']:,} rules, {o['n_variants_searched']:,} variants", "",
         f"Instruments: {', '.join(o['symbols'])}. Discovery 2025-01 … 2025-09 (direction + ranking), validation 2025-10 … 2026-06; holdout not ingested.", ""]
    for cname, s in o["scenarios"].items():
        L += [f"## {cname} ({o['costs_bps'][cname]} bps round trip): **{len(s['edges'])} statistically valid edges**", "",
              f"Best validation net among the top-{len(s['top_by_discovery'])} discovery rules: {f(s['best_validation_net_bps'])} bps; "
              f"share with positive validation net: {f(s['share_positive_validation'])}", "",
              "| rule | h | dir | disc net bps | val n | val gross | val net | trades/day | p (Bonferroni) | q (BH, all) | sym + | quarters net |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c_ in s["top_by_discovery"][:30]:
            L.append(f"| {' & '.join(c_['rule'])} | {c_['horizon_min']} | {c_['direction']} | {f(c_['disc_net_bps'])} | {c_['val_n']} | "
                     f"{f(c_['val_gross_bps'])} | {f(c_['val_net_bps'])} | {f(c_['val_trades_per_day'])} | {f(c_['p_bonferroni'], 3)} | "
                     f"{f(c_.get('q_bh_all_variants'), 3)} | {c_['symbols_positive']}/{c_['symbols_tested']} | "
                     f"{[None if q is None else round(q, 1) for q in c_['quarters_net_bps']]} |")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--stem", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--max-rules", type=int, default=0)
    g = sub.add_parser("aggregate")
    g.add_argument("--inputs", required=True)
    g.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "run":
        run_symbol(a.stem, a.out, max_rules=a.max_rules)
    else:
        from ..backtest.metrics import json_safe
        o = aggregate(a.inputs)
        json.dump(json_safe(o), open(a.out, "w"), default=str)
        open(a.out.replace(".json", ".md"), "w").write(to_markdown(o))
        print(to_markdown(o))


if __name__ == "__main__":
    main()
