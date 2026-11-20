"""Historical data: Binance public archive (data.binance.vision) loader and a
synthetic generator used ONLY for software tests (never for performance claims).
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import date, timedelta
from typing import Callable, Iterable, Optional

import numpy as np

from ..models import Bars, MINUTE_MS

ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{y:04d}-{m:02d}.zip"
DAILY = "https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{d}.zip"


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


def download_day(sym: str, day: date, out_dir: str, session=None) -> Optional[str]:
    """One daily 1m kline zip (used for the current month, before the monthly file exists)."""
    import requests
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{sym}-1m-{day.isoformat()}.zip")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    s = session or requests
    r = s.get(DAILY.format(sym=sym, d=day.isoformat()), timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with open(path, "wb") as fh:
        fh.write(r.content)
    return path


def ensure_data(sym: str, data_dir: str, start: date, end: date,
                progress: Optional[Callable[[str], None]] = None, today: Optional[date] = None) -> dict[str, int]:
    """Download whatever is missing for [start, end] from data.binance.vision:
    monthly archives for finished months, daily archives where the monthly file
    is not published yet. Nothing is synthesised; missing days stay missing."""
    import requests
    today = today or date.today()
    last_day = min(end, today - timedelta(days=1))
    out = os.path.join(data_dir, sym)
    sess = requests.Session()
    got = {"monthly": 0, "daily": 0, "missing": 0}
    months = list(months_between(start, last_day)) if last_day >= start else []
    for k, (y, m) in enumerate(months):
        if progress:
            progress(f"downloading {sym} {y:04d}-{m:02d} ({k + 1}/{len(months)})")
        month_done = (y, m) < (today.year, today.month)
        if month_done and download_month(sym, y, m, out, sess):
            got["monthly"] += 1
            continue
        d = max(start, date(y, m, 1))
        while d <= last_day and d.month == m:
            if download_day(sym, d, out, sess):
                got["daily"] += 1
            else:
                got["missing"] += 1
            d += timedelta(days=1)
    return got


def _zip_array(path: str) -> np.ndarray:
    """Columns: open_time, open, high, low, close, volume, taker_buy_volume, quote_volume."""
    with zipfile.ZipFile(path) as z:
        text = z.read(z.namelist()[0]).decode()
    skip = 0 if text[:1].isdigit() else 1  # newer files carry a header
    a = np.loadtxt(io.StringIO(text), delimiter=",", skiprows=skip, usecols=(0, 1, 2, 3, 4, 5, 9, 7), ndmin=2)
    return a


def read_zip(path: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        text = z.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].isdigit():  # newer files carry a header
        rows = rows[1:]
    return rows


def load_symbol(sym: str, data_dir: str, start: date, end: date) -> Bars:
    parts: list[np.ndarray] = []
    folder = os.path.join(data_dir, sym)
    for y, m in months_between(start, end):
        p = os.path.join(folder, f"{sym}-1m-{y:04d}-{m:02d}.zip")
        files = [p] if os.path.exists(p) else sorted(
            os.path.join(folder, f) for f in (os.listdir(folder) if os.path.isdir(folder) else [])
            if f.startswith(f"{sym}-1m-{y:04d}-{m:02d}-") and f.endswith(".zip"))
        for f in files:
            a = _zip_array(f)
            if len(a):
                if a[0, 0] > 10 ** 14:  # some archives use microseconds from 2025 on
                    a[:, 0] = a[:, 0] // 1000
                parts.append(a)
    if not parts:
        raise FileNotFoundError(f"no historical data for {sym} between {start} and {end} "
                                "(data.binance.vision has nothing for this range)")
    a = np.concatenate(parts)
    b = Bars(a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], a[:, 7])
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
