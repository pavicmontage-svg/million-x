"""MillionX 2.3 — new trading approaches with published evidence in crypto, tested on 6 years of real data.

  python -m millionx.strat23.research --out v23_research.json        (CI: needs data.binance.vision)

Data: Binance USDⓈ-M monthly 4h klines + funding history, 10 instruments, 2020-01 … 2026-06.
The final holdout (2026-07 … 2026-10) is NOT downloaded.

Protocol (fixed before results):
  * DISCOVERY 2020-01-01 … 2023-12-31: choose one configuration per strategy from a small grid (by Sharpe).
  * VALIDATION 2024-01-01 … 2026-06-30: report only. Every grid config is also reported (no cherry-picking).
  * Daily decisions at the daily close, applied to the NEXT day's return (no look-ahead).
  * Costs: 5 bps taker + 2 bps slippage per unit of turnover; funding paid/received on futures positions
    from the real funding history; carry adds spot fees (10 bps per side) and is labelled an estimate
    (spot/perp basis moves are not modelled).
  * Volatility targeting: each instrument sized to the same risk (20-day realised volatility), total
    gross exposure capped; results reported as returns, Sharpe, max drawdown, CAGR and days to double.

Strategies:
  T  Time-series momentum (trend following): ensemble of sign(return over L days).
  D  Donchian breakout (turtle-style): enter on an N-day high/low, exit on an M-day opposite extreme.
  X  Cross-sectional momentum: weekly, long the strongest 3 and short the weakest 3 of the 10.
  C  Funding carry (estimate): short perp + long spot while the trailing funding is above a threshold.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from datetime import date
from typing import Any, Callable

import numpy as np

BASE = "https://data.binance.vision/data/futures/um/monthly"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT"]
START, END = (2020, 1), (2026, 6)
DISC = ("2020-01-01", "2024-01-01")
VALID = ("2024-01-01", "2026-07-01")
COST = 7e-4          # per unit turnover (taker 5 bps + slippage 2 bps)
SPOT_COST = 1.2e-3   # spot leg per unit turnover (10 bps fee + 2 bps slippage)
TARGET_VOL = 0.40    # annualised portfolio volatility target
MAX_GROSS = 3.0
DAY = 86_400_000

GRID = {
    "T": [{"L": (10, 30, 60), "long_only": False}, {"L": (20, 60, 120), "long_only": False},
          {"L": (60, 120, 240), "long_only": False}, {"L": (20, 60, 120), "long_only": True}],
    "D": [{"entry": 20, "exit": 10}, {"entry": 55, "exit": 20}, {"entry": 100, "exit": 50}],
    "X": [{"L": 14}, {"L": 30}, {"L": 60}],
    "C": [{"thr": 0.0}, {"thr": 1e-4}, {"thr": 3e-4}],
}


def _months():
    y, m = START
    while (y, m) <= END:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _csv_rows(content: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(content))
    lines = z.open(z.namelist()[0]).read().decode().strip().splitlines()
    rows = [ln.split(",") for ln in lines]
    if rows and not rows[0][0].lstrip("-").replace(".", "").isdigit():
        rows = rows[1:]
    return rows


def download(sym: str, log: Callable = print) -> dict[str, np.ndarray]:
    import requests
    s = requests.Session()
    k, f = [], []
    for y, m in _months():
        r = s.get(f"{BASE}/klines/{sym}/4h/{sym}-4h-{y:04d}-{m:02d}.zip", timeout=60)
        if r.status_code == 200:
            k += _csv_rows(r.content)
        r = s.get(f"{BASE}/fundingRate/{sym}/{sym}-fundingRate-{y:04d}-{m:02d}.zip", timeout=60)
        if r.status_code == 200:
            f += _csv_rows(r.content)
    log(f"{sym}: {len(k)} 4h bars, {len(f)} funding settlements")
    ka = np.array([[float(x[0]), float(x[2]), float(x[3]), float(x[4])] for x in k]) if k else np.zeros((0, 4))
    fa = np.array([[float(x[0]), float(x[2])] for x in f]) if f else np.zeros((0, 2))
    return {"k": ka[np.argsort(ka[:, 0])] if len(ka) else ka, "f": fa[np.argsort(fa[:, 0])] if len(fa) else fa}


def daily_panel(raw: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    """Aligned daily close/high/low and daily funding sum per instrument (NaN before listing)."""
    d0 = int(np.datetime64(f"{START[0]}-{START[1]:02d}-01").astype("datetime64[ms]").astype(np.int64))
    d1 = int(np.datetime64("2026-07-01").astype("datetime64[ms]").astype(np.int64))
    days = np.arange(d0, d1, DAY)
    n, m = len(days), len(SYMBOLS)
    C, H, L, FU = (np.full((n, m), np.nan) for _ in range(4))
    for j, sym in enumerate(SYMBOLS):
        k = raw[sym]["k"]
        if len(k):
            di = ((k[:, 0] - d0) // DAY).astype(int)
            ok = (di >= 0) & (di < n)
            for i, row in zip(di[ok], k[ok]):
                C[i, j] = row[3]  # last 4h close of the day wins (sorted)
                H[i, j] = row[1] if np.isnan(H[i, j]) else max(H[i, j], row[1])
                L[i, j] = row[2] if np.isnan(L[i, j]) else min(L[i, j], row[2])
        f = raw[sym]["f"]
        if len(f):
            di = ((f[:, 0] - d0) // DAY).astype(int)
            ok = (di >= 0) & (di < n)
            FU[:, j] = 0.0
            np.add.at(FU[:, j], di[ok], f[ok, 1])
            FU[np.isnan(C[:, j]), j] = np.nan
    return {"days": days, "C": C, "H": H, "L": L, "F": FU}


def _ret(C):
    r = np.full_like(C, np.nan)
    r[1:] = C[1:] / C[:-1] - 1
    return r


def _vol(r, n=20):
    out = np.full_like(r, np.nan)
    for i in range(n, len(r)):
        w = r[i - n + 1:i + 1]
        out[i] = np.nanstd(w) * math.sqrt(365) if np.isfinite(w).sum() >= n // 2 else np.nan
    return out


def signals(P: dict[str, Any], strat: str, p: dict[str, Any]) -> np.ndarray:
    C, H, L = P["C"], P["H"], P["L"]
    n, m = C.shape
    S = np.zeros((n, m))
    if strat == "T":
        for Lb in p["L"]:
            past = np.full_like(C, np.nan)
            past[Lb:] = C[Lb:] / C[:-Lb] - 1
            S += np.sign(np.nan_to_num(past))
        S /= len(p["L"])
        if p["long_only"]:
            S = np.clip(S, 0, None)
    elif strat == "D":
        for j in range(m):
            pos = 0
            for i in range(p["entry"], n):
                if not np.isfinite(C[i, j]):
                    pos = 0
                    continue
                hi_e = np.nanmax(H[i - p["entry"]:i, j]) if np.isfinite(H[i - p["entry"]:i, j]).any() else np.nan
                lo_e = np.nanmin(L[i - p["entry"]:i, j]) if np.isfinite(L[i - p["entry"]:i, j]).any() else np.nan
                hi_x = np.nanmax(H[i - p["exit"]:i, j]) if np.isfinite(H[i - p["exit"]:i, j]).any() else np.nan
                lo_x = np.nanmin(L[i - p["exit"]:i, j]) if np.isfinite(L[i - p["exit"]:i, j]).any() else np.nan
                if pos == 0:
                    pos = 1 if C[i, j] > hi_e else (-1 if C[i, j] < lo_e else 0)
                elif pos == 1 and C[i, j] < lo_x:
                    pos = 0
                elif pos == -1 and C[i, j] > hi_x:
                    pos = 0
                S[i, j] = pos
    elif strat == "X":
        Lb = p["L"]
        cur = np.zeros(m)
        for i in range(Lb + 1, n):
            if i % 7 == 0:  # weekly rebalance
                past = C[i - 1] / C[i - 1 - Lb] - 1   # skip the most recent day
                ok = np.isfinite(past) & np.isfinite(C[i])
                cur = np.zeros(m)
                if ok.sum() >= 6:
                    idx = np.argsort(np.where(ok, past, np.nan))
                    valid = [j for j in idx if ok[j]]
                    for j in valid[-3:]:
                        cur[j] = 1
                    for j in valid[:3]:
                        cur[j] = -1
            S[i] = cur
    elif strat == "C":
        F = P["F"]
        f7 = np.full_like(F, np.nan)
        for i in range(7, n):
            f7[i] = np.nanmean(F[i - 7:i], axis=0) / 3  # average per 8 h settlement
        S = np.where(np.nan_to_num(f7) > p["thr"], -1.0, 0.0)  # short perp (hedged by long spot)
    return S


def backtest(P: dict[str, Any], strat: str, p: dict[str, Any]) -> dict[str, np.ndarray]:
    C, F = P["C"], P["F"]
    r = _ret(C)
    S = signals(P, strat, p)
    if strat == "C":
        # delta-neutral: price legs cancel; income = funding received on the short perp, minus both legs' costs
        W = np.abs(S) / max(1, len(SYMBOLS)) * 2.0     # 2x gross per instrument budget (perp + spot)
        W[np.isnan(C)] = 0
        Wp = np.vstack([np.zeros(len(SYMBOLS)), W[:-1]])     # decided at close t, held during t+1
        income = np.nansum(Wp / 2 * np.nan_to_num(F), axis=1)
        turn = np.nansum(np.abs(np.diff(np.vstack([np.zeros(len(SYMBOLS)), Wp]), axis=0)), axis=1)
        pnl = income - turn / 2 * (COST + SPOT_COST)
        return {"pnl": pnl, "gross": np.nansum(np.abs(Wp), axis=1)}
    vol = _vol(r)
    risk_w = np.where(np.isfinite(vol) & (vol > 0), (TARGET_VOL / np.sqrt(len(SYMBOLS))) / vol, 0.0)
    W = S * risk_w
    W[np.isnan(C)] = 0
    gross = np.sum(np.abs(W), axis=1)
    scale = np.where(gross > MAX_GROSS, MAX_GROSS / np.maximum(gross, 1e-12), 1.0)
    W = W * scale[:, None]
    Wp = np.vstack([np.zeros(len(SYMBOLS)), W[:-1]])        # yesterday's decision, today's return
    price = np.nansum(Wp * np.nan_to_num(r), axis=1)
    funding = -np.nansum(Wp * np.nan_to_num(F), axis=1)     # longs pay positive funding
    turn = np.nansum(np.abs(np.diff(np.vstack([np.zeros(len(SYMBOLS)), Wp]), axis=0)), axis=1)
    pnl = price + funding - turn * COST
    return {"pnl": pnl, "gross": np.sum(np.abs(Wp), axis=1), "price": price, "funding": funding, "cost": turn * COST}


def metrics(pnl: np.ndarray) -> dict[str, Any]:
    pnl = np.nan_to_num(pnl)
    if len(pnl) < 30:
        return {"n_days": int(len(pnl))}
    eq = np.cumprod(1 + pnl)
    years = len(pnl) / 365
    cagr = eq[-1] ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    dd = float((1 - eq / np.maximum.accumulate(eq)).max())
    sd = pnl.std()
    sharpe = float(pnl.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0
    from ..backtest import stats2
    boot = stats2.block_bootstrap(pnl * 1e4, risk_fraction=1e-4, block=20)
    m30 = [np.prod(1 + pnl[i:i + 30]) - 1 for i in range(0, len(pnl) - 29, 30)]
    return {"n_days": int(len(pnl)), "total_return": float(eq[-1] - 1), "cagr": float(cagr), "sharpe": sharpe,
            "max_dd": dd, "avg_daily_pct": float(pnl.mean() * 100), "best_day_pct": float(pnl.max() * 100),
            "worst_day_pct": float(pnl.min() * 100), "positive_days": float((pnl > 0).mean()),
            "positive_months": float(np.mean([x > 0 for x in m30])) if m30 else None,
            "days_to_double": float(math.log(2) / math.log(1 + cagr) * 365) if cagr > 0 else None,
            "daily_mean_bps_ci95": boot.get("exp_r_ci95"), "p_mean_le_0": boot.get("p_exp_le_0")}


def run(raw: dict[str, dict[str, np.ndarray]], log: Callable = print) -> dict[str, Any]:
    P = daily_panel(raw)
    days = P["days"]
    d = lambda s: int(np.datetime64(s).astype("datetime64[ms]").astype(np.int64))  # noqa: E731
    disc = (days >= d(DISC[0])) & (days < d(DISC[1]))
    val = (days >= d(VALID[0])) & (days < d(VALID[1]))
    n_trials = sum(len(v) for v in GRID.values())
    out: dict[str, Any] = {"protocol": __doc__, "symbols": SYMBOLS, "n_trials": n_trials, "strategies": {}}
    pnl_by: dict[str, np.ndarray] = {}
    for strat, grid in GRID.items():
        res = []
        for i, p in enumerate(grid):
            bt = backtest(P, strat, p)
            res.append({"cfg": i, "params": {k: list(v) if isinstance(v, tuple) else v for k, v in p.items()},
                        "discovery": metrics(bt["pnl"][disc]), "validation": metrics(bt["pnl"][val]),
                        "avg_gross_exposure": float(np.mean(bt["gross"][val]))})
            if "funding" in bt:
                res[-1]["validation_components_bps_per_day"] = {
                    k: float(np.mean(bt[k][val]) * 1e4) for k in ("price", "funding", "cost")}
            pnl_by[f"{strat}{i}"] = bt["pnl"]
        best = max(range(len(res)), key=lambda i: res[i]["discovery"].get("sharpe", -9))
        years = {}
        for y in range(2020, 2027):
            mk = (days >= d(f"{y}-01-01")) & (days < d(f"{y + 1}-01-01")) & (days < d(VALID[1]))
            if mk.any():
                years[str(y)] = metrics(pnl_by[f"{strat}{best}"][mk])
        vs = res[best]["validation"]
        from ..backtest import stats2
        dsr = stats2.deflated_sharpe(np.nan_to_num(pnl_by[f"{strat}{best}"][val]), n_trials)
        out["strategies"][strat] = {"configs": res, "chosen_on_discovery": best, "by_year_chosen": years,
                                    "deflated_sharpe_validation": dsr,
                                    "verdict": "CANDIDATE_FOR_PAPER" if (vs.get("sharpe", 0) >= 1.0 and
                                                                         (vs.get("daily_mean_bps_ci95") or [-1])[0] > 0 and
                                                                         vs.get("max_dd", 1) <= 0.35 and
                                                                         (dsr.get("dsr") or 0) >= 0.95) else "NO_TRADE"}
        log(f"{strat}: chosen cfg {best} -> validation {vs}")
    # combined portfolio of the chosen configurations (equal risk budget)
    chosen = [pnl_by[f"{s}{out['strategies'][s]['chosen_on_discovery']}"] for s in GRID]
    comb = np.nanmean(np.vstack(chosen), axis=0)
    out["combined_equal_weight"] = {"discovery": metrics(comb[disc]), "validation": metrics(comb[val])}
    return out


def to_markdown(o: dict[str, Any]) -> str:
    f = lambda x, nd=2: "—" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))  # noqa: E731
    L = ["# MillionX 2.3 — trend / momentum / carry research (2020-01 … 2026-06, holdout not used)", "",
         "| strat | cfg | params | disc Sharpe | disc CAGR | val Sharpe | val CAGR | val maxDD | val avg day % | val +months | days to double | CI daily bps | chosen | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, v in o["strategies"].items():
        for r in v["configs"]:
            ds, vs = r["discovery"], r["validation"]
            L.append(f"| {s} | {r['cfg']} | `{json.dumps(r['params'])}` | {f(ds.get('sharpe'))} | {f(ds.get('cagr'))} | {f(vs.get('sharpe'))} | "
                     f"{f(vs.get('cagr'))} | {f(vs.get('max_dd'))} | {f(vs.get('avg_daily_pct'), 3)} | {f(vs.get('positive_months'))} | "
                     f"{f(vs.get('days_to_double'), 0)} | {vs.get('daily_mean_bps_ci95')} | {'★' if r['cfg'] == v['chosen_on_discovery'] else ''} | "
                     f"{v['verdict'] if r['cfg'] == v['chosen_on_discovery'] else ''} |")
    L += ["", "## Chosen configuration by year", ""]
    for s, v in o["strategies"].items():
        L.append(f"- **{s}**: " + "; ".join(f"{y}: {f(m.get('total_return'))} (DD {f(m.get('max_dd'))})" for y, m in v["by_year_chosen"].items()))
    c = o["combined_equal_weight"]
    L += ["", f"**Combined (equal weight of chosen):** discovery Sharpe {f(c['discovery'].get('sharpe'))}, CAGR {f(c['discovery'].get('cagr'))}; "
          f"validation Sharpe {f(c['validation'].get('sharpe'))}, CAGR {f(c['validation'].get('cagr'))}, maxDD {f(c['validation'].get('max_dd'))}, "
          f"avg day {f(c['validation'].get('avg_daily_pct'), 3)} %"]
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    a = p.parse_args()
    raw = {s: download(s) for s in SYMBOLS}
    o = run(raw)
    from ..backtest.metrics import json_safe
    json.dump(json_safe(o), open(a.out, "w"), default=str)
    open(a.out.replace(".json", ".md"), "w").write(to_markdown(o))
    print(to_markdown(o))


if __name__ == "__main__":
    main()
