"""Trader data sources.

Only documented, official access paths are used:

| Platform              | Programmatic access used here                         | Copyable here? |
|-----------------------|-------------------------------------------------------|----------------|
| Myfxbook              | Myfxbook API (www.myfxbook.com/api/*.json, session)   | monitor only   |
| Darwinex              | DARWIN Info API (api.darwinex.com/darwininfo/<ver>)   | monitor only   |
| cTrader (Open API)    | accounts the owner authorised via OAuth               | authorised     |
| MetaTrader 5          | owner's terminal via self-hosted MT5 bridge           | authorised     |
| ZuluTrade             | no public API documented -> manual import             | monitor only   |
| MQL5 Signals          | no public API (copying only inside MT4/MT5 terminal)  | monitor only   |
| cTrader Copy listing  | no public strategy-listing API -> manual import       | monitor only   |

A publicly visible account is never treated as copyable. Copying requires an authorised signal
source (owner-controlled account or a provider that signs signals with an HMAC secret).
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

from ..analytics.metrics import EquityRow, TradeRow
from .._util import parse_dt
from ..services.http import RateLimiter, with_retries

log = logging.getLogger("titan.discovery")


@dataclass
class TraderData:
    platform: str
    external_id: str
    name: str
    source: str                      # data source tag (official api / manual_import)
    broker: str | None = None
    account_type: str | None = None  # real / demo
    description: str | None = None
    instruments: list[str] = field(default_factory=list)
    source_url: str | None = None
    trades: list[TradeRow] = field(default_factory=list)
    curve: list[EquityRow] = field(default_factory=list)
    published: dict = field(default_factory=dict)
    open_positions: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    note: str | None = None


class SourceUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------------------------ Myfxbook
class MyfxbookSource:
    """Official Myfxbook API. Methods used: login, logout, get-my-accounts, get-watched-accounts,
    get-open-trades, get-history, get-daily-gain (session-based, JSON variants *.json).

    Limitations (documented by Myfxbook): get-history returns only the most recent trades
    (last 50), timestamps are in the broker's local time zone, and the published 'drawdown'
    is Myfxbook's own maximum drawdown figure (stored as reported, treated as balance-type
    drawdown by the ranking engine because the equity/balance split is not exposed).
    """
    BASE = "https://www.myfxbook.com/api"
    platform = "myfxbook"

    def __init__(self, email: str, password: str, client: httpx.Client | None = None):
        self.email, self.password = email, password
        self.http = client or httpx.Client(timeout=20)
        self.session: str | None = None
        self.limiter = RateLimiter(1.0, 2)

    def _call(self, method: str, **params) -> dict:
        self.limiter.acquire()
        r = with_retries(lambda: self.http.get(f"{self.BASE}/{method}.json", params=params))
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise SourceUnavailable(f"myfxbook {method}: {data.get('message')}")
        return data

    def login(self) -> None:
        self.session = self._call("login", email=self.email, password=self.password)["session"]

    def _s(self) -> str:
        if not self.session:
            self.login()
        assert self.session
        return self.session

    def logout(self) -> None:
        if self.session:
            try:
                self._call("logout", session=self.session)
            finally:
                self.session = None

    def list_accounts(self) -> list[dict]:
        own = self._call("get-my-accounts", session=self._s()).get("accounts", [])
        watched = self._call("get-watched-accounts", session=self._s()).get("accounts", [])
        for a in own:
            a["_origin"] = "my-account"
        for a in watched:
            a["_origin"] = "watched"
        return own + watched

    def fetch(self, account: dict, days: int = 400) -> TraderData:
        acc_id = str(account.get("id"))
        name = account.get("name") or f"myfxbook-{acc_id}"
        td = TraderData(platform="myfxbook", external_id=acc_id, name=name, source="myfxbook_api",
                        description=account.get("description"),
                        account_type="demo" if account.get("demo") else "real" if "demo" in account else None,
                        broker=(account.get("server") or {}).get("name") if isinstance(account.get("server"), dict) else None,
                        raw={"account": account})
        pub = {}
        if account.get("drawdown") is not None:
            pub["max_balance_drawdown_pct"] = float(account["drawdown"])
        if account.get("profitFactor") is not None:
            pub["profit_factor"] = float(account["profitFactor"])
        td.published = pub
        if account.get("_origin") == "watched":
            td.note = "watched account: Myfxbook exposes summary fields only; trades/curve require owner access"
            return td
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        dg = self._call("get-daily-gain", session=self._s(), id=acc_id, start=start.isoformat(), end=end.isoformat())
        for row in _flatten(dg.get("dailyGain", [])):
            ts = parse_dt(row.get("date"), fmt="%m/%d/%Y")
            if ts is not None and row.get("value") is not None:
                td.curve.append(EquityRow(ts=ts, growth_pct=float(row["value"])))
        hist = self._call("get-history", session=self._s(), id=acc_id).get("history", [])
        for i, h in enumerate(hist):
            td.trades.append(TradeRow(
                symbol=h.get("symbol", ""), side=_side(h.get("action")), volume=_f(h.get("sizing", {}).get("value"))
                if isinstance(h.get("sizing"), dict) else _f(h.get("lots")),
                open_time=parse_dt(h.get("openTime"), fmt="%m/%d/%Y %H:%M"),
                close_time=parse_dt(h.get("closeTime"), fmt="%m/%d/%Y %H:%M"),
                open_price=_f(h.get("openPrice")), close_price=_f(h.get("closePrice")),
                profit=_f(h.get("profit")), sl=_f(h.get("sl")), commission=_f(h.get("commission")),
                swap=_f(h.get("interest"))))
        td.trades = [t for t in td.trades if t.open_time is not None]
        ot = self._call("get-open-trades", session=self._s(), id=acc_id).get("openTrades", [])
        td.open_positions = ot
        td.published["current_positions"] = len(ot)
        if len(hist) >= 50:
            td.note = "Myfxbook API returns only the latest trades; trade statistics cover that window"
        return td


# ------------------------------------------------------------------------------------ Darwinex
class DarwinexInfoSource:
    """DARWIN Info API. Paths as used by Darwinex's official `darwinexapis` package:
    GET /products?status=&page=&per_page=, /products/{name}/history/quotes,
    /products/{name}/history/badges, /products/{name}/candles/{tf}. Bearer token (data-API
    tokens last ~6 months). Info API rate limit: 10 requests/minute.

    Note: DARWIN quotes are Darwinex's risk-normalised index of a strategy, not the raw
    trader account; drawdown derived from them is stored as DARWIN (index) drawdown.
    """
    platform = "darwinex"

    def __init__(self, token: str, version: str = "2.0", client: httpx.Client | None = None):
        self.base = f"https://api.darwinex.com/darwininfo/{version}"
        self.http = client or httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"})
        self.limiter = RateLimiter(10 / 60, 2)

    def _get(self, path: str, **params):
        self.limiter.acquire()
        r = with_retries(lambda: self.http.get(self.base + path, params=params or None))
        if r.status_code in (401, 403):
            raise SourceUnavailable("Darwinex token rejected (401/403)")
        r.raise_for_status()
        return r.json()

    def list_universe(self, status: str = "ACTIVE", max_pages: int = 5) -> list[dict]:
        out, page = [], 0
        while page < max_pages:
            d = self._get("/products", status=status, page=page, per_page=50)
            out += d.get("content", [])
            if page + 1 >= int(d.get("totalPages", 1)):
                break
            page += 1
        return out

    def fetch(self, product: str) -> TraderData:
        td = TraderData(platform="darwinex", external_id=product, name=product, source="darwinex_info_api",
                        broker="Darwinex", source_url=f"https://www.darwinex.com/darwin/{product.split('.')[0]}")
        quotes = self._get(f"/products/{product}/history/quotes")
        rows = quotes if isinstance(quotes, list) else quotes.get("quotes", [])
        for q in rows:
            ts_ms, val = (q[0], q[1]) if isinstance(q, (list, tuple)) else (q.get("timestamp"), q.get("quote"))
            if ts_ms is None or val is None:
                continue
            td.curve.append(EquityRow(ts=datetime.fromtimestamp(ts_ms / 1000, timezone.utc),
                                      growth_pct=(float(val) / 100 - 1) * 100))
        try:
            td.raw["badges"] = self._get(f"/products/{product}/history/badges")
        except Exception as e:  # badges are optional
            td.raw["badges_error"] = str(e)
        td.note = "DARWIN quotes are Darwinex's risk-adjusted index (not the underlying account)."
        return td


# ------------------------------------------------------------------------------------ manual import
MYFXBOOK_CSV = {"open date": "open_time", "close date": "close_time", "symbol": "symbol", "action": "side",
                "units/lots": "volume", "lots": "volume", "sl": "sl", "tp": "tp", "open price": "open_price",
                "close price": "close_price", "commission": "commission", "swap": "swap", "profit": "profit"}
GENERIC = {"ticket": "ticket", "id": "ticket", "symbol": "symbol", "side": "side", "type": "side",
           "volume": "volume", "lots": "volume", "open_time": "open_time", "close_time": "close_time",
           "open_price": "open_price", "close_price": "close_price", "sl": "sl", "tp": "tp",
           "profit": "profit", "commission": "commission", "swap": "swap", "time": "open_time",
           "price": "open_price", "s / l": "sl", "t / p": "tp", "open time": "open_time",
           "close time": "close_time"}


def parse_trade_csv(text: str) -> list[TradeRow]:
    """Import a trade history CSV (Myfxbook export or generic/MT5-style columns)."""
    sample = text[:2048]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    out = []
    for row in reader:
        rec: dict = {}
        for k, v in row.items():
            if k is None:
                continue
            key = k.strip().lower()
            tgt = MYFXBOOK_CSV.get(key) or GENERIC.get(key)
            if tgt and v not in (None, ""):
                rec.setdefault(tgt, v.strip())
        side = _side(rec.get("side"))
        if side not in ("buy", "sell"):
            continue  # deposits, withdrawals, balance operations
        ot = parse_dt(rec.get("open_time"))
        if ot is None:
            continue
        out.append(TradeRow(symbol=rec.get("symbol", "").upper(), side=side, volume=_f(rec.get("volume")),
                            open_time=ot, close_time=parse_dt(rec.get("close_time")),
                            open_price=_f(rec.get("open_price")), close_price=_f(rec.get("close_price")),
                            profit=_f(rec.get("profit")), sl=_f(rec.get("sl")),
                            commission=_f(rec.get("commission")), swap=_f(rec.get("swap"))))
    return out


def parse_curve_csv(text: str) -> list[EquityRow]:
    """Columns: date/time, and any of balance, equity, growth (cumulative %)."""
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        ts = parse_dt(r.get("date") or r.get("time") or r.get("ts"))
        if ts is None:
            continue
        out.append(EquityRow(ts=ts, balance=_f(r.get("balance")), equity=_f(r.get("equity")),
                             growth_pct=_f(r.get("growth") or r.get("growth_pct"))))
    return out


# ------------------------------------------------------------------------------------ helpers
def _flatten(x):
    for i in x:
        if isinstance(i, list):
            yield from _flatten(i)
        else:
            yield i


def _side(v) -> str:
    s = str(v or "").strip().lower()
    if s.startswith("buy") or s in ("long", "b", "0"):
        return "buy"
    if s.startswith("sell") or s in ("short", "s", "1"):
        return "sell"
    return s


def _f(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", ""))
    except ValueError:
        return None


def json_or_none(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


__all__ = ["TraderData", "MyfxbookSource", "DarwinexInfoSource", "parse_trade_csv", "parse_curve_csv",
           "SourceUnavailable", "date"]
