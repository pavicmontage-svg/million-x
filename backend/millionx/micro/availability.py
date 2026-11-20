"""Data availability audit (2.1, phase A): what Binance REALLY publishes, checked by request,
not assumed. Run on CI (needs internet):

  python -m millionx.micro.availability --out availability.json

For every symbol and dataset it requests the public archive (data.binance.vision) for sample
days across 2024-2026 and records HTTP status and size; for one day per dataset it downloads
the file and records the header, row count and median time step. It also probes the REST
endpoints (which may be geo-blocked from the CI region: the HTTP status is recorded as is).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import zipfile
from datetime import date
from typing import Any

BASE = "https://data.binance.vision/data/futures/um"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT",
           "AVAXUSDT", "LTCUSDT"]
DAILY = {  # dataset -> url template (daily)
    "metrics": "{b}/daily/metrics/{s}/{s}-metrics-{d}.zip",
    "bookDepth": "{b}/daily/bookDepth/{s}/{s}-bookDepth-{d}.zip",
    "bookTicker": "{b}/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip",
    "aggTrades": "{b}/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip",
    "trades": "{b}/daily/trades/{s}/{s}-trades-{d}.zip",
    "liquidationSnapshot": "{b}/daily/liquidationSnapshot/{s}/{s}-liquidationSnapshot-{d}.zip",
    "premiumIndexKlines_1m": "{b}/daily/premiumIndexKlines/{s}/1m/{s}-1m-{d}.zip",
    "markPriceKlines_1m": "{b}/daily/markPriceKlines/{s}/1m/{s}-1m-{d}.zip",
    "klines_1m": "{b}/daily/klines/{s}/1m/{s}-1m-{d}.zip",
}
MONTHLY = {
    "fundingRate": "{b}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip",
    "aggTrades_monthly": "{b}/monthly/aggTrades/{s}/{s}-aggTrades-{m}.zip",
}
SAMPLE_DAYS = ["2024-01-15", "2024-03-15", "2024-06-15", "2025-01-15", "2025-06-15", "2025-12-15",
               "2026-03-15", "2026-06-15", "2026-11-15"]
SAMPLE_MONTHS = ["2024-01", "2025-01", "2025-06", "2026-01", "2026-06", "2026-10"]
REST = {
    "fundingRate": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime=1735689600000&limit=5",
    "openInterestHist_old": "https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=5&startTime=1735689600000",
    "openInterestHist_recent": "https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=5",
    "aggTrades_old": "https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&startTime=1735689600000&endTime=1735690000000&limit=5",
    "depth": "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=20",
}


def _head(sess, url: str) -> dict[str, Any]:
    try:
        r = sess.head(url, timeout=30, allow_redirects=True)
        return {"status": r.status_code, "bytes": int(r.headers.get("Content-Length") or 0)}
    except Exception as e:  # network errors are data too
        return {"status": "error", "error": str(e)[:120]}


def _inspect(sess, url: str) -> dict[str, Any]:
    r = sess.get(url, timeout=120)
    if r.status_code != 200:
        return {"status": r.status_code}
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = z.namelist()[0]
    rows = list(csv.reader(io.TextIOWrapper(z.open(name), "utf-8")))
    header = rows[0] if rows and not rows[0][0].replace(".", "").replace("-", "").isdigit() else None
    data = rows[1:] if header else rows
    ts = []
    for row in data[:20000]:
        for c in row:  # first column that looks like a ms timestamp or a datetime
            if c.isdigit() and len(c) >= 13:
                ts.append(int(c[:13]))
                break
            if len(c) == 19 and c[4] == "-" and c[10] == " ":
                ts.append(int(time.mktime(time.strptime(c, "%Y-%m-%d %H:%M:%S")) * 1000))
                break
    steps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    return {"status": 200, "zip_bytes": len(r.content), "header": header, "rows": len(data),
            "first_row": data[0][:12] if data else None,
            "median_step_ms": steps[len(steps) // 2] if steps else None}


def main() -> None:
    import requests
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    a = p.parse_args()
    sess = requests.Session()
    out: dict[str, Any] = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "daily": {},
                           "monthly": {}, "inspect": {}, "rest": {}}
    for ds, tpl in DAILY.items():
        out["daily"][ds] = {s: {d: _head(sess, tpl.format(b=BASE, s=s, d=d)) for d in SAMPLE_DAYS} for s in SYMBOLS}
        print(ds, {d: out["daily"][ds]["BTCUSDT"][d]["status"] for d in SAMPLE_DAYS}, flush=True)
    for ds, tpl in MONTHLY.items():
        out["monthly"][ds] = {s: {m: _head(sess, tpl.format(b=BASE, s=s, m=m)) for m in SAMPLE_MONTHS} for s in SYMBOLS}
        print(ds, {m: out["monthly"][ds]["BTCUSDT"][m]["status"] for m in SAMPLE_MONTHS}, flush=True)
    for ds, tpl in DAILY.items():  # inspect the newest available sample day for BTC
        for d in reversed(SAMPLE_DAYS):
            if out["daily"][ds]["BTCUSDT"][d].get("status") == 200:
                if ds in ("trades",):
                    out["inspect"][ds] = {"day": d, "skipped": "too large to inspect; status/size recorded"}
                else:
                    out["inspect"][ds] = {"day": d, **_inspect(sess, tpl.format(b=BASE, s="BTCUSDT", d=d))}
                break
        else:
            # find the last day it existed (liquidationSnapshot stopped in 2024 per binance-public-data #337)
            out["inspect"][ds] = {"status": "no sample day available"}
    for m in reversed(SAMPLE_MONTHS):
        if out["monthly"]["fundingRate"]["BTCUSDT"][m].get("status") == 200:
            out["inspect"]["fundingRate"] = {"month": m, **_inspect(sess, MONTHLY["fundingRate"].format(b=BASE, s="BTCUSDT", m=m))}
            break
    for k, url in REST.items():
        try:
            r = sess.get(url, timeout=20)
            out["rest"][k] = {"status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            out["rest"][k] = {"status": "error", "error": str(e)[:120]}
    json.dump(out, open(a.out, "w"), indent=1)
    open(a.out.replace(".json", ".md"), "w").write(to_markdown(out))
    print(to_markdown(out))


def to_markdown(o: dict[str, Any]) -> str:
    L = [f"# Data availability probe ({o['checked_at']})", "", "## Daily archives (data.binance.vision), all 10 symbols", "",
         "| dataset | " + " | ".join(SAMPLE_DAYS) + " |", "|---|" + "---|" * len(SAMPLE_DAYS)]
    for ds, per in o["daily"].items():
        cells = []
        for d in SAMPLE_DAYS:
            ok = sum(1 for s in per if per[s][d].get("status") == 200)
            cells.append(f"{ok}/{len(per)}")
        L.append(f"| {ds} | " + " | ".join(cells) + " |")
    L += ["", "## Monthly archives", "", "| dataset | " + " | ".join(SAMPLE_MONTHS) + " |", "|---|" + "---|" * len(SAMPLE_MONTHS)]
    for ds, per in o["monthly"].items():
        L.append(f"| {ds} | " + " | ".join(f"{sum(1 for s in per if per[s][m].get('status') == 200)}/{len(per)}"
                                            for m in SAMPLE_MONTHS) + " |")
    L += ["", "## File contents (BTCUSDT, newest available sample)", ""]
    for ds, v in o["inspect"].items():
        L.append(f"- **{ds}**: {json.dumps(v)[:600]}")
    L += ["", "## REST endpoints (from the CI runner's region)", ""]
    for k, v in o["rest"].items():
        L.append(f"- **{k}**: HTTP {v.get('status')} — `{(v.get('body') or v.get('error') or '')[:160]}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
