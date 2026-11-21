"""Microstructure research A–F (2.1, §9). Pre-registered, leak-free, holdout untouched.

Protocol (fixed before any result was seen):
  * Data: 1m tables built by micro.archive for 2024-12 … 2026-06 (warm-up Dec 2024). The final holdout
    (2026-07 … 2026-10) is not ingested at all.
  * Split of the selection period: DISCOVERY 2025-01-01 … 2025-09-30, VALIDATION 2025-10-01 … 2026-06-30.
    Each condition's trade direction (follow / fade) is decided ONLY on discovery (sign of the pooled
    mean forward return). All reported performance and significance come from VALIDATION only.
  * Conditions: a fixed list per combination A–F (below). Thresholds are rolling, causal percentiles
    (30-day window) or fixed z-scores — not tuned.
  * Trade model: decision at the close of bar i, entry at that close (BASE) or one bar later
    (STRESSED latency), exit at the close of bar i+h, h ∈ {1, 5, 15, 60} minutes. Non-overlapping:
    after a trade the next one may start only after its exit. Costs per round trip: BASE 14 bps
    (taker 5 bps × 2 + slippage 2 bps × 2), STRESSED 23 bps (+ spread and 5 bps slippage).
  * Statistics: block-bootstrap 95 % CI and p-value of the validation mean; Benjamini–Hochberg
    FDR over ALL condition × horizon tests; per-symbol consistency.
  * A result is an EDGE CANDIDATE only if on validation: n >= 100, net expectancy (BASE) CI low > 0,
    FDR q < 0.05, STRESSED net > 0, and the mean has the same sign on >= 6 of 10 instruments.
  * C (liquidations) and the liquidation part of E have no historical data for 2025–2026 and are
    reported as INCOMPLETE; nothing is estimated in their place.

  python -m millionx.micro.research --data-dir /tmp/md --out micro_research.json
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from ..backtest import stats2
from . import archive

DAY = 86_400_000
DISC = ("2025-01-01", "2025-10-01")
VALID = ("2025-10-01", "2026-07-01")
HORIZONS = (1, 5, 15, 60)
COST = {"BASE": 14.0, "STRESSED": 23.0}  # bps per round trip
PCT_WIN = 30 * 1440


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _roll_sum(x: np.ndarray, n: int) -> np.ndarray:
    v = np.nan_to_num(x)
    c = np.concatenate([[0.0], np.cumsum(v)])
    out = np.full(len(x), np.nan)
    out[n - 1:] = c[n:] - c[:-n]
    return out


def _chg(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n:] = x[n:] / x[:-n] - 1
    return out


def _z(x: np.ndarray, n: int) -> np.ndarray:
    import pandas as pd
    s = pd.Series(x)
    m = s.rolling(n, min_periods=n // 2).mean()
    sd = s.rolling(n, min_periods=n // 2).std()
    return ((s - m) / sd).to_numpy()


def _pct(x: np.ndarray, sample: int = 5) -> np.ndarray:
    """Causal rolling percentile (30 d) evaluated every `sample` minutes and held (speed)."""
    from ..regime2 import rolling_percentile
    idx = np.arange(0, len(x), sample)
    p = rolling_percentile(x[idx], PCT_WIN // sample, min_count=500)
    # minute i uses the value computed at the last sampled minute <= i (causal hold)
    return p[np.arange(len(x)) // sample] if len(p) else np.full(len(x), np.nan)


def features(c: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    close = c["close"].astype(float)
    vol = c["volume"].astype(float)
    f: dict[str, np.ndarray] = {}
    lr = np.log(close)
    for n in (15, 60):
        r = np.full(len(close), np.nan)
        r[n:] = lr[n:] - lr[:-n]
        f[f"ret{n}"] = r
    tr = (c["high"] - c["low"]) / close
    f["atr15"] = _roll_sum(tr, 15) / 15
    # CVD: trade-level when available, else kline taker volume (identical at 1m aggregation)
    has_agg = "agg_buy" in c and np.isfinite(c["agg_buy"]).mean() > 0.5
    delta = (c["agg_buy"] - c["agg_sell"]) if has_agg else (2 * c["taker_buy_volume"] - vol)
    f["cvd_source"] = np.array(["aggTrades" if has_agg else "kline_taker"])
    for n in (15, 60):
        f[f"cvd{n}_norm"] = _roll_sum(delta, n) / np.maximum(1e-12, _roll_sum(vol, n))
    f["cvd15_z"] = _z(f["cvd15_norm"], 1440)
    if has_agg:
        ld = np.nan_to_num(c["agg_large_buy"]) - np.nan_to_num(c["agg_large_sell"])
        f["large15_z"] = _z(_roll_sum(ld, 15) / np.maximum(1e-12, _roll_sum(vol, 15)), 1440)
    # VWAP (24 h rolling) deviation z
    pv = _roll_sum(close * vol, 1440)
    vwap = pv / np.maximum(1e-12, _roll_sum(vol, 1440))
    f["vwap_z"] = _z(close / vwap - 1, 1440)
    # funding
    if "premium" in c:
        f["prem_pct"] = _pct(c["premium"].astype(float))
    # open interest (already lagged one 5-min period in the archive table)
    if "oi" in c:
        oi = c["oi"].astype(float)
        oi[~(oi > 0)] = np.nan  # the archive has a few zero/invalid OI rows (flagged in the quality report)
        f["oi_chg60"] = _chg(oi, 60)
        f["oi_chg15"] = _chg(oi, 15)
        f["oi_vol"] = c["oi_value"].astype(float) / np.maximum(1e-9, _roll_sum(c["quote_volume"].astype(float), 60))
    # order book (bookDepth cumulative notional within +-x %)
    if "depth_m1" in c and "depth_p1" in c:
        b1, a1 = c["depth_m1"].astype(float), c["depth_p1"].astype(float)
        b1[~(b1 > 0)] = np.nan
        a1[~(a1 > 0)] = np.nan
        f["imb1"] = (b1 - a1) / (b1 + a1)
        f["imb1_pct"] = _pct(f["imb1"])
        tot = b1 + a1
        f["depth1_pct"] = _pct(tot)
    if "depth_m0_2" in c and "depth_p0_2" in c:
        b, a = c["depth_m0_2"].astype(float), c["depth_p0_2"].astype(float)
        f["imb02_pct"] = _pct((b - a) / (b + a))
    return f


def conditions(f: dict[str, np.ndarray], regime_trending: np.ndarray | None) -> dict[str, tuple[str, np.ndarray]]:
    """name -> (combination, boolean state at bar close). Pre-registered list."""
    g = lambda k: f.get(k)  # noqa: E731
    C: dict[str, tuple[str, np.ndarray]] = {}

    def add(name, combo, *parts):
        if any(p is None for p in parts):
            return
        m = np.ones(len(parts[0]), bool)
        for p in parts:
            m &= np.nan_to_num(p, nan=0).astype(bool)
        C[name] = (combo, m)

    ret60, ret15 = g("ret60"), g("ret15")
    pp, oc = g("prem_pct"), g("oi_chg60")
    if pp is not None and oc is not None:
        add("A1_funding_hi_oi_up_price_up", "A", pp >= 0.95, oc > 0, ret60 > 0)
        add("A2_funding_lo_oi_up_price_dn", "A", pp <= 0.05, oc > 0, ret60 < 0)
        add("A3_funding_hi_oi_dn", "A", pp >= 0.95, oc < 0)
        add("A4_funding_lo_oi_dn", "A", pp <= 0.05, oc < 0)
    cv = g("cvd60_norm")
    if oc is not None:
        add("B1_price_up_oi_up_cvd_up", "B", ret60 > 0, oc > 0, cv > 0)
        add("B2_price_dn_oi_up_cvd_dn", "B", ret60 < 0, oc > 0, cv < 0)
        add("B3_price_up_oi_dn", "B", ret60 > 0, oc < 0)
        add("B4_price_dn_oi_dn", "B", ret60 < 0, oc < 0)
    if cv is not None:
        add("B5_price_up_cvd_dn_divergence", "B", ret60 > 0.003, cv < -0.05)
        add("B6_price_dn_cvd_up_divergence", "B", ret60 < -0.003, cv > 0.05)
    cz, at, vz = g("cvd15_z"), g("atr15"), g("vwap_z")
    if cz is not None:
        flat = np.abs(ret15) <= 0.25 * at * np.sqrt(15)
        add("D1_sell_absorption_below_vwap", "D", cz <= -2, flat, vz <= -1)
        add("D2_buy_absorption_above_vwap", "D", cz >= 2, flat, vz >= 1)
    lz = g("large15_z")
    if lz is not None:
        add("D3_large_buyers", "D", lz >= 2)
        add("D4_large_sellers", "D", lz <= -2)
    if pp is not None and oc is not None and regime_trending is not None:
        add("E1_funding_hi_oi_up_trending", "E", pp >= 0.95, oc > 0, regime_trending)
        add("E2_funding_hi_oi_up_ranging", "E", pp >= 0.95, oc > 0, ~regime_trending)
        add("E3_funding_lo_oi_up_trending", "E", pp <= 0.05, oc > 0, regime_trending)
        add("E4_funding_lo_oi_up_ranging", "E", pp <= 0.05, oc > 0, ~regime_trending)
    ip = g("imb1_pct")
    if ip is not None:
        add("F1_bid_heavy_1pct_and_up_momentum", "F", ip >= 0.95, ret15 > 0)
        add("F2_ask_heavy_1pct_and_dn_momentum", "F", ip <= 0.05, ret15 < 0)
        add("F5_liquidity_thin_up_momentum", "F", g("depth1_pct") <= 0.05, ret15 > 0)
        add("F6_liquidity_thin_dn_momentum", "F", g("depth1_pct") <= 0.05, ret15 < 0)
    i2 = g("imb02_pct")
    if i2 is not None:
        add("F3_bid_heavy_0_2pct", "F", i2 >= 0.95)
        add("F4_ask_heavy_0_2pct", "F", i2 <= 0.05)
    return C


def trades(state: np.ndarray, close: np.ndarray, t: np.ndarray, h: int, direction: int, delay: int,
           lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Non-overlapping trades for bars with lo <= t < hi. Returns (gross bps, entry time)."""
    idx = np.flatnonzero(state & (t >= lo) & (t < hi))
    out_r, out_t = [], []
    n = len(close)
    k = 0
    while k < len(idx):  # jump straight to the first state at/after the previous exit (loop count = trades)
        i = int(idx[k])
        e, x = i + delay, i + delay + h
        if x < n and np.isfinite(close[e]) and np.isfinite(close[x]):
            out_r.append(direction * (close[x] / close[e] - 1) * 1e4)
            out_t.append(t[i])
            k = int(np.searchsorted(idx, x, side="left"))
        else:
            k += 1
    return np.array(out_r), np.array(out_t, dtype=np.int64)


