"""Real-time trading engine (paper / Binance demo / live).

Runs in its own thread on the server, independent of the Android app.
Market data: REST polling of closed 1m bars (+ order book at each bar close).
Every real position is protected by an exchange-side STOP_MARKET
(closePosition) placed immediately after the entry fill; if the stop cannot be
placed the position is closed at once.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..competition import CompetitionEngine
from ..config import Settings
from ..exchange.base import Exchange, ExchangeError
from ..models import Bars, BotStatus, OrderBookSnapshot, Position, Side, Signal, Trade, now_ms, MINUTE_MS
from ..regime import regime_key
from ..risk import RiskManager, SizingDecision, SymbolRules
from ..storage import Store
from ..strategies import MarketData, Strategy, build_bots, ALL_IDS
from ..supervisor import Supervisor, Verdict
from .events import EventBus
from .position import ExitPolicy, update_on_bar

log = logging.getLogger("millionx.engine")
WINDOW = 1500


@dataclass
class PendingApproval:
    signal: Signal
    decision: SizingDecision
    created: int
    expires: int
    record: dict[str, Any] = field(default_factory=dict)


def cid(prefix: str, *parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]
    return f"mx{prefix}-{h}"[:36]


class TradingEngine:
    def __init__(self, settings: Settings, store: Store, exchange: Exchange, bus: Optional[EventBus] = None,
                 notifier=None, org=None, poll_seconds: float = 5.0, gate=None, approval_ttl_s: int = 90):
        self.s = settings
        self.store = store
        self.ex = exchange
        self.bus = bus or EventBus()
        self.notifier = notifier
        self.org = org
        self.gate = gate
        self.approval_ttl_ms = approval_ttl_s * 1000
        self.poll = poll_seconds
        self.policy = ExitPolicy()
        self.comp = CompetitionEngine(list(ALL_IDS))
        self.sup = Supervisor(self.comp, tradable_statuses=self._tradable())
        self.risk = RiskManager(settings.risk, settings.fees, settings.eur_usdt)
        from ..strategies.base import set_min_stop_from_fees
        set_min_stop_from_fees(settings.fees, settings.target_fee_share)
        self.rules: dict[str, SymbolRules] = {}
        self.universe: list[str] = []
        self.scanner: dict[str, dict[str, Any]] = {}
        # Optional public real-market source used ONLY to rank symbols by real traded
        # volume (demo/paper venues report their own, non-representative volume).
        self.volume_source: Optional[Exchange] = None
        self.volume_source_used = "venue"
        self.bars: dict[str, Bars] = {}
        self.books: dict[str, OrderBookSnapshot] = {}
        self.mds: dict[str, MarketData] = {}
        self.bots: dict[str, list[Strategy]] = {}
        self.positions: dict[str, Position] = {}
        self.virtual: dict[tuple[str, str], Position] = {}
        self.pending: dict[str, PendingApproval] = {}
        self.last_signals: list[dict[str, Any]] = []
        self.leverage_set: dict[str, int] = {}
        self.allocated_equity = 0.0
        self.start_capital_usdt = 0.0
        self.started_at = now_ms()
        self.last_bar_ms: dict[str, int] = {}
        self.last_universe_refresh = 0
        self.last_heartbeat = 0
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.thread: Optional[threading.Thread] = None
        self.errors: list[str] = []
        self.ai = None                # AIDesk (independent trader + AI supervisor), set by the runtime
        self._ai_requeue: set[str] = set()
        self._last_audit = 0
        self.bars_processed = 0       # closed candles analysed since start
        self.signals_seen = 0         # raw bot signals since start
        self.last_tick_ms = 0
        self.started_reconcile = False
        self._err_alerted: dict[str, int] = {}
        from .execution import ExecutionManager
        self.execm = ExecutionManager(self)
        self._load_state()
        self.risk.reset(self.allocated_equity, now_ms())
        self.risk.state.data_fresh = False  # no entries until real market data has arrived
        if self.s.mode == "live":
            from ..validation import enforce_live_statuses
            enforce_live_statuses(self)
        if self.store.kv_get("emergency_stop", False):
            self.risk.emergency(True, "persisted emergency stop")

    # ------------------------------------------------------------------ setup
    def _tradable(self) -> tuple:
        if self.s.mode == "live":
            return (BotStatus.LIVE_ELIGIBLE,)
        # paper/demo: every bot that is not suspended/disabled trades, to collect evidence
        return (BotStatus.RESEARCH, BotStatus.BACKTEST_PASSED, BotStatus.PAPER, BotStatus.LIVE_ELIGIBLE)

    def _load_state(self) -> None:
        for r in self.store.q("SELECT * FROM bot_state"):
            try:
                self.sup.set_status(r["bot_id"], BotStatus(r["status"]), r["reason"] or "")
                if r["weight"] is not None and r["bot_id"] in self.comp.books:
                    self.comp.books[r["bot_id"]].weight = float(r["weight"])
            except ValueError:
                pass
        cap = self.store.kv_get(f"capital:{self.s.mode}:{self.s.capital_mode}")
        self.start_capital_usdt = self.s.starting_capital_eur * self.s.eur_usdt
        self.allocated_equity = float(cap) if cap is not None else self.start_capital_usdt
        # restore open positions
        for r in self.store.q("SELECT data FROM positions WHERE is_open=1 AND mode=?", (self.s.mode,)):
            import json
            d = json.loads(r["data"])
            d["side"] = Side.LONG if d["side"] == "LONG" else Side.SHORT
            p = Position(**d)
            if p.virtual:
                self.virtual[(p.bot_id, p.symbol)] = p
            else:
                self.positions[p.symbol] = p
        if self.store.kv_get("emergency_stop", False):
            self.risk.emergency(True, "persisted emergency stop")
        # approvals do not survive a restart: the owner must see a fresh, re-priced request
        self.store.x("UPDATE pending_approvals SET status='expired_restart' WHERE status='pending'")

    def save_bot_state(self, bot_id: str) -> None:
        st = self.sup.status.get(bot_id, BotStatus.RESEARCH)
        self.store.x("INSERT INTO bot_state(bot_id,status,reason,params,weight,updated_at) VALUES(?,?,?,?,?,?) "
                     "ON CONFLICT(bot_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
                     "weight=excluded.weight, updated_at=excluded.updated_at",
                     (bot_id, st.value, self.sup.status_reason.get(bot_id, ""), "{}",
                      self.comp.weight(bot_id), now_ms()))

    def _persist_position(self, p: Position, open_: bool) -> None:
        import json
        self.store.x("INSERT INTO positions(id,symbol,bot_id,is_open,data,mode,updated_at) VALUES(?,?,?,?,?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET is_open=excluded.is_open, data=excluded.data, "
                     "updated_at=excluded.updated_at",
                     (p.id, p.symbol, p.bot_id, int(open_), json.dumps(p.to_dict(), default=str), self.s.mode, now_ms()))

    def alert(self, level: str, category: str, title: str, body: str) -> None:
        self.store.notify(level, category, title, body)
        self.bus.publish("alert", {"level": level, "category": category, "title": title, "body": body})
        if self.notifier:
            try:
                self.notifier.send(level, category, title, body)
            except Exception as e:  # notifications must never break trading
                log.warning("notify failed: %s", e)

    # ------------------------------------------------------------- universe
    def refresh_universe(self) -> None:
        rules = self.ex.symbols()
        tick = None
        if self.volume_source is not None:
            try:
                tick = self.volume_source.tickers_24h()
                self.volume_source_used = "binance_real_market"
            except ExchangeError as e:
                self.errors = (self.errors + [f"real-volume ranking unavailable, using venue volume: {e}"])[-50:]
        if tick is None:
            tick = self.ex.tickers_24h()
            self.volume_source_used = "venue"
        cands = []
        for t in tick:
            sym = t["symbol"]
            r = rules.get(sym)
            if not r or getattr(r, "quote", "USDT") != self.s.quote_asset:
                continue
            if getattr(r, "base", "") in self.s.excluded_bases:
                continue
            cands.append((float(t.get("quoteVolume", 0)), sym))
        cands.sort(reverse=True)
        chosen, scan = [], {}
        for qv, sym in cands[: self.s.universe_size * 3]:
            try:
                ob = self.ex.order_book(sym, 50)
            except ExchangeError as e:
                scan[sym] = {"symbol": sym, "quote_volume": qv, "ok": False, "reason": str(e)}
                continue
            bid_d, ask_d = ob.depth_within(10)
            ok = ob.spread_bps <= self.s.max_spread_bps and min(bid_d, ask_d) >= self.s.min_depth_usdt
            scan[sym] = {"symbol": sym, "quote_volume": qv, "spread_bps": round(ob.spread_bps, 3),
                         "depth_bid_10bps": bid_d, "depth_ask_10bps": ask_d, "ok": ok,
                         "reason": "" if ok else "spread/depth below threshold"}
            if ok and len(chosen) < self.s.universe_size:
                chosen.append(sym)
        # never drop a symbol with an open position
        for sym in self.positions:
            if sym not in chosen:
                chosen.append(sym)
        with self._lock:
            self.rules.update({k: rules[k] for k in chosen if k in rules})
            self.universe = chosen
            for k, v in scan.items():
                self.scanner.setdefault(k, {}).update(v)
        self.last_universe_refresh = now_ms()
        self.store.audit("market_intelligence", "universe_refreshed", {"symbols": chosen,
                         "volume_source": self.volume_source_used,
                         "candidates": [c[1] for c in cands[: self.s.universe_size * 3]]})
        self.bus.publish("universe", {"symbols": chosen})

    def _load_fees(self) -> None:
        for sym in self.universe:
            try:
                c = self.ex.commission(sym)
                if c:
                    self.rules[sym].maker, self.rules[sym].taker = c
            except ExchangeError as e:
                log.info("commission %s: %s", sym, e)

    # ------------------------------------------------------------ data feed
    def _update_bars(self, sym: str) -> bool:
        now = now_ms()
        if sym not in self.bars or len(self.bars[sym]) == 0:
            b = self._history(sym, self.window())
        else:
            b = self.ex.klines(sym, 5)
        if len(b) == 0:
            return False
        closed = b.open_time + MINUTE_MS <= now - 1500  # only bars that have closed
        b = b.slice(0, int(closed.sum())) if not closed.all() else b
        if len(b) == 0:
            return False
        old = self.bars.get(sym)
        if old is None or len(old) == 0:
            self.bars[sym] = b
            self.last_bar_ms[sym] = int(b.open_time[-1])
            return True
        new_mask = b.open_time > old.open_time[-1]
        if not new_mask.any():
            return False
        k = int(np.argmax(new_mask))
        add = b.slice(k, len(b))
        merged = Bars(*(np.concatenate([getattr(old, f), getattr(add, f)])[-self.window():] for f in
                        ("open_time", "open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume")))
        self.bars[sym] = merged
        self.last_bar_ms[sym] = int(merged.open_time[-1])
        return True

    def window(self) -> int:
        """1m bars kept per symbol: enough for every bot's timeframe and warm-up
        (e.g. 4-hour bots need ~90 four-hour bars = 21,600 one-minute bars)."""
        import json
        params = self._bot_params()
        key = json.dumps(params, sort_keys=True, default=str)
        if getattr(self, "_window_key", None) != key:
            need = WINDOW
            for x in build_bots(None, params):
                need = max(need, x.timeframe * (getattr(x, "warmup_bars", 60) + 30))
            self._window_key, self._window_val = key, int(min(need, 22000))
        return self._window_val

    def _history(self, sym: str, n: int) -> Bars:
        """Initial history; Binance returns at most 1,500 klines per request, so page backwards."""
        b = self.ex.klines(sym, min(n, WINDOW))
        while len(b) and len(b) < n:
            older = self.ex.klines(sym, min(WINDOW, n - len(b)), end_time=int(b.open_time[0]) - 1)
            if len(older) == 0 or older.open_time[-1] >= b.open_time[0]:
                break
            b = Bars(*(np.concatenate([getattr(older, f), getattr(b, f)]) for f in
                       ("open_time", "open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume")))
        return b

    def data_fresh(self) -> bool:
        if not self.universe:
            return False
        now = now_ms()
        limit = self.s.risk.stale_data_seconds * 1000
        return all(now - (self.last_bar_ms.get(s, 0) + MINUTE_MS) <= limit + MINUTE_MS for s in self.universe)

    # ------------------------------------------------------------- main loop
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="trading-engine", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self.alert("info", "system", "Engine started", f"mode={self.s.mode} execution={self.s.execution}")
        while not self._stop.is_set():
            t0 = time.time()
            try:
                with self._lock:
                    self.tick()
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.errors = (self.errors + [msg])[-50:]
                key = type(e).__name__
                if now_ms() - self._err_alerted.get(key, 0) > 600_000:
                    self._err_alerted[key] = now_ms()
                    log.error("tick failed: %s\n%s", msg, traceback.format_exc())
                    self.alert("critical", "system", "Engine error", msg[:300])
                else:
                    log.warning("tick failed: %s", msg)
            self._stop.wait(max(0.5, self.poll - (time.time() - t0)))

    def tick(self) -> None:
        now = now_ms()
        self.last_tick_ms = now
        if not self.universe or now - self.last_universe_refresh > self.s.universe_refresh_minutes * 60_000:
            self.refresh_universe()
            self._load_fees()
        if not self.started_reconcile:
            rep = self.execm.reconcile(startup=True)
            if "error" not in rep:
                self.started_reconcile = True
                self.alert("info", "system", "Startup reconciliation done",
                           f"adopted {rep['adopted']}, finalized {rep['finalized']}, stops restored {rep['stop_restored']}")
        new_bar = []
        for sym in list(self.universe):
            try:
                if self._update_bars(sym):
                    new_bar.append(sym)
            except ExchangeError as e:
                self.errors = (self.errors + [f"{sym}: {e}"])[-50:]
        errs = getattr(self.ex, "consecutive_errors", 0) or getattr(getattr(self.ex, "m", None), "consecutive_errors", 0)
        conn_ok = errs < 3
        if conn_ok != self.risk.state.connection_ok:
            self.risk.state.connection_ok = conn_ok
            self.alert("critical" if not conn_ok else "info", "connection",
                       "Exchange connection lost" if not conn_ok else "Exchange connection restored",
                       "New entries blocked; exchange-side stops still protect positions." if not conn_ok else "")
        fresh = self.data_fresh()
        if fresh != self.risk.state.data_fresh:
            self.risk.state.data_fresh = fresh
            if not fresh:
                self.alert("warning", "data", "Stale market data", "New entries blocked until data is fresh.")
        if new_bar and self.started_reconcile:
            self.execm.reconcile()
        for sym in new_bar:
            self.on_bar(sym)
        self._expire_approvals()
        if new_bar:
            self._mark_equity()
        if now - self.last_heartbeat > 15_000:
            self.last_heartbeat = now
            self.bus.publish("heartbeat", self.status_summary())
        day = now // 86_400_000
        if self.store.kv_get("last_competition_day") != day:
            self.store.kv_set("last_competition_day", day)
            rec = self.comp.daily_cycle(now)
            for b in self.comp.books:
                self.save_bot_state(b)
            self.store.audit("strategy_intelligence", "daily_competition_cycle",
                             {"proposed": rec["proposed"], "sim_old": rec["sim_old"], "sim_new": rec["sim_new"]},
                             "auto", {"applied": rec["applied"]})
            if self.org:
                self.org.on_new_day(self)
        if self.org:
            self.org.on_tick(self)
        if self.ai is not None:
            try:
                self.ai.on_tick(self)
                for sid in list(self._ai_requeue):
                    self._ai_requeue.discard(sid)
                    if sid in self.pending:
                        self.ai.queue_review(self, sid)
            except Exception as e:  # the AI layer must never stop trading protection
                self.errors = (self.errors + [f"ai: {e}"])[-50:]
        if now_ms() - self._last_audit > 15 * 60_000:  # Performance Auditor snapshot
            self._last_audit = now_ms()
            try:
                from ..auditor import snapshot
                snapshot(self.store, self.s.mode, self.start_capital_usdt)
            except Exception as e:
                self.errors = (self.errors + [f"auditor: {e}"])[-50:]

    # ------------------------------------------------------------- per bar
    def on_bar(self, sym: str) -> None:
        self.bars_processed += 1
        b = self.bars[sym]
        o, h, l, c = (float(x[-1]) for x in (b.open, b.high, b.low, b.close))
        ts = int(b.open_time[-1]) + MINUTE_MS
        if self.ex.is_paper:
            self.ex.on_bar(sym, o, h, l, c, ts)  # type: ignore[attr-defined]
        try:
            self.books[sym] = self.ex.order_book(sym, 20)
        except ExchangeError:
            self.books.pop(sym, None)
        md = MarketData(sym, b, self.books.get(sym))
        md.live = True  # a missing order book here is missing data, not a historical run
        self.mds[sym] = md
        if sym not in self.bots:
            self.bots[sym] = build_bots(self.comp.regimes, self._bot_params())
        bots = self.bots[sym]
        for x in bots:
            x.prepare(md)
        i = len(b) - 1
        trend, vol = md.regime_at_1m(i)
        self.scanner.setdefault(sym, {}).update(symbol=sym, price=c, regime=trend, volatility=vol,
                                                atr_pct_15m=md.atr_pct15_at_1m(i),
                                                spread_bps=self.books[sym].spread_bps if sym in self.books else None,
                                                book_imbalance=self.books[sym].imbalance(10) if sym in self.books else None,
                                                updated=ts)
        # manage existing real position and shadow positions
        if sym in self.positions:
            self._manage_position(sym, o, h, l, c, ts)
        self._update_virtual(sym, o, h, l, c, ts)
        # signals
        sigs = []
        for x in bots:
            if self.sup.status.get(x.bot_id) == BotStatus.DISABLED:
                continue
            s = x.signal_at(i)
            if s is not None:
                sigs.append(s)
        if not sigs:
            return
        self.signals_seen += len(sigs)
        for s in sigs:
            self._open_virtual(s, c, ts, md)
        regime_of = lambda sy: self.mds[sy].regime_at_1m(len(self.bars[sy]) - 1)
        open_syms = {p.symbol: int(p.side) for p in self.positions.values()}
        open_syms.update({p.signal.symbol: int(p.signal.side) for p in self.pending.values()})
        verdicts = self.sup.review(sigs, regime_of, open_syms, self._price_corr)
        for v in verdicts:
            rec = {"signal": v.signal.to_dict(), "verdict": v.to_dict()}
            self.store.x("INSERT OR IGNORE INTO signals(id,ts,bot_id,symbol,data,verdict) VALUES(?,?,?,?,?,?)",
                         (v.signal.id, ts, v.signal.bot_id, sym, _j(v.signal.to_dict()), _j(v.to_dict())))
            self.last_signals = ([rec] + self.last_signals)[:100]
            self.bus.publish("signal", rec)
            if v.accepted:
                self._decide(v, md, ts)

    def _bot_params(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.store.q("SELECT bot_id, params FROM bot_state"):
            try:
                import json
                p = json.loads(r["params"] or "{}")
                if p:
                    out[r["bot_id"]] = p
            except ValueError:
                pass
        tf = int(getattr(self.s, "strategy_timeframe", 0) or 0)
        if tf:  # owner-selected timeframe for every base bot (the adaptive bot relays them)
            for bid in ALL_IDS:
                if bid != "bot10_adaptive":
                    out[bid] = {**out.get(bid, {}), "timeframe": tf}
        return out

    def _price_corr(self, a: str, b: str) -> float:
        ba, bb = self.bars.get(a), self.bars.get(b)
        if ba is None or bb is None or len(ba) < 300 or len(bb) < 300:
            return 0.0
        n = min(len(ba), len(bb), 600)
        ra = np.diff(np.log(ba.close[-n:]))
        rb = np.diff(np.log(bb.close[-n:]))
        if ra.std() == 0 or rb.std() == 0:
            return 0.0
        return float(np.corrcoef(ra, rb)[0, 1])

    # --------------------------------------------------------- decision flow
    def _decide(self, v: Verdict, md: MarketData, ts: int) -> None:
        """Steps 3-8 of the organisational workflow. Each step is logged."""
        s = v.signal
        steps: list[dict[str, Any]] = [
            {"step": 1, "dept": "market_intelligence", "result": "regime", "data": md.regime_at_1m(len(md.b1) - 1)},
            {"step": 2, "dept": "strategy_intelligence", "result": "accepted", "score": v.score, "reasons": v.reasons},
        ]
        st = self.sup.status.get(s.bot_id, BotStatus.RESEARCH)
        validated = st in self.sup.tradable
        steps.append({"step": 3, "dept": "quant_research", "result": "validated" if validated else "not_validated",
                      "bot_status": st.value})
        if not validated:
            self._log_decision(s, steps, "rejected")
            return
        if any(p.bot_id == s.bot_id for p in self.positions.values()):
            steps.append({"step": 4, "dept": "capital_intelligence", "result": "rejected",
                          "reason": "bot already has an open position (per-bot budget)"})
            self._log_decision(s, steps, "rejected")
            return
        rules = self.rules.get(s.symbol)
        if rules is None:
            steps.append({"step": 4, "dept": "capital_intelligence", "result": "rejected",
                          "reason": "exchange rules for this symbol not loaded"})
            self._log_decision(s, steps, "no_symbol_rules")
            return
        bk = self.books.get(s.symbol)
        quality = {"score": v.score, "confidence": s.confidence, "spread_bps": bk.spread_bps if bk else None}
        dec = self.risk.size(s, rules, list(self.positions.values()), md.atr_pct15_at_1m(len(md.b1) - 1), ts,
                             v.size_multiplier * self._budget_multiplier(s.bot_id), quality=quality)
        steps.append({"step": 4, "dept": "capital_intelligence", "result": "sized" if dec.approved else "rejected",
                      "decision": dec.to_dict()})
        book = self.books.get(s.symbol)
        exec_plan = self._execution_plan(s, dec, book)
        steps.append({"step": 5, "dept": "execution_intelligence", **exec_plan})
        cro_ok, cro_reason = dec.approved and exec_plan["ok"], (dec.reasons or [exec_plan.get("reason", "")])
        if cro_ok and self.org:
            cro_ok, factor, why = self.org.cro_review(self, s, dec)
            if cro_ok and factor < 1.0:
                dec.qty = rules.round_qty(dec.qty * factor)
                dec.adjustments.append(f"CRO reduced size x{factor:.2f}: {why}")
                if dec.qty * s.entry < rules.min_notional:
                    cro_ok, why = False, "CRO reduction falls below exchange minimum"
            cro_reason = [why]
        if cro_ok and self.s.mode == "live" and not (self.gate and self.gate.is_valid()):
            cro_ok, cro_reason = False, ["live trading not authorised (missing, expired or invalidated unlock)"]
        steps.append({"step": 6, "dept": "cro", "result": "approved" if cro_ok else "vetoed", "reasons": cro_reason})
        if not cro_ok:
            self._log_decision(s, steps, "vetoed")
            return
        if self.ai is not None and self.ai.supervisor_active():
            # AI SIGNAL -> SUPERVISOR -> RISK ENGINE -> EXECUTION: the AI review runs in a worker thread;
            # on APPROVE the engine re-sizes at the current price and governance re-checks everything
            pa = PendingApproval(s, dec, now_ms(), now_ms() + self.approval_ttl_ms, {"steps": steps, "stage": "ai_review",
                                 "quality": {"score": v.score, "spread_bps": self.books[s.symbol].spread_bps
                                             if s.symbol in self.books else None}})
            self.pending[s.id] = pa
            self.store.x("INSERT OR REPLACE INTO pending_approvals(signal_id,created_at,expires_at,data,status) "
                         "VALUES(?,?,?,?,?)", (s.id, pa.created, pa.expires,
                                               _j({"signal": s.to_dict(), "decision": dec.to_dict()}), "ai_review"))
            self._log_decision(s, steps, "awaiting_ai_supervisor")
            self.ai.queue_review(self, s.id)
            return
        if self.s.execution == "manual":
            pa = PendingApproval(s, dec, now_ms(), now_ms() + self.approval_ttl_ms, {"steps": steps})
            self.pending[s.id] = pa
            self.store.x("INSERT OR REPLACE INTO pending_approvals(signal_id,created_at,expires_at,data,status) "
                         "VALUES(?,?,?,?,?)", (s.id, pa.created, pa.expires,
                                               _j({"signal": s.to_dict(), "decision": dec.to_dict()}), "pending"))
            self.alert("info", "approval", f"Approve {s.bot_id} {s.side.name} {s.symbol}?",
                       f"qty {dec.qty} lev {dec.leverage}x risk {dec.risk_amount:.4f} USDT "
                       f"(expires in {self.approval_ttl_ms // 1000} s; re-checked against the live price)")
            self.bus.publish("approval_request", {"signal": s.to_dict(), "decision": dec.to_dict()})
            self._log_decision(s, steps, "awaiting_owner_approval")
            return
        self._execute(s, dec, steps)

    def _budget_multiplier(self, bot_id: str) -> float:
        if self.s.capital_mode != "multi":
            return 1.0
        w = self.comp.weight(bot_id)
        return max(0.25, min(1.0, w))  # weights above 1 never increase risk beyond the 0.5 % base

    def _execution_plan(self, s: Signal, dec: SizingDecision, book: Optional[OrderBookSnapshot]) -> dict[str, Any]:
        if not dec.approved:
            return {"ok": False, "reason": "not sized"}
        if book is None:
            return {"ok": False, "reason": "no order book snapshot"}
        if book.spread_bps > self.risk.limits.max_spread_bps:
            return {"ok": False, "reason": f"spread {book.spread_bps:.2f}bps > {self.risk.limits.max_spread_bps}"}
        side_depth = book.depth_within(10)[1 if s.side == Side.LONG else 0]
        if dec.notional > 0.1 * side_depth:
            return {"ok": False, "reason": "order would exceed 10% of depth within 10 bps"}
        return {"ok": True, "order_type": "MARKET", "spread_bps": book.spread_bps, "depth_10bps": side_depth,
                "why": "scalp entry needs certainty of fill; spread and depth within limits"}

    # ---- external signals (AI trader, TradingView) and AI supervisor hooks ----
    def submit_external(self, sig: Signal, origin: str) -> dict[str, Any]:
        """Entry point for signals that do not come from the bots. They take exactly the
        same path: shadow book -> (AI) supervisor -> deterministic risk engine -> execution."""
        with self._lock:
            md = self.mds.get(sig.symbol)
            if md is None or sig.symbol not in self.bars:
                return {"ok": False, "error": f"{sig.symbol} is not tracked by the engine"}
            ts = now_ms()
            price = float(self.bars[sig.symbol].close[-1])
            self._open_virtual(sig, price, ts, md)
            from ..supervisor import Verdict
            v = Verdict(signal=sig, accepted=True, score=float(sig.confidence), reasons=[f"external:{origin}"])
            rec = {"signal": sig.to_dict(), "verdict": v.to_dict()}
            self.store.x("INSERT OR IGNORE INTO signals(id,ts,bot_id,symbol,data,verdict) VALUES(?,?,?,?,?,?)",
                         (sig.id, ts, sig.bot_id, sig.symbol, _j(sig.to_dict()), _j(v.to_dict())))
            self.last_signals = ([rec] + self.last_signals)[:100]
            self.bus.publish("signal", rec)
            self._decide(v, md, ts)
            return {"ok": True, "signal_id": sig.id}

    def mark_supervisor_approved(self, signal_id: str, review: dict[str, Any]) -> None:
        """Manual execution: the AI supervisor approved, the owner still has to approve."""
        with self._lock:
            p = self.pending.get(signal_id)
            if not p:
                return
            p.record["stage"] = "owner"
            self.store.x("UPDATE pending_approvals SET status='pending' WHERE signal_id=?", (signal_id,))
            s = p.signal
            self.alert("info", "approval", f"AI supervisor approved {s.bot_id} {s.side.name} {s.symbol} — your approval?",
                       f"max leverage {review.get('max_leverage')}x; " + "; ".join(map(str, review.get("reasons", [])))[:300])
            self.bus.publish("approval_request", {"signal": s.to_dict(), "decision": p.decision.to_dict()})

    def requeue_review(self, signal_id: str) -> None:
        self._ai_requeue.add(signal_id)

    def approve(self, signal_id: str, approved: bool, by: str) -> dict[str, Any]:
        with self._lock:
            return self._approve_unlocked(signal_id, approved, by)

    def _approve_unlocked(self, signal_id: str, approved: bool, by: str) -> dict[str, Any]:
        p = self.pending.pop(signal_id, None)
        if not p:
            return {"ok": False, "error": "not pending (expired, decided or unknown)"}
        if now_ms() > p.expires:
            self._close_approval(signal_id, "expired")
            return {"ok": False, "error": "approval window expired"}
        self._close_approval(signal_id, "approved" if approved else "rejected", by)
        self.store.audit(by, "manual_trade_approval", {"signal_id": signal_id}, "approved" if approved else "rejected")
        if not approved:
            return {"ok": True, "status": "rejected"}
        ok, why = self.risk.can_open(now_ms())
        if not ok:
            return {"ok": False, "error": f"risk state changed: {why}"}
        # the market moved while the owner decided: re-size at the current executable price;
        # the governance pre-trade check then enforces the drift and stop/target limits
        s = p.signal
        try:
            px = self.ex.book_price(s.symbol, "BUY" if s.side == Side.LONG else "SELL")
        except ExchangeError as ex:
            return {"ok": False, "error": f"cannot price the order now: {ex}"}
        rules = self.rules.get(s.symbol)
        md = self.mds.get(s.symbol)
        if rules is None or md is None:
            return {"ok": False, "error": "symbol no longer tracked"}
        import copy
        s_now = copy.copy(s)
        s_now.entry = px
        dec = self.risk.size(s_now, rules, list(self.positions.values()), md.atr_pct15_at_1m(len(md.b1) - 1),
                             now_ms(), min(1.0, p.decision.risk_fraction / max(1e-12, self.risk.risk_fraction())),
                             quality=p.record.get("quality"), max_leverage=p.record.get("supervisor_max_leverage"))
        steps = p.record.get("steps", []) + [{"step": 6.2, "dept": "capital_intelligence", "result":
                                              "resized_at_approval" if dec.approved else "rejected_at_approval",
                                              "price_at_signal": s.entry, "price_now": px, "decision": dec.to_dict()}]
        if not dec.approved:
            self._log_decision(s, steps, "rejected_at_approval")
            return {"ok": False, "error": "; ".join(dec.reasons)}
        return self.execm.execute(s, dec, steps)

    def _close_approval(self, signal_id: str, status: str, by: str = "system") -> None:
        self.store.x("UPDATE pending_approvals SET status=? WHERE signal_id=?", (status, signal_id))
        self.store.x("UPDATE signals SET approval=?, decided_by=? WHERE id=?", (status, by, signal_id))

    def _expire_approvals(self) -> None:
        now = now_ms()
        for k in [k for k, v in self.pending.items() if v.expires < now]:
            self.pending.pop(k)
            self._close_approval(k, "expired")
            self.bus.publish("approval_expired", {"signal_id": k})

    # ------------------------------------------------------------ execution
    def _execute(self, s: Signal, dec: SizingDecision, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return self.execm.execute(s, dec, steps)

    def _log_decision(self, s: Signal, steps: list[dict[str, Any]], outcome: str) -> None:
        self.store.audit("decision_pipeline", "trade_decision", {"signal_id": s.id, "bot": s.bot_id,
                         "symbol": s.symbol, "side": "LONG" if int(s.side) > 0 else "SHORT", "steps": steps}, outcome)
        self.bus.publish("decision", {"signal_id": s.id, "outcome": outcome, "steps": steps})

    def _replace_stop(self, p: Position, new_stop: float, reason: str) -> bool:
        return self.execm.replace_stop(p, new_stop, reason)

    def _manage_position(self, sym: str, o: float, h: float, l: float, c: float, ts: int) -> None:
        p = self.positions[sym]
        if now_ms() - self.execm.ex_pos_ts < 30_000:
            amt = abs(self.execm.ex_pos.get(sym, (0.0, 0.0))[0])
        else:
            try:
                amt = abs(self.ex.position_amount(sym))
            except ExchangeError as e:
                self.errors.append(f"position sync {sym}: {e}")
                return
        if amt <= 0:
            self._finalize(p, c, "exchange_exit")
            return
        if p.bot_id == "orphan":
            self._persist_position(p, True)
            return
        # targets not on the exchange: manage them from bar data
        if not p.exchange_ids.get("tp_ok", True):
            hit2 = (h >= p.tp2) if p.side == Side.LONG else (l <= p.tp2)
            if hit2:
                self.close_position(sym, "take_profit_2_engine")
                return
        rules = self.rules.get(sym)
        if not p.tp1_done and amt < p.qty_initial * 0.75:
            p.tp1_done, p.trail_active, p.qty = True, True, amt
            be = p.entry_price * (1 + int(p.side) * self.policy.be_buffer_fee_mult * self.s.fees.taker)
            if (p.side == Side.LONG and be > p.stop) or (p.side == Side.SHORT and be < p.stop):
                self._replace_stop(p, be, "break_even")
        p.best_price = max(p.best_price, h) if p.side == Side.LONG else min(p.best_price, l)
        if p.trail_active and p.atr_at_entry > 0 and rules:
            trail = p.best_price - int(p.side) * self.policy.trail_atr * p.atr_at_entry
            if (p.side == Side.LONG and trail > p.stop + 5 * rules.tick_size) or \
               (p.side == Side.SHORT and trail < p.stop - 5 * rules.tick_size):
                self._replace_stop(p, trail, "trailing")
        if ts - p.opened_at >= self.policy.max_hold_minutes * 60_000:
            self.close_position(sym, "time_stop")
        else:
            self._persist_position(p, True)

    def close_position(self, sym: str, reason: str) -> dict[str, Any]:
        with self._lock:
            return self._close_position_unlocked(sym, reason)

    def _close_position_unlocked(self, sym: str, reason: str) -> dict[str, Any]:
        if sym not in self.positions and sym not in self.execm.ex_pos:
            return {"ok": False, "error": "no position"}
        flat = self.execm.emergency_close(sym, reason)
        return {"ok": flat, "verified_flat": flat}

    def _finalize(self, p: Position, ref_price: float, reason: str) -> None:
        sym = p.symbol
        try:
            self.ex.cancel_all(sym)
        except ExchangeError:
            pass
        try:
            inc = self.ex.realized_since(sym, p.opened_at - 1000)
        except ExchangeError:
            inc = {"realized": p.unrealized(ref_price), "commission": 0.0, "funding": 0.0}
        net = inc["realized"] - inc["commission"] - inc["funding"]
        t = Trade(id=p.id, bot_id=p.bot_id, symbol=sym, side="LONG" if p.side == Side.LONG else "SHORT",
                  qty=p.qty_initial, entry_price=p.entry_price, exit_price=ref_price, opened_at=p.opened_at,
                  closed_at=now_ms(), gross_pnl=inc["realized"], fees=inc["commission"], funding=inc["funding"],
                  net_pnl=net, r_multiple=net / p.risk_amount if p.risk_amount else 0.0, risk_amount=p.risk_amount,
                  exit_reason=reason, regime=p.regime, virtual=False, mode=self.s.mode)
        self.store.insert_trade(t.to_dict())
        self.positions.pop(sym, None)
        self._persist_position(p, False)
        self.allocated_equity += net
        self.store.kv_set(f"capital:{self.s.mode}:{self.s.capital_mode}", self.allocated_equity)
        self.risk.on_trade_closed(net, now_ms())
        ev = self.sup.on_trade(t)
        if ev == "suspended":
            self.save_bot_state(p.bot_id)
            self.alert("warning", "bot", f"{p.bot_id} suspended", self.sup.status_reason.get(p.bot_id, ""))
        self.store.audit("execution_intelligence", "position_closed", {"position_id": p.id, "reason": reason},
                         "n/a", t.to_dict())
        self.bus.publish("trade", t.to_dict())
        self.alert("info", "trade", f"{sym} closed: {net:+.4f} USDT ({t.r_multiple:+.2f}R)", f"{p.bot_id} — {reason}")

    # ------------------------------------------------------ shadow (virtual)
    def _open_virtual(self, s: Signal, price: float, ts: int, md: MarketData) -> None:
        key = (s.bot_id, s.symbol)
        if key in self.virtual:
            return
        slip = self.s.fees.slippage_bps / 1e4
        fill = price * (1 + slip * int(s.side))
        d = abs(fill - s.stop) + fill * 2 * (self.s.fees.taker + slip)
        if d <= 0:
            return
        trend, vol = md.regime_at_1m(len(md.b1) - 1)
        p = Position(id=uuid.uuid4().hex[:12], bot_id=s.bot_id, symbol=s.symbol, side=s.side, qty=0.5 / d,
                     timeframe=s.timeframe,
                     entry_price=fill, stop=s.stop, tp1=s.tp1, tp2=s.tp2, initial_stop=s.stop, opened_at=ts,
                     risk_amount=0.5, qty_initial=0.5 / d, best_price=fill, atr_at_entry=s.features.get("atr", 0.0),
                     signal_id=s.id, regime=regime_key(trend, vol), virtual=True)
        p.fees_paid = p.qty * fill * self.s.fees.taker
        self.virtual[key] = p
        self._persist_position(p, True)

    def _update_virtual(self, sym: str, o: float, h: float, l: float, c: float, ts: int) -> None:
        taker = self.s.fees.taker
        slip = self.s.fees.slippage_bps / 1e4
        for key in [k for k in self.virtual if k[1] == sym]:
            p = self.virtual[key]
            if ts <= p.opened_at:
                continue
            for ev in update_on_bar(p, o, h, l, c, p.atr_at_entry, ts, self.policy, taker):
                if ev.kind == "PARTIAL":
                    px = ev.price * (1 - slip * int(p.side))
                    p.realized_pnl += (px - p.entry_price) * ev.qty * int(p.side)
                    p.fees_paid += ev.qty * px * taker
                    p.qty -= ev.qty
                elif ev.kind == "CLOSE":
                    px = ev.price * (1 - slip * int(p.side))
                    gross = p.unrealized(px) + p.realized_pnl
                    p.fees_paid += p.qty * px * taker
                    net = gross - p.fees_paid
                    t = Trade(id=p.id, bot_id=p.bot_id, symbol=sym, side="LONG" if p.side == Side.LONG else "SHORT",
                              qty=p.qty_initial, entry_price=p.entry_price, exit_price=px, opened_at=p.opened_at,
                              closed_at=ts, gross_pnl=gross, fees=p.fees_paid, funding=0.0, net_pnl=net,
                              r_multiple=net / p.risk_amount, risk_amount=p.risk_amount, exit_reason=ev.reason,
                              regime=p.regime, virtual=True, mode=self.s.mode)
                    self.store.insert_trade(t.to_dict())
                    self.comp.on_virtual_trade(t)
                    res = self.sup.on_trade(t)
                    if res is None and self.sup.status.get(p.bot_id) not in (BotStatus.SUSPENDED, BotStatus.DISABLED):
                        from ..validation import degradation
                        dg = degradation(self.store, p.bot_id, self.s.mode)
                        if dg["degraded"]:
                            self.sup.set_status(p.bot_id, BotStatus.SUSPENDED,
                                                f"degradation vs validation: mean R {dg['recent_mean_r']:.3f} "
                                                f"({dg['basis']})")
                            res = "suspended"
                    if res:
                        self.save_bot_state(p.bot_id)
                        self.alert("warning" if res == "suspended" else "info", "bot",
                                   f"{p.bot_id}: {res}", self.sup.status_reason.get(p.bot_id, ""))
                    self.virtual.pop(key, None)
                    self._persist_position(p, False)
                    break

    # -------------------------------------------------------------- equity
    def _mark_equity(self) -> None:
        unreal = 0.0
        for p in self.positions.values():
            if p.symbol in self.bars:
                unreal += p.unrealized(float(self.bars[p.symbol].close[-1]))
        eq = self.allocated_equity + unreal
        self.risk.update_equity(eq, now_ms())
        minute = now_ms() // MINUTE_MS * MINUTE_MS
        self.store.x("INSERT OR REPLACE INTO equity(ts,mode,equity) VALUES(?,?,?)", (minute, self.s.mode, eq))
        self.bus.publish("equity", {"equity": eq, "allocated": self.allocated_equity, "unrealized": unreal})

    # ------------------------------------------------------------ controls
    def emergency_stop(self, by: str, flatten: bool = True) -> dict[str, Any]:
        with self._lock:
            return self._emergency_stop_unlocked(by, flatten)

    def _emergency_stop_unlocked(self, by: str, flatten: bool = True) -> dict[str, Any]:
        self.risk.emergency(True, f"emergency stop by {by}")
        self.store.kv_set("emergency_stop", True)
        for k in list(self.pending):
            self.pending.pop(k)
            self._close_approval(k, "cancelled_emergency")
        results: dict[str, Any] = {}
        if flatten:
            try:
                symbols = set(self.execm.refresh_positions()) | set(self.positions)
            except ExchangeError as ex:
                symbols = set(self.positions)
                results["_warning"] = f"exchange position list unavailable: {ex}"
            for sym in sorted(symbols):
                results[sym] = {"verified_flat": self.execm.emergency_close(sym, "emergency_stop")}
            for sym in set(self.universe) - symbols:
                try:
                    self.ex.cancel_all(sym)
                except ExchangeError:
                    pass
        failed = [k for k, v in results.items() if isinstance(v, dict) and not v.get("verified_flat")]
        self.store.audit(by, "emergency_stop", {"flatten": flatten}, "executed", results)
        self.alert("critical", "risk", "EMERGENCY STOP",
                   f"by {by}; flatten={flatten}; " + (f"NOT VERIFIED FLAT: {failed}" if failed else "all flat (verified)"))
        return {"ok": not failed, "closed": results, "not_flat": failed}

    def emergency_reset(self, by: str) -> dict[str, Any]:
        with self._lock:
            return self._emergency_reset_unlocked(by)

    def _emergency_reset_unlocked(self, by: str) -> dict[str, Any]:
        self.risk.emergency(False)
        self.store.kv_set("emergency_stop", False)
        self.store.audit(by, "emergency_reset", {}, "owner")
        self.alert("warning", "risk", "Emergency stop cleared", f"by {by}")
        return {"ok": True}

    def status_summary(self) -> dict[str, Any]:
        eq = self.risk.state.equity or self.allocated_equity
        eur = eq / self.s.eur_usdt if self.s.eur_usdt else eq
        start_eur = self.s.starting_capital_eur
        import math
        prog = math.log2(max(eur, 1e-9) / start_eur) / math.log2(self.s.goal_eur / start_eur) if eur > 0 else 0.0
        return {
            "mode": self.s.mode, "execution": self.s.execution, "capital_mode": self.s.capital_mode,
            "equity_usdt": eq, "equity_eur": eur, "start_capital_eur": start_eur,
            "pnl_usdt": eq - self.start_capital_usdt, "daily_pnl_pct": self.risk.daily_pnl_frac * 100,
            "drawdown_pct": self.risk.drawdown * 100, "goal_eur": self.s.goal_eur,
            "goal_progress_doublings": max(0.0, prog) * math.log2(self.s.goal_eur / start_eur),
            "goal_progress_pct": max(0.0, prog) * 100,
            "open_positions": [p.to_dict() for p in self.positions.values()],
            "pending_approvals": [{"signal": v.signal.to_dict(), "decision": v.decision.to_dict(),
                                   "expires": v.expires} for v in self.pending.values()],
            "universe": self.universe, "data_fresh": self.risk.state.data_fresh,
            "connection_ok": self.risk.state.connection_ok, "emergency_stop": self.risk.state.emergency_stop,
            "halted_reason": self.risk.state.halted_reason,
            "active_bots": sum(1 for b in self.comp.books if self.sup.status.get(b) in self.sup.tradable),
            "live_unlocked": bool(self.gate and self.gate.is_valid()),
            "live_gate": self.gate.status() if self.gate else {"valid": False, "reason": "no gate"},
            "data_kind": {"paper": "SIMULATED fills on real prices", "demo": "Binance DEMO (virtual funds)",
                          "live": "REAL MONEY"}[self.s.mode],
            "last_tick_ms": self.last_tick_ms, "errors": self.errors[-5:],
        }


def _j(x: Any) -> str:
    import json
    return json.dumps(x, default=str)
