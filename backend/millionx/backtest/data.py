"""Historical data: Binance public archive (data.binance.vision) loader and a
synthetic generator used ONLY for software tests (never for performance claims).
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import date
from typing import Iterable, Optional

import numpy as np

from ..models import Bars, MINUTE_MS

ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{y:04d}-{m:02d}.zip"


def months_between(start: date, end: date) -> Iterable[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def download_month(sym: str, y: int, m: int, out_dir: str, session=None) -> Optional[str]:
    """Download one monthly 1m kline zip. Runs on the user's server (needs
    internet). Returns the local path or None if the month is not published."""
    import requests  # local import: not needed for backtests on local files
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{sym}-1m-{y:04d}-{m:02d}.zip")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    s = session or requests
    r = s.get(ARCHIVE.format(sym=sym, y=y, m=m), timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with open(path, "wb") as fh:
        fh.write(r.content)
    return path


def read_zip(path: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        text = z.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].isdigit():  # newer files carry a header
        rows = rows[1:]
    return rows


def load_symbol(sym: str, data_dir: str, start: date, end: date) -> Bars:
    rows: list[list[str]] = []
    for y, m in months_between(start, end):
        p = os.path.join(data_dir, sym, f"{sym}-1m-{y:04d}-{m:02d}.zip")
        if os.path.exists(p):
            rows.extend(read_zip(p))
    if not rows:
        raise FileNotFoundError(f"no local data for {sym} in {data_dir}/{sym}; run `python -m millionx.cli download`")
    b = Bars.from_rows(rows)
    # some archives use microseconds from 2025 on
    if len(b) and b.open_time[0] > 10 ** 14:
        b.open_time = b.open_time // 1000
    order = np.argsort(b.open_time, kind="stable")
    _, uniq = np.unique(b.open_time[order], return_index=True)
    idx = order[uniq]
    return Bars(*(getattr(b, k)[idx] for k in ("open_time", "open", "high", "low", "close", "volume",
                                                "taker_buy_volume", "quote_volume")))


def synthetic_bars(n: int = 20_000, seed: int = 7, start_ms: int = 1_700_000_000_000 // MINUTE_MS * MINUTE_MS,
                   price: float = 100.0, regime_len: int = 3000) -> Bars:
    """Random-walk bars with alternating drift/volatility regimes. FOR TESTS
    ONLY: there is no exploitable edge by construction, so a correct engine
    should NOT show reliable profits on it after fees."""
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    vol = np.zeros(n)
    for k in range(0, n, regime_len):
        drift[k:k + regime_len] = rng.choice([-1, 0, 1]) * 2e-5
        vol[k:k + regime_len] = rng.choice([4e-4, 8e-4, 1.6e-3])
    ret = drift + vol * rng.standard_t(4, n) / np.sqrt(2)
    close = price * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[price], close[:-1]])
    wig = np.abs(rng.normal(0, 1, n)) * vol * close
    high = np.maximum(open_, close) + wig
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1, n)) * vol * close
    volume = rng.lognormal(3, 0.6, n) * (1 + 50 * np.abs(ret))
    tb = volume * np.clip(0.5 + ret / (4 * vol) + rng.normal(0, 0.08, n), 0.02, 0.98)
    ot = start_ms + np.arange(n, dtype=np.int64) * MINUTE_MS
    return Bars(ot, open_, high, low, close, volume, tb, volume * close)
