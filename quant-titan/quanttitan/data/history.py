"""Historical data from Binance's public archive (data.binance.vision).

Downloads monthly (and, for the current month, daily) USDT-M futures kline and funding-rate archives,
verifies them, and caches them as compressed ``.npz`` files. The archive is the same data Binance
serves through the REST API, and it is reachable from locations where the REST API is not.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import zipfile
from pathlib import Path

import httpx
import numpy as np

from ..core.logging import get_logger
from .candles import Candles, check_integrity

log = get_logger("data.history")
ARCHIVE = "https://data.binance.vision/data/futures/um"


def _months(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _parse_kline_csv(data: bytes) -> list[list]:
    rows = []
    for r in csv.reader(io.StringIO(data.decode())):
        if not r or not r[0].strip().lstrip("-").isdigit():
            continue  # header
        rows.append(r)
    return rows


def _fetch_zip(client: httpx.Client, url: str) -> bytes | None:
    r = client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return z.read(z.namelist()[0])


class HistoryStore:
    def __init__(self, root: str | os.PathLike = "data/history") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str, tf: str) -> Path:
        return self.root / f"{symbol}_{tf}.npz"

    def funding_path(self, symbol: str) -> Path:
        return self.root / f"{symbol}_funding.npz"

    def load(self, symbol: str, tf: str) -> Candles | None:
        p = self.path(symbol, tf)
        return Candles.from_npz(str(p)) if p.exists() else None

    def load_funding(self, symbol: str) -> tuple[np.ndarray, np.ndarray] | None:
        p = self.funding_path(symbol)
        if not p.exists():
            return None
        d = np.load(p)
        return d["ts"], d["rate"]

    def download(self, symbol: str, tf: str, start: dt.date, end: dt.date, *, funding: bool = True,
                 timeout: float = 60.0) -> Candles:
        """Download [start, end] (inclusive dates, UTC), merge with any cached data and save."""
        cached = self.load(symbol, tf) or Candles(symbol, tf)
        today = dt.datetime.now(dt.timezone.utc).date()
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for y, m in _months(start, end):
                month_start = dt.date(y, m, 1)
                complete_month = (y, m) < (today.year, today.month)
                got = None
                if complete_month:
                    url = f"{ARCHIVE}/monthly/klines/{symbol}/{tf}/{symbol}-{tf}-{y}-{m:02d}.zip"
                    got = _fetch_zip(client, url)
                if got is not None:
                    cached = cached.append(Candles.from_rows(symbol, tf, _parse_kline_csv(got)))
                    log.info("downloaded %s %s %d-%02d", symbol, tf, y, m)
                    continue
                # fall back to daily files (current month, or monthly file not yet published)
                d = max(month_start, start)
                last = min(end, (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)), today - dt.timedelta(days=1))
                while d <= last:
                    url = f"{ARCHIVE}/daily/klines/{symbol}/{tf}/{symbol}-{tf}-{d.isoformat()}.zip"
                    day = _fetch_zip(client, url)
                    if day is not None:
                        cached = cached.append(Candles.from_rows(symbol, tf, _parse_kline_csv(day)))
                    d += dt.timedelta(days=1)
            if funding:
                self._download_funding(client, symbol, start, end)
        rep = check_integrity(cached)
        if not rep.ok:
            log.warning("%s %s integrity: %s", symbol, tf, "; ".join(rep.issues))
        cached.to_npz(str(self.path(symbol, tf)))
        return cached

    def _download_funding(self, client: httpx.Client, symbol: str, start: dt.date, end: dt.date) -> None:
        ts, rate = [], []
        prev = self.load_funding(symbol)
        if prev is not None:
            ts.extend(prev[0].tolist()); rate.extend(prev[1].tolist())
        for y, m in _months(start, end):
            url = f"{ARCHIVE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip"
            data = _fetch_zip(client, url)
            if data is None:
                continue
            for r in csv.reader(io.StringIO(data.decode())):
                if r and r[0].isdigit():
                    ts.append(int(r[0])); rate.append(float(r[2]))
        if ts:
            a_ts, idx = np.unique(np.array(ts, dtype=np.int64), return_index=True)
            np.savez_compressed(self.funding_path(symbol), ts=a_ts, rate=np.array(rate)[idx])
