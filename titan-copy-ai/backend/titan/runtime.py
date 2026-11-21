"""TITAN engine process: the permanent execution and monitoring service.

Runs independently of the Android app. Consumes signals and commands from Redis Streams
(consumer groups -> at-least-once, survives restarts), drives the paper broker, reconciles
venues, monitors risk and writes a heartbeat. Start: `python -m titan.cli engine`.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from .api.deps import COMMANDS
from .copy.engine import CopyEngine, client_id
from .copy.monitor import flatten, monitor_portfolio
from .db import session_scope
from .events import SIGNALS, get_bus
from .models import Credential, Portfolio, Position, Signal
from .notify import notify
from .services import state as st
from .services.venues import FOREX_PAPER_FEED, build_adapter, credential

log = logging.getLogger("titan.engine")
GROUP = "titan-engine"


class Runtime:
    def __init__(self, consumer: str = "engine-1"):
        self.engine = CopyEngine()
        self.bus = get_bus()
        self.consumer = consumer
        self.stop = threading.Event()
        self.errors: dict[int, str] = {}

    # ------------------------------------------------------------------ adapters
    def load_adapters(self):
        with session_scope() as s:
            ports = list(s.scalars(select(Portfolio)))
            for p in ports:
                if p.id in self.engine.adapters:
                    continue
                try:
                    self.engine.adapters[p.id] = build_adapter(s, p)
                    self.errors.pop(p.id, None)
                    log.info("adapter ready: %s (%s/%s)", p.name, p.venue, p.mode.value)
                except Exception as e:
                    if self.errors.get(p.id) != str(e):
                        log.warning("adapter for %s unavailable: %s", p.name, e)
                        notify(s, "api_disconnected" if p.venue.startswith("binance") else "broker_disconnected",
                               f"{p.name}: adapter unavailable", str(e)[:300], severity="warning", portfolio_id=p.id)
                    self.errors[p.id] = str(e)

    # ------------------------------------------------------------------ loops
    def heartbeat(self):
        with session_scope() as s:
            st.put(s, st.ENGINE, {"status": "running", "heartbeat": datetime.now(timezone.utc).isoformat(),
                                  "consumer": self.consumer, "adapters": len(self.engine.adapters),
                                  "adapter_errors": {str(k): v[:200] for k, v in self.errors.items()}})

    def signals_loop(self):
        self.bus.ensure_group(SIGNALS, GROUP)
        while not self.stop.is_set():
            try:
                for mid, ev in self.bus.consume(SIGNALS, GROUP, self.consumer, count=10, block_ms=1000):
                    sid = int(ev["signal_id"])
                    self._feed_prices(sid)
                    self.engine.process(sid)
                    self.bus.ack(SIGNALS, GROUP, mid)
            except Exception:
                log.exception("signal loop error")
                time.sleep(1)

    def _feed_prices(self, sid: int):
        """Forex paper pricing from authorised signal payloads (real source prices)."""
        with session_scope() as s:
            g = s.get(Signal, sid)
            if g and g.market.value == "forex":
                bid, ask = (g.payload or {}).get("bid"), (g.payload or {}).get("ask")
                if bid and ask:
                    FOREX_PAPER_FEED.update(g.symbol, bid, ask, g.source_ts.timestamp())

    def commands_loop(self):
        self.bus.ensure_group(COMMANDS, GROUP)
        while not self.stop.is_set():
            try:
                for mid, cmd in self.bus.consume(COMMANDS, GROUP, self.consumer, count=10, block_ms=1000):
                    self.handle(cmd)
                    self.bus.ack(COMMANDS, GROUP, mid)
            except Exception:
                log.exception("command loop error")
                time.sleep(1)

    def handle(self, cmd: dict):
        kind = cmd.get("cmd")
        if kind == "close_position":
            self.engine.close_position(cmd["position_id"], client_id("manual", cmd["position_id"], time.time()),
                                       reason="manual_close")
        elif kind == "flatten":
            flatten(self.engine, cmd.get("portfolio_id"), "emergency_flatten", cmd.get("actor", "owner"))
        elif kind == "reload_adapters":
            self.load_adapters()
        elif kind == "verify_credential":
            self.verify_credential(cmd["credential_id"])

    def verify_credential(self, cid: int):
        with session_scope() as s:
            c = s.get(Credential, cid)
            try:
                data = credential(s, cid)
                if c.venue.startswith("binance_"):
                    from .adapters.binance.adapter import BinanceAdapter
                    from .adapters.binance.client import BinanceClient
                    ad = BinanceAdapter(BinanceClient(c.venue.split("_", 1)[1], c.environment, data["api_key"],
                                                      data["api_secret"]))
                    result = {"ok": True, **ad.preflight()}
                elif c.venue == "mt5":
                    from .adapters.forex.mt5 import MT5BridgeAdapter
                    a = MT5BridgeAdapter(data["url"], data["secret"]).account()
                    result = {"ok": True, "currency": a.currency, "equity": a.equity}
                elif c.venue == "myfxbook":
                    from .discovery.sources import MyfxbookSource
                    src = MyfxbookSource(data["email"], data["password"])
                    result = {"ok": True, "accounts": len(src.list_accounts())}
                    src.logout()
                elif c.venue == "darwinex":
                    from .discovery.sources import DarwinexInfoSource
                    result = {"ok": True, "products_page": len(DarwinexInfoSource(data["token"]).list_universe(max_pages=1))}
                else:
                    result = {"ok": None, "note": "verified on first connection"}
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
            c.verify_result = result
            c.verified_at = datetime.now(timezone.utc)

    def periodic(self):
        last = {"triggers": 0, "reconcile": 0, "monitor": 0, "reload": 0, "hb": 0}
        while not self.stop.is_set():
            now = time.time()
            try:
                if now - last["hb"] >= 15:
                    self.heartbeat()
                    last["hb"] = now
                if now - last["reload"] >= 60:
                    self.load_adapters()
                    last["reload"] = now
                if now - last["triggers"] >= 2:
                    for pid in list(self.engine.adapters):
                        self.engine.paper_triggers(pid)
                    last["triggers"] = now
                if now - last["reconcile"] >= 20:
                    for pid in list(self.engine.adapters):
                        self.engine.reconcile(pid)
                    last["reconcile"] = now
                if now - last["monitor"] >= 30:
                    for pid in list(self.engine.adapters):
                        monitor_portfolio(self.engine, pid)
                    last["monitor"] = now
            except Exception:
                log.exception("periodic loop error")
            self.stop.wait(1)

    def run(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        from .db import init_db
        init_db()
        self.load_adapters()
        self.heartbeat()
        threads = [threading.Thread(target=f, name=f.__name__, daemon=True)
                   for f in (self.signals_loop, self.commands_loop, self.periodic)]
        for t in threads:
            t.start()

        def _stop(*_):
            log.info("shutting down engine")
            self.stop.set()
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        while not self.stop.is_set():
            time.sleep(0.5)
        with session_scope() as s:
            st.put(s, st.ENGINE, {"status": "stopped", "heartbeat": None,
                                  "stopped_at": datetime.now(timezone.utc).isoformat()})
            notify(s, "copy_engine_stopped", "Copy engine stopped", "Engine process shut down.", severity="critical")
        _ = Position
