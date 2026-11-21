"""Pull-based authorised signal sources (no inbound webhook needed).

A phone has no public address, so webhooks cannot reach it. Instead the engine polls a SOURCE
account the owner is authorised to read - their own Binance futures account / sub-account,
their MT5 terminal (via the bridge) or a cTrader account granted through OAuth - and turns
position changes into signals:

    new position            -> OPEN   (waits up to SL_GRACE_S for the stop-loss to appear)
    position gone           -> CLOSE
    quantity reduced        -> PARTIAL_CLOSE (fraction of the previous size)
    stop-loss / TP changed  -> MODIFY
    quantity increased      -> ignored (TITAN never adds to positions) and logged

Positions that already exist when a source is first connected are taken as a baseline and are
NOT copied (they would be stale entries). The last snapshot is persisted, so a restart does not
replay old positions.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import after_commit, session_scope
from ..markets import forex as fx
from ..models import CopyCapability, Trader
from ..services import state as st
from .signals import SignalIn, ingest_signal, publish

log = logging.getLogger("titan.sources")
SL_GRACE_S = 5.0
SOURCE_TYPES = {"binance_usdm", "binance_coinm", "mt5", "ctrader"}


def _norm(sym: str, forex: bool) -> str:
    try:
        return fx.normalize_symbol(sym) if forex else sym.upper()
    except Exception:
        return sym.upper()


class PositionSource:
    """fetch() -> {ref: {"symbol", "side", "qty", "entry", "sl", "tp"}}"""
    forex = False

    def fetch(self) -> dict[str, dict]:
        raise NotImplementedError


class BinanceFuturesSource(PositionSource):
    def __init__(self, client):
        self.c = client

    def fetch(self):
        out = {}
        for p in self.c.position_risk():
            amt = float(p.get("positionAmt", 0) or 0)
            if amt:
                out[p["symbol"]] = {"symbol": p["symbol"], "side": "buy" if amt > 0 else "sell", "qty": abs(amt),
                                    "entry": float(p.get("entryPrice") or 0), "sl": None, "tp": None}
        if not out:
            return out
        # protective orders: classic conditional orders and (USD-M) algo orders
        orders = []
        try:
            orders += self.c.open_orders()
        except Exception as e:
            log.debug("open orders unavailable: %s", e)
        if self.c.product == "usdm":
            try:
                orders += self.c.request("GET", "/fapi/v1/openAlgoOrders", signed=True) or []
            except Exception as e:
                log.debug("open algo orders unavailable: %s", e)
        for o in orders:
            pos = out.get(o.get("symbol"))
            if not pos:
                continue
            typ = o.get("type") or o.get("orderType") or ""
            trig = float(o.get("triggerPrice") or o.get("stopPrice") or 0) or None
            exit_side = "SELL" if pos["side"] == "buy" else "BUY"
            if trig is None or o.get("side") != exit_side:
                continue
            if typ.startswith("STOP"):
                pos["sl"] = trig
            elif typ.startswith("TAKE_PROFIT"):
                pos["tp"] = trig
        return out


class AdapterSource(PositionSource):
    """MT5 bridge or cTrader adapter used read-only as a source."""

    def __init__(self, adapter, forex: bool = True):
        self.ad, self.forex = adapter, forex

    def fetch(self):
        return {p.ref: {"symbol": p.symbol, "side": p.side, "qty": p.quantity, "entry": p.entry_price,
                        "sl": p.sl or None, "tp": p.tp or None} for p in self.ad.positions()}


class SourceTracker:
    """Diffs successive snapshots of one source account into signals."""

    def __init__(self, trader_id: int, source: PositionSource, now=time.time):
        self.trader_id, self.source, self.now = trader_id, source, now
        self.key = f"source:{trader_id}"

    def _load(self) -> dict:
        with session_scope() as s:
            return st.get(s, self.key, {})

    def _save(self, snap: dict):
        with session_scope() as s:
            st.put(s, self.key, snap)

    def poll(self) -> list[int]:
        cur = self.source.fetch()
        state = self._load()
        if "positions" not in state:     # first connection: baseline, do not copy existing positions
            self._save({"positions": cur, "pending": {}, "since": datetime.now(timezone.utc).isoformat()})
            log.info("source %s baseline: %d existing positions not copied", self.trader_id, len(cur))
            return []
        prev, pending = state["positions"], state.get("pending", {})
        signals: list[tuple[str, dict]] = []
        known = {}
        now = self.now()
        for ref, p in cur.items():
            old = prev.get(ref)
            if old is None:
                first = pending.get(ref, now)
                if p["sl"] is None and now - first < SL_GRACE_S:
                    pending[ref] = first          # give the trader a few seconds to attach the stop
                    continue
                pending.pop(ref, None)
                signals.append(("open", {"ref": ref, **p, "detected": first}))
                known[ref] = p
                continue
            known[ref] = p
            if p["qty"] < old["qty"] * 0.999:
                signals.append(("partial_close", {"ref": ref, **p, "fraction": 1 - p["qty"] / old["qty"]}))
            elif p["qty"] > old["qty"] * 1.001:
                log.info("source %s added to %s (ignored: no pyramiding)", self.trader_id, ref)
            if (p["sl"] != old.get("sl") and p["sl"] is not None) or (p["tp"] != old.get("tp") and p["tp"] is not None):
                signals.append(("modify", {"ref": ref, **p}))
        for ref, old in prev.items():
            if ref not in cur:
                signals.append(("close", {"ref": ref, **old}))
        for ref in list(pending):
            if ref not in cur:
                pending.pop(ref)
        ids = self._emit(signals)
        self._save({"positions": known, "pending": pending, "since": state.get("since")})
        return ids

    def _emit(self, signals: list[tuple[str, dict]]) -> list[int]:
        ids = []
        for kind, p in signals:
            ts = datetime.fromtimestamp(p.get("detected") or self.now(), timezone.utc)
            data = {"trader_id": self.trader_id, "kind": kind, "source_position_id": str(p["ref"]),
                    "symbol": _norm(p["symbol"], self.source.forex), "source_ts": ts,
                    # each change is a distinct event (sl/tp/qty part of the id)
                    "signal_id": f"{p['ref']}:{kind}:{p['qty']}:{p.get('sl')}:{p.get('tp')}:{int(self.now())}"}
            if kind == "open":
                data.update(side=p["side"], entry_price=p["entry"] or None, sl=p["sl"], tp=p["tp"],
                            source_volume=p["qty"])
            elif kind == "partial_close":
                data["close_fraction"] = round(min(1.0, max(0.0001, p["fraction"])), 6)
            elif kind == "modify":
                data.update(sl=p["sl"], tp=p["tp"])
            try:
                sig = SignalIn.model_validate(data)
            except ValueError as e:
                log.warning("source %s produced an invalid %s signal: %s", self.trader_id, kind, e)
                continue
            with session_scope() as s:
                row, created = ingest_signal(s, sig, f"source:{self.trader_id}", payload={"source_position": p})
                if created:
                    rid = row.id
                    after_commit(lambda rid=rid: publish(rid))
                    ids.append(rid)
        return ids


def build_source(trader: Trader, credential: dict, venue: str) -> PositionSource:
    if venue in ("binance_usdm", "binance_coinm"):
        from ..adapters.binance.client import BinanceClient
        env = trader.signal_source.get("environment", "demo")
        return BinanceFuturesSource(BinanceClient(venue.split("_", 1)[1], env, credential["api_key"],
                                                  credential["api_secret"]))
    if venue == "mt5":
        from ..adapters.forex.mt5 import MT5BridgeAdapter
        return AdapterSource(MT5BridgeAdapter(credential["url"], credential["secret"]))
    if venue == "ctrader":
        from ..adapters.forex.ctrader import CTraderAdapter, CTraderConnection
        env = trader.signal_source.get("environment", "demo")
        ad = CTraderAdapter(CTraderConnection(env, credential["client_id"], credential["client_secret"],
                                              credential["access_token"], int(credential["account_id"])), env)
        from ..services.venues import FOREX_SYMBOLS
        ad.connect(FOREX_SYMBOLS)
        return AdapterSource(ad)
    raise ValueError(f"{venue} cannot be used as a signal source")


def active_source_traders() -> list[tuple[int, int, str]]:
    """(trader_id, credential_id, venue) for traders wired to an owner account."""
    with session_scope() as s:
        out = []
        for t in s.scalars(select(Trader).where(Trader.copy_capability == CopyCapability.AUTHORIZED_SIGNALS)):
            src = t.signal_source or {}
            if src.get("type") in SOURCE_TYPES and src.get("credential_id"):
                out.append((t.id, int(src["credential_id"]), src["type"]))
        return out
