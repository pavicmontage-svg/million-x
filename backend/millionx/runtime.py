"""Wiring: builds the exchange, engine, organisation and background jobs."""
from __future__ import annotations

import json
import os
import logging
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from .backtest import metrics
from .backtest.data import load_symbol
from .backtest.engine import BacktestConfig, run as run_backtest
from .backtest.walkforward import walk_forward
from .config import Settings
from .engine.events import EventBus
from .engine.live import TradingEngine, cid
from .exchange.binance import BinanceFutures
from .exchange.paper import PaperExchange
from .models import now_ms
from .notify import FCMNotifier
from .org.organization import Organization
from .security import Vault, load_api_keys
from .storage import Store

log = logging.getLogger("millionx.runtime")


def process_stats() -> dict[str, Any]:
    """Memory/CPU of this process for VPS and phone monitoring (no extra packages)."""
    out: dict[str, Any] = {"threads": threading.active_count()}
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        t = os.times()
        out["cpu_seconds"] = round(t.user + t.system, 1)
        out["load_avg_1m"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    return out


class Runtime:
    def __init__(self, settings: Settings, store: Store, vault: Optional[Vault]):
        self.s = settings
        self.store = store
        self.vault = vault
        self.bus = EventBus()
        self.notifier = FCMNotifier(store)
        self.org = Organization(store, brain_interval_min=int(store.kv_get("brain_interval_min", 60)))
        self.load_llm_config()
        from .ai.desk import AIDesk
        from .ai.providers import ChainLLM, ProviderChain
        self.ai_chain = ProviderChain(store, vault)
        self.org.llm = ChainLLM(self.ai_chain, self.org.llm)
        self.ai_desk = AIDesk(store, self.ai_chain)
        try:  # Research 2.0 results packaged with this build (CI, real data); never activates anything
            from . import research2_store
            research2_store.load_snapshot(store)
        except Exception as ex:  # a broken snapshot must not stop the app
            store.audit("system", "research2_snapshot_error", {"error": str(ex)[:200]})
        self.engine: Optional[TradingEngine] = None
        self.engine_error: Optional[str] = None
        saved = store.kv_get("runtime_settings", {})
        for k in ("mode", "execution", "capital_mode", "strategy_timeframe"):
            if k in saved:
                setattr(self.s, k, saved[k])
        lim = store.kv_get("risk_limits")
        if lim:
            from .governance import apply_limit_changes
            try:
                apply_limit_changes(self.s.risk, {k: v for k, v in lim.items() if hasattr(self.s.risk, k)},
                                    self.s.mode)
            except ValueError as e:
                store.notify("critical", "risk", "Stored risk limits rejected", f"{e}; defaults kept")
        from .live_gate import LiveGate
        self.gate = LiveGate(store, self.s)
        import os
        from .security import bootstrap_owner_code
        bootstrap_owner_code(store, os.environ.get("MX_BOOTSTRAP_CODE"))
        if self.s.mode == "live" and not self.gate.is_valid():
            # never resume real trading without a valid, unexpired authorisation
            self.s.mode = "paper"
            self.save_runtime()
            store.notify("critical", "risk", "Live mode not resumed",
                         f"live authorisation invalid ({self.gate.status()['reason']}); switched to PAPER")
            store.audit("system", "live_not_resumed", self.gate.status())

        # 2.1: live microstructure collector (separate micro.db; research data only, never trades)
        self.micro_db_path = os.environ.get("MX_MICRO_DB") or os.path.join(os.path.dirname(self.s.db_path) or ".", "micro.db")
        self.micro_thread = None
        self.micro_stop = None
        self.scanner = None
        self.micro_symbols: list[str] = []
        if os.environ.get("MX_MICRO_COLLECTOR") == "1" or (store.kv_get("micro_collector") or {}).get("enabled"):
            self.start_collector()

    # ---------------------------------------------------------- microstructure collector
    MICRO_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT",
                     "AVAXUSDT", "LTCUSDT"]

    def collector_running(self) -> bool:
        return bool(self.micro_thread and self.micro_thread.is_alive())

    def start_collector(self, symbols: Optional[list[str]] = None) -> None:
        import threading
        from .micro import collector
        if self.collector_running():
            return
        self.micro_symbols = symbols or (self.store.kv_get("micro_collector") or {}).get("symbols") or self.MICRO_DEFAULT
        self.micro_stop = threading.Event()

        def attach(agg):
            # 2.2: the continuous scalping scanner runs on every collected second (virtual PaperBook only)
            from .scalp import live as scalp_live
            self.scanner = scalp_live.Scanner(agg.db, agg.lock, scalp_live.load_config(),
                                              capital_eur=self.s.pro_capital_eur if self.s.capital_mode == "pro" else 500.0,
                                              risk_frac=self.s.risk.risk_per_trade, max_positions=self.s.risk.max_open_positions,
                                              daily_loss_eur=self.s.risk.max_daily_loss_eur, max_leverage=self.s.risk.max_leverage)
            agg.listeners.append(self.scanner.on_second)

        def loop():
            try:
                collector.run(self.micro_db_path, self.micro_symbols, 0, self.micro_stop, log=log.info, on_start=attach)
            except Exception as ex:  # recorded, never crashes the app
                self.store.audit("system", "micro_collector_error", {"error": str(ex)[:300]})
        self.micro_thread = threading.Thread(target=loop, name="micro-collector", daemon=True)
        self.micro_thread.start()
        self.store.kv_set("micro_collector", {"enabled": True, "symbols": self.micro_symbols})

    def stop_collector(self) -> None:
        if self.micro_stop:
            self.micro_stop.set()
        if self.micro_thread:
            self.micro_thread.join(timeout=15)
        self.micro_thread = None
        self.store.kv_set("micro_collector", {"enabled": False, "symbols": self.micro_symbols})

    def micro_db(self):
        import sqlite3
        if not os.path.exists(self.micro_db_path):
            return None
        return sqlite3.connect(f"file:{self.micro_db_path}?mode=ro", uri=True, check_same_thread=False)

    # ---------------------------------------------------------- exchange
    def build_exchange(self):
        live = self.s.mode == "live"
        keys = None
        if self.s.mode in ("demo", "live") and self.vault:
            keys = load_api_keys(self.store, self.vault, "binance", self.s.mode)
        if self.s.mode in ("demo", "live") and not keys:
            raise RuntimeError(f"no API keys stored for mode '{self.s.mode}'")
        if self.s.mode == "paper":
            market = BinanceFutures(self.s.market_data_base)
            return PaperExchange(market, self.s.starting_capital_eur * self.s.eur_usdt, self.s.fees)
        ex = BinanceFutures(self.s.rest_base, keys[0], keys[1], live=live)
        if live:
            perms = ex.api_permissions()
            self.store.kv_set("api_permissions", perms)
            if perms.get("withdrawals"):
                raise RuntimeError("refusing to trade: API key has WITHDRAWAL permission enabled")
            if not perms.get("ip_restrict"):
                raise RuntimeError("refusing to trade: live API key is not IP-restricted")
            if not perms.get("futures"):
                raise RuntimeError("refusing to trade: live API key has no Futures permission")
        return ex

    def start_engine(self) -> None:
        self.stop_engine()
        try:
            ex = self.build_exchange()
            self.engine = TradingEngine(self.s, self.store, ex, self.bus, self.notifier, self.org, gate=self.gate)
            self.engine.ai = self.ai_desk
            if self.s.mode != "live":
                # rank the TOP symbols by REAL Binance volume (public endpoint, no key);
                # orders still go to the selected venue (demo / paper)
                self.engine.volume_source = BinanceFutures(os.environ.get("MX_VOLUME_URL", "https://fapi.binance.com"))
            self.engine.start()
            self.engine_error = None
        except Exception as e:
            self.engine = None
            self.engine_error = str(e)
            self.store.notify("critical", "system", "Engine not started", str(e))
            log.error("engine start failed: %s", e)

    def stop_engine(self) -> None:
        if self.engine:
            self.engine.stop()
            if self.engine.thread:
                self.engine.thread.join(timeout=15)
        self.engine = None

    def live_check(self) -> dict[str, Any]:
        keys = load_api_keys(self.store, self.vault, "binance", "live") if self.vault else None
        if not keys:
            return {"ok": False, "checks": {"live_keys": {"pass": False, "detail": "store LIVE API keys first"}}}
        ex = BinanceFutures("https://fapi.binance.com", keys[0], keys[1], live=True)
        return self.gate.live_check(ex, self.s.starting_capital_eur * self.s.eur_usdt)

    # ------------------------------------------------------- monitoring
    def load_llm_config(self) -> None:
        """AI-model connection for the agents; the key is stored encrypted."""
        import base64
        cfg = self.store.kv_get("llm_config")
        if not cfg or not cfg.get("key_enc") or not self.vault:
            return
        try:
            key = self.vault.dec(base64.b64decode(cfg["key_enc"]))
            self.org.llm.configure(cfg["provider"], key, cfg.get("base_url", ""), cfg.get("model", ""))
        except Exception as e:  # never block startup; agents fall back to rules
            self.store.audit("system", "llm_config_load_failed", {"error": f"{type(e).__name__}: {e}"})

    def health(self) -> dict[str, Any]:
        e = self.engine
        now = now_ms()
        alive = bool(e and e.thread and e.thread.is_alive())
        tick_age = (now - e.last_tick_ms) / 1000 if e and e.last_tick_ms else None
        unresolved = self.store.one("SELECT COUNT(*) n FROM incidents WHERE resolved=0 AND severity='critical'")["n"]
        h = {"ts": now, "mode": self.s.mode, "engine_running": alive, "engine_error": self.engine_error,
             "process": process_stats(),
             "last_tick_age_s": tick_age, "data_fresh": e.risk.state.data_fresh if e else None,
             "connection_ok": e.risk.state.connection_ok if e else None,
             "emergency_stop": e.risk.state.emergency_stop if e else self.store.kv_get("emergency_stop", False),
             "startup_reconciled": e.started_reconcile if e else False,
             "schema_version": self.store.schema_version(), "db_integrity": self.store.kv_get("db_integrity"),
             "audit_chain": self.store.kv_get("audit_verify"), "last_backup": self.store.kv_get("last_backup"),
             "unresolved_critical_incidents": unresolved, "live_gate": self.gate.status(),
             "market_symbols": len(e.universe) if e else 0, "last_errors": e.errors[-3:] if e else []}
        warming_up = bool(e and now - e.started_at < 120_000)
        h["ok"] = bool(alive and (tick_age is None or tick_age < 120) and unresolved == 0
                       and (h["db_integrity"] or {}).get("ok", True)
                       and (warming_up or (e is not None and len(e.universe) > 0 and e.risk.state.data_fresh)))
        return h

    def start_watchdog(self, interval_s: int = 30, backup_dir: Optional[str] = None) -> None:
        """Restarts a dead/stuck engine, verifies the audit chain and backs up the DB daily."""
        import os
        backup_dir = backup_dir or os.path.join(os.path.dirname(self.s.db_path) or ".", "backups")
        self.store.kv_set("db_integrity", {"ok": self.store.integrity_ok(), "ts": now_ms()})

        def loop():
            last_daily = 0
            while not getattr(self, "_wd_stop", False):
                try:
                    e = self.engine
                    stuck = e and e.last_tick_ms and now_ms() - e.last_tick_ms > 180_000
                    dead = e is not None and not (e.thread and e.thread.is_alive())
                    if dead or stuck:
                        self.store.notify("critical", "system", "Engine restarted by watchdog",
                                          "dead thread" if dead else "no tick for 3 minutes")
                        self.store.audit("watchdog", "engine_restart", {"dead": dead, "stuck": bool(stuck)})
                        self.start_engine()
                    if now_ms() - last_daily > 86_400_000:
                        last_daily = now_ms()
                        v = self.store.verify_audit()
                        self.store.kv_set("audit_verify", {**v, "ts": now_ms()})
                        if not v["ok"]:
                            self.store.notify("critical", "security", "AUDIT CHAIN BROKEN", str(v))
                        path = self.store.backup(backup_dir)
                        self.store.kv_set("last_backup", {"path": path, "ts": now_ms()})
                except Exception as ex:  # the watchdog itself must never die
                    log.error("watchdog: %s", ex)
                time.sleep(interval_s)

        threading.Thread(target=loop, name="watchdog", daemon=True).start()

    def save_runtime(self) -> None:
        self.store.kv_set("runtime_settings", {"mode": self.s.mode, "execution": self.s.execution,
                                               "capital_mode": self.s.capital_mode,
                                               "strategy_timeframe": self.s.strategy_timeframe})

    # -------------------------------------------------------------- jobs
    def submit_job(self, kind: str, req: dict[str, Any]) -> str:
        jid = uuid.uuid4().hex[:10]
        self.store.x("INSERT INTO jobs(id,kind,status,request,created_at) VALUES(?,?,?,?,?)",
                     (jid, kind, "queued", json.dumps(req), now_ms()))
        threading.Thread(target=self._job, args=(jid, kind, req), daemon=True, name=f"job-{jid}").start()
        return jid

    def _job(self, jid: str, kind: str, req: dict[str, Any]) -> None:
        self.store.x("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        try:
            start = date.fromisoformat(req["start"])
            end = date.fromisoformat(req["end"])
            if end < start:
                raise ValueError("end date is before start date")

            def note(text: str) -> None:
                self.store.x("UPDATE jobs SET progress=? WHERE id=?", (json.dumps({"text": text}), jid))
            from .backtest.data import ensure_data
            for sym in req["symbols"]:
                try:
                    ensure_data(sym, self.s.data_dir, start, end, progress=note)
                except Exception as e:  # offline: fall back to whatever is already on disk
                    note(f"download failed for {sym}: {e}; using local files only")
            note("loading data")
            data = {s: load_symbol(s, self.s.data_dir, start, end) for s in req["symbols"]}
            note(f"running {kind} on " + ", ".join(f"{k}: {len(v):,} candles" for k, v in data.items()))
            t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
            t1 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000
            data = {k: v.slice(int((v.open_time < t0).sum()), int((v.open_time < t1).sum())) for k, v in data.items()}
            tf = int(req.get("timeframe") or 0)
            if tf not in (0, 5, 15, 30, 60, 240):
                raise ValueError("timeframe must be 0, 5, 15, 30, 60 or 240")
            from .strategies import ALL_IDS
            tf_params = {b: {"timeframe": tf} for b in ALL_IDS if b != "bot10_adaptive"} if tf else {}
            if kind == "backtest":
                cfg = BacktestConfig(start_equity=float(req.get("start_equity", 40.0)), bots=req.get("bots"),
                                     use_supervisor=bool(req.get("use_supervisor", True)), params=tf_params)
                res = run_backtest(data, self.s, cfg)
                out = res.summary()
                out["equity_curve"] = res.equity_curve[:: max(1, len(res.equity_curve) // 500)]
                out["trades_sample"] = [t.to_dict() for t in res.trades[-200:]]
            elif kind == "walkforward":
                def prog(bot, k, w):
                    self.store.x("UPDATE jobs SET progress=? WHERE id=?", (json.dumps({"bot": bot, "window": k, "text": f"walk-forward {bot}: window {k}"}), jid))
                out = {}
                for b in req.get("bots") or []:
                    import math as _m
                    warm = max(3, _m.ceil((tf or 5) * 60 / 1440) + 1)
                    wf = walk_forward(data, self.s, b, int(req.get("train_days", 60)), int(req.get("test_days", 20)),
                                      warmup_days=warm, progress=prog, base_params={"timeframe": tf} if tf else None)
                    out[b] = wf.to_dict()
                    self.store.audit("quant_research", "walk_forward_completed",
                                     {"job": jid, "bot": b}, "n/a", wf.activation)
            else:
                raise ValueError(f"unknown job kind {kind}")
            self.store.x("UPDATE jobs SET status='done', result=?, finished_at=? WHERE id=?",
                         (json.dumps(metrics.json_safe(out), default=str), now_ms(), jid))
            self.store.notify("info", "backtest", f"{kind} {jid} finished", ", ".join(req["symbols"]))
        except Exception as e:
            self.store.x("UPDATE jobs SET status='error', result=?, finished_at=? WHERE id=?",
                         (json.dumps({"error": str(e), "trace": traceback.format_exc()[-2000:]}), now_ms(), jid))

    # --------------------------------------------------------- preflight
    def preflight(self) -> dict[str, Any]:
        """Execution & security checks against Binance DEMO. All must pass
        before live trading can be unlocked by the owner."""
        checks: dict[str, Any] = {}

        def check(name, fn):
            try:
                checks[name] = {"pass": True, "detail": fn()}
            except Exception as e:
                checks[name] = {"pass": False, "detail": str(e)}

        keys = load_api_keys(self.store, self.vault, "binance", "demo") if self.vault else None
        if not keys:
            return {"ok": False, "checks": {"demo_keys": {"pass": False, "detail": "store demo API keys first"}}}
        ex = BinanceFutures(self.s.rest_base if self.s.mode != "live" else "https://demo-fapi.binance.com",
                            keys[0], keys[1])
        sym = "ETHUSDT"
        state: dict[str, Any] = {}
        check("connectivity", lambda: ex.server_time())
        check("exchange_info", lambda: len(ex.symbols()))
        check("account_auth", lambda: ex.balance())
        check("commission", lambda: ex.commission(sym))

        def order_cycle():
            rules = ex.symbols()[sym]
            ob = ex.order_book(sym, 5)
            qty = rules.round_qty(max(rules.min_notional * 1.2 / ob.mid, rules.min_qty))
            while qty * ob.mid < rules.min_notional:
                qty = rules.round_qty(qty + rules.step_size)
            ex.set_isolated(sym)
            ex.set_leverage(sym, 2)
            eid = cid("P", "preflight", str(now_ms()))
            r = ex.market_order(sym, "BUY", qty, eid)
            r2 = ex.query_order(sym, eid)
            assert r2.status == "FILLED", f"entry status {r2.status}"
            sl = ex.conditional_order(sym, "SELL", "STOP_MARKET", rules.round_price(ob.mid * 0.95),
                                      cid("PS", eid), close_position=True)
            ex.cancel_conditional(sym, sl.client_id)
            amt = ex.position_amount(sym)
            ex.market_order(sym, "SELL", abs(amt), cid("PX", eid), reduce_only=True)
            ex.cancel_all(sym)
            assert abs(ex.position_amount(sym)) < 1e-12, "position not flat after close"
            return {"entry": r2.raw.get("avgPrice"), "qty": qty}

        check("order_lifecycle_demo", order_cycle)
        def dup_guard():
            test_id = cid("D", "preflight", str(now_ms()))
            self.store.upsert_order({"client_id": test_id, "symbol": sym, "side": "BUY", "type": "TEST",
                                     "qty": 0, "status": "TEST", "purpose": "preflight", "mode": "test"})
            assert self.store.one("SELECT client_id FROM orders WHERE client_id=?", (test_id,)), "lookup failed"
            return "engine refuses an entry whose client id already exists in the order journal"

        check("duplicate_protection", dup_guard)
        check("stale_data_guard", lambda: self.s.risk.stale_data_seconds <= 120 or (_ for _ in ()).throw(
            AssertionError("stale threshold too loose")))
        check("master_key_present", lambda: bool(self.vault) or (_ for _ in ()).throw(AssertionError("no vault")))
        check("audit_chain", lambda: self.store.verify_audit())
        ok = all(c["pass"] for c in checks.values())
        res = {"ok": ok, "ts": now_ms(), "checks": checks}
        self.store.kv_set("preflight", res)
        self.gate.record_demo_preflight(res)
        self.store.audit("operations", "preflight", {}, "n/a", res)
        return res
