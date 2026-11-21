"""Historical microstructure ingestion from the public Binance archive (2.1, §2, §3, §5, §6, §11).

Builds ONE time-aligned 1-minute table per instrument for the selection period, every column
stamped with the time it became available, and writes it with its data-quality report:

  python -m millionx.micro.archive --symbol BTCUSDT --out data/micro/BTCUSDT.json
  -> data/micro/BTCUSDT.npz (columns) + BTCUSDT.json (quality, coverage, provenance)

Causality (decision taken at the CLOSE of 1m bar i, i.e. at open_time[i] + 60 s):
  * klines / taker volume / premium index / mark price of bar i: known at its close.
  * metrics (open interest, top-trader and taker ratios): a 5-minute row stamped t is used only from
    t + 5 min (one full period of publication lag; conservative, no back-filling).
  * bookDepth: a 30-second snapshot stamped t is used only at decisions >= t + 1 s.
  * funding: the settled rate of calc_time t is used only at decisions >= t; the premium index is
    the live estimate of the next rate.
  * aggTrades: aggregated into the bar in which the trade happened (known at that bar's close). The
    "large trade" threshold is the 99th percentile of the PREVIOUS day's trade notionals.
Missing source rows stay NaN. Nothing is interpolated or synthesised. The final holdout
(2026-07-01 onwards) is refused.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from . import quality as Q

BASE = "https://data.binance.vision/data/futures/um"
HOLDOUT_START = date(2026, 7, 1)
MIN = 60_000
BANDS = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _get(sess, url: str, tries: int = 4) -> Optional[bytes]:
    for k in range(tries):
        try:
            r = sess.get(url, timeout=180)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))
    return None


def _csv(content: bytes):
    import pandas as pd
    z = zipfile.ZipFile(io.BytesIO(content))
    raw = z.open(z.namelist()[0]).read()
    first = raw[:200].split(b"\n", 1)[0]
    header = 0 if not first[:1].isdigit() and not first[:1] == b"-" else None
    return pd.read_csv(io.BytesIO(raw), header=header)


def _ms(col) -> np.ndarray:
    """Timestamps as int64 ms: numeric ms/us columns or 'YYYY-mm-dd HH:MM:SS' strings (UTC)."""
    import pandas as pd
    if np.issubdtype(np.asarray(col).dtype, np.number):
        v = np.asarray(col, dtype=np.int64)
        return np.where(v > 10**14, v // 1000, v)  # microseconds -> ms
    dt = pd.to_datetime(pd.Series(col), utc=True)
    return ((dt - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy(dtype=np.int64)


def _ffill_at(grid_decision: np.ndarray, t_avail: np.ndarray, values: np.ndarray, max_age_ms: int) -> tuple[np.ndarray, np.ndarray]:
    """Value known at each decision time: last row with t_avail <= decision, NaN if older than max_age."""
    order = np.argsort(t_avail, kind="stable")
    t, v = t_avail[order], values[order]
    j = np.searchsorted(t, grid_decision, side="right") - 1
    out = np.full(len(grid_decision), np.nan)
    age = np.full(len(grid_decision), np.nan)
    ok = j >= 0
    out[ok] = v[j[ok]]
    age[ok] = grid_decision[ok] - t[j[ok]]
    stale = ok & (age > max_age_ms)
    out[stale] = np.nan
    return out, age


def build_symbol(sym: str, start: date, end: date, kline_dir: str, workers: int = 8, aggtrades: bool = True,
                 forward_test_of_frozen: bool = False,
                 log=print) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if end >= HOLDOUT_START and not forward_test_of_frozen:
        raise ValueError("2.1 rule: the final holdout (from 2026-07-01) is not ingested for research")
    import requests
    from ..backtest.data import ensure_data, load_symbol
    sess = requests.Session()
    days = list(_days(start, end))
    t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    t1 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000
    grid = np.arange(t0, t1, MIN, dtype=np.int64)
    decision = grid + MIN  # decisions at bar close
    n = len(grid)
    cols: dict[str, np.ndarray] = {"open_time": grid}
    rep: dict[str, Any] = {"symbol": sym, "start": str(start), "end": str(end), "minutes": n, "sources": {},
                           "causality": __doc__.split("Causality", 1)[1].split("Missing", 1)[0].strip()}

    # ---- klines (existing loader)
    ensure_data(sym, kline_dir, start, end, progress=lambda s: None)
    b = load_symbol(sym, kline_dir, start, end)
    idx = np.searchsorted(grid, b.open_time)
    okk = (idx < n) & (grid[np.minimum(idx, n - 1)] == b.open_time)
    for k in ("open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume"):
        a = np.full(n, np.nan)
        a[idx[okk]] = getattr(b, k)[okk]
        cols[k] = a
    rep["sources"]["klines"] = Q.check_series("ohlcv", sym, b.open_time, b.close, MIN, t0, t1).to_dict()
    log(f"{sym} klines {len(b)}")

    # ---- daily datasets, downloaded in parallel
    def fetch(kind: str, d: date):
        url = {"metrics": f"{BASE}/daily/metrics/{sym}/{sym}-metrics-{d}.zip",
               "bookDepth": f"{BASE}/daily/bookDepth/{sym}/{sym}-bookDepth-{d}.zip",
               "premium": f"{BASE}/daily/premiumIndexKlines/{sym}/1m/{sym}-1m-{d}.zip",
               "mark": f"{BASE}/daily/markPriceKlines/{sym}/1m/{sym}-1m-{d}.zip",
               "aggTrades": f"{BASE}/daily/aggTrades/{sym}/{sym}-aggTrades-{d}.zip"}[kind]
        c = _get(sess, url)
        return d, (None if c is None else _csv(c))

    def run_kind(kind: str, handler, w: int = workers):
        """Downloads run ahead in `w` threads (bounded window), handlers run in day order."""
        missing = []
        with ThreadPoolExecutor(w) as ex:
            pending = [ex.submit(fetch, kind, dd) for dd in days[:w]]
            nxt = w
            while pending:
                d, df = pending.pop(0).result()
                if nxt < len(days):
                    pending.append(ex.submit(fetch, kind, days[nxt]))
                    nxt += 1
                if df is None or len(df) == 0:
                    missing.append(str(d))
                else:
                    handler(d, df)
        log(f"{sym} {kind}: {len(days) - len(missing)}/{len(days)} days")
        return missing

    # metrics -> open interest etc. (5 min)
    m_rows: list[np.ndarray] = []

    def h_metrics(d, df):
        df.columns = [str(c) for c in df.columns] if isinstance(df.columns[0], str) else \
            ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
             "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"][:df.shape[1]]
        bad = Q.check_symbol_mapping(sym, df["symbol"].astype(str).unique())
        if bad:
            rep.setdefault("symbol_mapping", []).extend(bad)
            df = df[df["symbol"].astype(str).str.upper() == sym]
        t = _ms(df["create_time"])
        m_rows.append(np.column_stack([t] + [df[c].astype(float).to_numpy() for c in
                                             ("sum_open_interest", "sum_open_interest_value",
                                              "sum_toptrader_long_short_ratio", "count_long_short_ratio",
                                              "sum_taker_long_short_vol_ratio")]))
    miss = run_kind("metrics", h_metrics)
    if m_rows:
        m = np.vstack(m_rows)
        rep["sources"]["open_interest"] = {**Q.check_series("open_interest", sym, m[:, 0], m[:, 1], 5 * MIN, t0, t1).to_dict(),
                                           "missing_days": miss}
        for k, name in enumerate(("oi", "oi_value", "top_ls_pos", "ls_accounts", "taker_ls_vol"), start=1):
            cols[name], age = _ffill_at(decision, m[:, 0] + 5 * MIN, m[:, k], 15 * MIN)
        cols["oi_age_ms"] = age
    else:
        rep["sources"]["open_interest"] = {"status": Q.MISSING, "missing_days": miss}

    # bookDepth -> cumulative notional per +-% band (30 s)
    bd: list[np.ndarray] = []

    def h_book(d, df):
        if not isinstance(df.columns[0], str):
            df.columns = ["timestamp", "percentage", "depth", "notional"][:df.shape[1]]
        t = _ms(df["timestamp"])
        pc = df["percentage"].astype(float).to_numpy()
        no = df["notional"].astype(float).to_numpy()
        bd.append(np.column_stack([t, pc, no]))
    miss = run_kind("bookDepth", h_book)
    if bd:
        x = np.vstack(bd)
        snaps = np.unique(x[:, 0])
        rep["sources"]["order_book_depth"] = {**Q.check_series("order_book", sym, snaps, None, 30_000, t0, t1).to_dict(),
                                              "missing_days": miss, "bands_seen": sorted(set(np.round(x[:, 1], 2).tolist()))}
        for band in BANDS:
            sel = np.isclose(x[:, 1], band)
            if sel.any():
                key = f"depth_{'m' if band < 0 else 'p'}{abs(band):g}".replace(".", "_")
                cols[key], age = _ffill_at(decision, x[sel, 0] + 1_000, x[sel, 2], 90_000)
        cols["book_age_ms"] = age
    else:
        rep["sources"]["order_book_depth"] = {"status": Q.MISSING, "missing_days": miss}

    # premium index (live funding estimate) and mark price, 1 m, known at bar close
    for kind, name in (("premium", "premium"), ("mark", "mark")):
        arr = np.full(n, np.nan)

        def h_k(d, df, arr=arr):
            ot = _ms(df.iloc[:, 0])
            j = np.searchsorted(grid, ot)
            ok = (j < n) & (grid[np.minimum(j, n - 1)] == ot)
            arr[j[ok]] = df.iloc[:, 4].astype(float).to_numpy()[ok]
        miss = run_kind(kind, h_k)
        cols[name] = arr
        good = np.isfinite(arr)
        rep["sources"][name] = {**Q.check_series(name, sym, grid[good], arr[good] if name == "mark" else None, MIN, t0, t1).to_dict(),
                                "missing_days": miss}

    # settled funding (monthly files)
    fr: list[np.ndarray] = []
    months = sorted({(d.year, d.month) for d in days})
    for y, mth in months:
        c = _get(sess, f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y:04d}-{mth:02d}.zip")
        if c is None:
            continue
        df = _csv(c)
        if not isinstance(df.columns[0], str):
            df.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"][:df.shape[1]]
        fr.append(np.column_stack([_ms(df["calc_time"]), df["funding_interval_hours"].astype(float),
                                   df["last_funding_rate"].astype(float)]))
    if fr:
        f = np.vstack(fr)
        f = f[np.argsort(f[:, 0])]
        cols["funding_settled"], _ = _ffill_at(decision, f[:, 0], f[:, 2], 9 * 3600_000)
        cols["funding_interval_h"], _ = _ffill_at(decision, f[:, 0], f[:, 1], 9 * 3600_000)
        rep["sources"]["funding"] = {**Q.check_series("funding", sym, f[:, 0], None, int(np.median(f[:, 1])) * 3600_000,
                                                      t0, t1).to_dict(),
                                     "intervals_h": sorted(set(f[:, 1].tolist())), "settlements": int(len(f))}
        cols["_funding_events"] = f
    else:
        rep["sources"]["funding"] = {"status": Q.MISSING}

    # aggTrades -> trade-level CVD per minute, large trades, consistency with kline taker volume
    if aggtrades:
        buy, sell = np.zeros(n), np.zeros(n)
        lbuy, lsell, cnt = np.zeros(n), np.zeros(n), np.zeros(n)
        prev_thr = {"v": np.inf}
        seen = np.zeros(n, bool)

        def h_tr(d, df):
            if not isinstance(df.columns[0], str):
                df.columns = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time",
                              "is_buyer_maker"][:df.shape[1]]
            t = _ms(df["transact_time"])
            q = df["quantity"].astype(float).to_numpy()
            p = df["price"].astype(float).to_numpy()
            mk = df["is_buyer_maker"].astype(str).str.lower().eq("true").to_numpy()
            j = np.searchsorted(grid, t, side="right") - 1
            ok = (j >= 0) & (j < n)
            j, q, p, mk = j[ok], q[ok], p[ok], mk[ok]
            notional = p * q
            large = notional >= prev_thr["v"]
            np.add.at(buy, j[~mk], q[~mk])
            np.add.at(sell, j[mk], q[mk])
            np.add.at(lbuy, j[~mk & large], q[~mk & large])
            np.add.at(lsell, j[mk & large], q[mk & large])
            np.add.at(cnt, j, 1)
            seen[np.unique(j)] = True
            prev_thr["v"] = float(np.quantile(notional, 0.99)) if len(notional) else np.inf
        miss = run_kind("aggTrades", h_tr, w=3)  # handled in day order: the large-trade threshold uses the previous day
        for k, v in (("agg_buy", buy), ("agg_sell", sell), ("agg_large_buy", lbuy), ("agg_large_sell", lsell),
                     ("agg_count", cnt)):
            cols[k] = np.where(seen, v, np.nan)
        both = seen & np.isfinite(cols["taker_buy_volume"])
        diff = np.abs(cols["agg_buy"][both] - cols["taker_buy_volume"][both])
        rel = diff / np.maximum(1e-12, cols["taker_buy_volume"][both])
        rep["sources"]["agg_trades"] = {**Q.check_series("cvd", sym, grid[seen], None, MIN, t0, t1).to_dict(),
                                        "missing_days": miss,
                                        "consistency_vs_kline_taker_buy": {
                                            "minutes_compared": int(both.sum()),
                                            "median_rel_diff": float(np.median(rel)) if len(rel) else None,
                                            "share_within_1pct": float((rel <= 0.01).mean()) if len(rel) else None}}
    rep["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cols, rep


GROUPS = {"base": ("open_time", "open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume", "mark",
                    "premium", "funding_settled", "funding_interval_h", "_funding_events"),
          "oi": ("oi", "oi_value", "top_ls_pos", "ls_accounts", "taker_ls_vol", "oi_age_ms"),
          "trades": ("agg_buy", "agg_sell", "agg_large_buy", "agg_large_sell", "agg_count")}


def save(cols: dict[str, np.ndarray], stem: str) -> list[str]:
    """One compressed .npz per column group (keeps every file well under git's 100 MB limit)."""
    out = []
    groups = dict(GROUPS)
    groups["book"] = tuple(k for k in cols if k.startswith("depth_") or k == "book_age_ms")
    for g, keys in groups.items():
        d = {k: (cols[k].astype(np.float32) if cols[k].dtype == np.float64 and not k.startswith("_") else cols[k])
             for k in keys if k in cols}
        if d:
            path = f"{stem}.{g}.npz"
            np.savez_compressed(path, **d)
            out.append(path)
    return out


def load(stem: str) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for g in ("base", "oi", "book", "trades"):
        p = f"{stem}.{g}.npz"
        if os.path.exists(p):
            with np.load(p) as z:
                out.update({k: z[k] for k in z.files})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", default="2024-12-01")
    p.add_argument("--end", default="2026-06-30")
    p.add_argument("--kline-dir", default=os.environ.get("MX_DATA_DIR", "data/klines"))
    p.add_argument("--out", required=True, help="path of the quality JSON; the .npz is written next to it")
    p.add_argument("--no-aggtrades", action="store_true")
    a = p.parse_args()
    cols, rep = build_symbol(a.symbol, date.fromisoformat(a.start), date.fromisoformat(a.end), a.kline_dir,
                             aggtrades=not a.no_aggtrades)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    save(cols, a.out.replace(".json", ""))
    json.dump(rep, open(a.out, "w"), indent=1, default=str)
    print(json.dumps({k: (v.get("status"), v.get("coverage")) for k, v in rep["sources"].items()}, default=str))


if __name__ == "__main__":
    main()