def _metrics(r: np.ndarray, days: float) -> dict[str, Any]:
    if len(r) == 0:
        return {"n": 0}
    w, l_ = r[r > 0], r[r <= 0]
    eq = np.cumsum(r) / 1e4
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
    dd = float((peak - np.concatenate([[0.0], eq])).max())
    return {"n": int(len(r)), "exp_bps": float(r.mean()), "win_rate": float((r > 0).mean()),
            "pf": float(w.sum() / -l_.sum()) if len(l_) and l_.sum() < 0 else None,
            "max_dd_pct_1x": dd * 100, "per_day": float(len(r) / max(days, 1e-9))}


def bh_fdr(p: list[float]) -> list[float]:
    m = len(p)
    order = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q.tolist()


def run(data_dir: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    from ..models import Bars
    from ..regime2 import TRENDING, _last_closed, classify
    d0, d1 = _ms(DISC[0]), _ms(DISC[1])
    v0, v1 = _ms(VALID[0]), _ms(VALID[1])
    stems = sorted({p.rsplit(".", 2)[0] for p in glob.glob(os.path.join(data_dir, "*.base.npz"))})
    per: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    status = {"C": {"status": "INCOMPLETE", "reason": "no historical liquidation data for 2025-2026 on Binance "
                                                     "(liquidationSnapshot archive ends 2024-03); not estimated"},
              "E": {"status": "INCOMPLETE", "reason": "liquidation component missing; funding+OI+regime part run"}}
    sources: dict[str, Any] = {}
    for stem in stems:
        sym = os.path.basename(stem)
        c = archive.load(stem)
        t = c["open_time"].astype(np.int64)
        close = c["close"].astype(float)
        f = features(c)
        sources[sym] = {"cvd_source": str(f["cvd_source"][0]), "has_oi": "oi" in c, "has_book": "depth_m1" in c,
                        "has_premium": "premium" in c}
        ok = np.isfinite(close)
        b = Bars(t[ok], c["open"][ok].astype(float), c["high"][ok].astype(float), c["low"][ok].astype(float),
                 close[ok], c["volume"][ok].astype(float), c["taker_buy_volume"][ok].astype(float),
                 c["quote_volume"][ok].astype(float))
        rg = classify(b)
        k = _last_closed(rg.close_idx15, np.arange(len(b)))
        lab = np.where(k >= 0, rg.label[np.maximum(k, 0)], "SIDEWAYS")
        trending = np.zeros(len(t), bool)
        trending[np.flatnonzero(ok)] = np.isin(lab, list(TRENDING))
        conds = conditions(f, trending)
        per[sym] = {}
        for name, (combo, st) in conds.items():
            per[sym][name] = {}
            for h in HORIZONS:
                rd, _ = trades(st, close, t, h, +1, 0, d0, d1)
                per[sym][name][h] = {"combo": combo, "disc_sum": float(rd.sum()), "disc_n": int(len(rd)),
                                     "state_share": float(st[(t >= d0) & (t < v1)].mean())}
        log(f"{sym}: {len(conds)} conditions, cvd={sources[sym]['cvd_source']}")
        per[sym]["_arrays"] = {"close": close, "t": t, "conds": conds}
    # direction from DISCOVERY (pooled over instruments), evaluation on VALIDATION
    results = []
    names = sorted({n for s in per for n in per[s] if not n.startswith("_")})
    combo_of = {n: cb for s in per for n, (cb, _) in per[s]["_arrays"]["conds"].items()}
    vdays = (v1 - v0) / DAY
    for name in names:
        for h in HORIZONS:
            ssum = sum(per[s][name][h]["disc_sum"] for s in per if name in per[s])
            sn = sum(per[s][name][h]["disc_n"] for s in per if name in per[s])
            direction = 1 if ssum >= 0 else -1
            pooled = {sc: [] for sc in COST}
            tt, sym_sign = [], {}
            for s in per:
                if name not in per[s]:
                    continue
                A = per[s]["_arrays"]
                st = A["conds"][name][1]
                r0, t0_ = trades(st, A["close"], A["t"], h, direction, 0, v0, v1)
                r1, _ = trades(st, A["close"], A["t"], h, direction, 1, v0, v1)
                pooled["BASE"].append(r0 - COST["BASE"])
                pooled["STRESSED"].append(r1 - COST["STRESSED"])
                tt.append(t0_)
                if len(r0) >= 10:
                    sym_sign[s] = float(np.mean(r0 - COST["BASE"]))
            order = np.argsort(np.concatenate(tt)) if tt else np.array([], int)
            base = np.concatenate(pooled["BASE"])[order] if pooled["BASE"] else np.array([])
            stress = np.concatenate(pooled["STRESSED"]) if pooled["STRESSED"] else np.array([])
            gross = base + COST["BASE"]
            # bootstrap directly in bps (stats2 rounds to 4 decimals: bps keep full precision)
            boot = stats2.block_bootstrap(base, risk_fraction=1e-4) if len(base) >= 20 else {}
            ci = list(boot.get("exp_r_ci95", [math.nan, math.nan]))
            results.append({"condition": name, "combo": combo_of.get(name, name[0]),
                            "horizon_min": h, "direction_from_discovery": "follow(+1)" if direction > 0 else "fade(-1)",
                            "discovery_n": sn, "discovery_gross_mean_bps": ssum / sn if sn else None,
                            "validation": _metrics(base, vdays * max(1, len(per))),
                            "validation_gross_bps": float(gross.mean()) if len(gross) else None,
                            "validation_ci95_bps": ci, "p_value": boot.get("p_exp_le_0", 1.0),
                            "stressed_exp_bps": float(stress.mean()) if len(stress) else None,
                            "symbols_positive": sum(v > 0 for v in sym_sign.values()), "symbols_tested": len(sym_sign)})
    q = bh_fdr([r["p_value"] if r["p_value"] is not None else 1.0 for r in results])
    for r, qq in zip(results, q):
        r["fdr_q"] = qq
        v = r["validation"]
        r["edge_candidate"] = bool(v.get("n", 0) >= 100 and r["validation_ci95_bps"][0] > 0 and qq < 0.05
                                   and (r["stressed_exp_bps"] or -1) > 0 and r["symbols_positive"] >= 6)
    for s in per:
        per[s].pop("_arrays", None)
    return {"protocol": __doc__, "discovery": DISC, "validation": VALID, "costs_bps": COST, "status": status,
            "sources": sources, "tests": len(results), "results": results,
            "edge_candidates": [f"{r['condition']}@{r['horizon_min']}m" for r in results if r["edge_candidate"]]}


def to_markdown(o: dict[str, Any]) -> str:
    L = ["# Microstructure research A–F (validation period only; holdout not ingested)", "",
         f"Discovery {o['discovery'][0]} … {o['discovery'][1]} (direction only) · Validation {o['validation'][0]} … "
         f"{o['validation'][1]} · costs BASE {o['costs_bps']['BASE']} bps, STRESSED {o['costs_bps']['STRESSED']} bps round trip · "
         f"{o['tests']} tests, Benjamini–Hochberg FDR.", "",
         f"**Edge candidates: {len(o['edge_candidates'])}** — {', '.join(o['edge_candidates']) or 'none'}", "",
         "| condition | h | dir | n (val) | gross bps | net bps (BASE) | 95 % CI | q (FDR) | STRESSED bps | WR | PF | /day/sym | sym + |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(o["results"], key=lambda r: (r["condition"], r["horizon_min"])):
        v = r["validation"]
        fmt = lambda x, nd=1: "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"  # noqa: E731
        L.append(f"| {r['condition']} | {r['horizon_min']} | {r['direction_from_discovery'][:6]} | {v.get('n', 0)} | "
                 f"{fmt(r['validation_gross_bps'])} | {fmt(v.get('exp_bps'))} | {fmt(r['validation_ci95_bps'][0])}…"
                 f"{fmt(r['validation_ci95_bps'][1])} | {fmt(r['fdr_q'], 3)} | {fmt(r['stressed_exp_bps'])} | "
                 f"{fmt(v.get('win_rate'), 2)} | {fmt(v.get('pf'), 2)} | {fmt(v.get('per_day'), 2)} | "
                 f"{r['symbols_positive']}/{r['symbols_tested']} |")
    L += ["", "## INCOMPLETE", ""] + [f"- **{k}**: {v['reason']}" for k, v in o["status"].items()]
    L += ["", "## Sources per instrument", ""] + [f"- {s}: {json.dumps(v)}" for s, v in o["sources"].items()]
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    o = run(a.data_dir)
    from ..backtest.metrics import json_safe
    json.dump(json_safe(o), open(a.out, "w"), default=str)
    open(a.out.replace(".json", ".md"), "w").write(to_markdown(o))
    print(to_markdown(o))


if __name__ == "__main__":
    main()
