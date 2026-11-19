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
                 notifier=None, org=None, poll_seconds: float = 5.0):
        self.s = settings
        self.store = store
        self.ex = exchange
        self.bus = bus or EventBus()
        self.notifier = notifier
        self.org = org
        self.poll = poll_seconds
        self.policy = ExitPolicy()
        self.comp = CompetitionEngine(list(ALL_IDS))
        self.sup = Supervisor(self.comp, tradable_statuses=self._tradable())
        self.risk = RiskManager(settings.risk, settings.fees)
        self.rules: dict[str, SymbolRules] = {}
        self.universe: list[str] = []
        self.scanner: dict[str, dict[str, Any]] = {}
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
        self._load_state()
        self.risk.reset(self.allocated_equity, now_ms())
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
        tick = self.ex.tickers_24h()
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
            b = self.ex.klines(sym, WINDOW)
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
        merged = Bars(*(np.concatenate([getattr(old, f), getattr(add, f)])[-WINDOW:] for f in
                        ("open_time", "open", "high", "low", "close", "volume", "taker_buy_volume", "quote_volume")))
        self.bars[sym] = merged
        self.last_bar_ms[sym] = int(merged.open_time[-1])
        return True

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
                log.error("tick failed: %s\n%s", msg, traceback.format_exc())
            self._stop.wait(max(0.5, self.poll - (time.time() - t0)))

    def tick(self) -> None:
        now = now_ms()
        if not self.universe or now - self.last_universe_refresh > self.s.universe_refresh_minutes * 60_000:
            self.refresh_universe()
            self._load_fees()
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

    # ------------------------------------------------------------- per bar
    def on_bar(self, sym: str) -> None:
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
        out = {}
        for r in self.store.q("SELECT bot_id, params FROM bot_state"):
            try:
                import json
                p = json.loads(r["params"] or "{}")
                if p:
                    out[r["bot_id"]] = p
            except ValueError:
                pass
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
            return
        dec = self.risk.size(s, rules, list(self.positions.values()), md.atr_pct15_at_1m(len(md.b1) - 1), ts,
                             v.size_multiplier * self._budget_multiplier(s.bot_id))
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
        if cro_ok and self.s.mode == "live" and not self.s.live_trading_unlocked:
            cro_ok, cro_reason = False, ["live trading locked: preflight checks not passed"]
        steps.append({"step": 6, "dept": "cro", "result": "approved" if cro_ok else "vetoed", "reasons": cro_reason})
        if not cro_ok:
            self._log_decision(s, steps, "vetoed")
            return
        if self.s.execution == "manual":
            self.pending[s.id] = PendingApproval(s, dec, now_ms(), now_ms() + 90_000, {"steps": steps})
            self.alert("info", "approval", f"Approve {s.bot_id} {s.side.name} {s.symbol}?",
                       f"qty {dec.qty} lev {dec.leverage}x risk {dec.risk_amount:.4f} USDT (expires in 90 s)")
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
        if book.spread_bps > self.s.max_spread_bps:
            return {"ok": False, "reason": f"spread {book.spread_bps:.2f}bps > {self.s.max_spread_bps}"}
        side_depth = book.depth_within(10)[1 if s.side == Side.LONG else 0]
        if dec.notional > 0.1 * side_depth:
            return {"ok": False, "reason": "order would exceed 10% of depth within 10 bps"}
        return {"ok": True, "order_type": "MARKET", "spread_bps": book.spread_bps, "depth_10bps": side_depth,
                "why": "scalp entry needs certainty of fill; spread and depth within limits"}

    def approve(self, signal_id: str, approved: bool, by: str) -> dict[str, Any]:
        with self._lock:
            return self._approve_unlocked(signal_id, approved, by)

    def _approve_unlocked(self, signal_id: str, approved: bool, by: str) -> dict[str, Any]:
        p = self.pending.pop(signal_id, None)
        if not p:
            return {"ok": False, "error": "not pending or expired"}
        self.store.x("UPDATE signals SET approval=?, decided_by=? WHERE id=?",
                     ("approved" if approved else "rejected", by, signal_id))
        self.store.audit(by, "manual_trade_approval", {"signal_id": signal_id}, "approved" if approved else "rejected")
        if not approved:
            return {"ok": True, "status": "rejected"}
        ok, why = self.risk.can_open(now_ms())
        if not ok:
            return {"ok": False, "error": f"risk state changed: {why}"}
        return self._execute(p.signal, p.decision, p.record.get("steps", []))

    def _expire_approvals(self) -> None:
        now = now_ms()
        for k in [k for k, v in self.pending.items() if v.expires < now]:
            p = self.pending.pop(k)
            self.store.x("UPDATE signals SET approval='expired' WHERE id=?", (k,))
            self.bus.publish("approval_expired", {"signal_id": k})

    # ------------------------------------------------------------ execution
    def _execute(self, s: Signal, dec: SizingDecision, steps: list[dict[str, Any]]) -> dict[str, Any]:
        sym = s.symbol
        side = "BUY" if s.side == Side.LONG else "SELL"
        exit_side = "SELL" if side == "BUY" else "BUY"
        entry_id = cid("E", s.id)
        if self.store.one("SELECT client_id FROM orders WHERE client_id=?", (entry_id,)):
            return {"ok": False, "error": "duplicate order prevented"}
        try:
            if not self.ex.is_paper and self.leverage_set.get(sym) != dec.leverage:
                self.ex.set_isolated(sym)
                self.ex.set_leverage(sym, dec.leverage)
                self.leverage_set[sym] = dec.leverage
            self.store.upsert_order({"client_id": entry_id, "symbol": sym, "side": side, "type": "MARKET",
                                     "qty": dec.qty, "status": "SUBMITTING", "purpose": "entry", "mode": self.s.mode})
            t_submit = now_ms()
            r = self.ex.market_order(sym, side, dec.qty, entry_id)
            for _ in range(5):
                if r.status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    break
                time.sleep(0.4)
                r = self.ex.query_order(sym, entry_id)
        except ExchangeError as e:
            self.store.upsert_order({"client_id": entry_id, "symbol": sym, "side": side, "type": "MARKET",
                                     "qty": dec.qty, "status": "ERROR", "purpose": "entry", "mode": self.s.mode,
                                     "raw": {"error": str(e)}})
            steps.append({"step": 7, "dept": "execution", "result": "error", "error": str(e)})
            self._log_decision(s, steps, "execution_error")
            self.alert("warning", "execution", f"Entry failed {sym}", str(e))
            return {"ok": False, "error": str(e)}
        self.store.upsert_order({"client_id": entry_id, "exchange_id": r.exchange_id, "symbol": sym, "side": side,
                                 "type": "MARKET", "qty": dec.qty, "price": r.avg_price, "status": r.status,
                                 "purpose": "entry", "mode": self.s.mode, "raw": r.raw})
        steps.append({"step": 7, "dept": "execution", "result": "submitted", "client_id": entry_id})
        if r.status != "FILLED" or r.filled_qty <= 0:
            steps.append({"step": 8, "dept": "exchange", "result": "not_filled", "status": r.status})
            self._log_decision(s, steps, "not_filled")
            if r.status == "UNKNOWN":
                self.alert("critical", "execution", f"Order status unknown {sym}",
                           "Will reconcile with the exchange; no retry until confirmed.")
            return {"ok": False, "status": r.status}
        steps.append({"step": 8, "dept": "exchange", "result": "confirmed_fill", "avg_price": r.avg_price,
                      "qty": r.filled_qty})
        rules = self.rules[sym]
        md = self.mds.get(sym)
        trend, vol = md.regime_at_1m(len(md.b1) - 1) if md else ("SIDEWAYS", "NORMAL_VOL")
        p = Position(id=s.id, bot_id=s.bot_id, symbol=sym, side=s.side, qty=r.filled_qty, entry_price=r.avg_price,
                     stop=s.stop, tp1=s.tp1, tp2=s.tp2, initial_stop=s.stop, opened_at=t_submit,
                     leverage=dec.leverage, risk_amount=dec.risk_amount, qty_initial=r.filled_qty,
                     best_price=r.avg_price, atr_at_entry=s.features.get("atr", 0.0), signal_id=s.id,
                     regime=regime_key(trend, vol))
        # protective orders
        try:
            sl = self.ex.conditional_order(sym, exit_side, "STOP_MARKET", rules.round_price(p.stop), cid("S", s.id, "0"),
                                           close_position=True)
            p.exchange_ids["sl"] = sl.client_id
            p.exchange_ids["sl_v"] = 0
        except ExchangeError as e:
            self.alert("critical", "execution", f"Stop could not be placed on {sym} — closing",
                       f"{e}. Position closed immediately to avoid an unprotected position.")
            self.ex.market_order(sym, exit_side, p.qty, cid("X", s.id), reduce_only=True)
            steps.append({"step": 8, "dept": "execution", "result": "closed_no_stop", "error": str(e)})
            self._log_decision(s, steps, "closed_no_stop")
            return {"ok": False, "error": "stop placement failed; position closed"}
        try:
            q1 = rules.round_qty(p.qty * self.policy.partial_fraction)
            if q1 >= rules.min_qty and q1 > 0:
                tp1 = self.ex.conditional_order(sym, exit_side, "TAKE_PROFIT_MARKET", rules.round_price(p.tp1),
                                                cid("T1", s.id), qty=q1)
                p.exchange_ids["tp1"] = tp1.client_id
            tp2 = self.ex.conditional_order(sym, exit_side, "TAKE_PROFIT_MARKET", rules.round_price(p.tp2),
                                            cid("T2", s.id), close_position=True)
            p.exchange_ids["tp2"] = tp2.client_id
        except ExchangeError as e:
            self.alert("warning", "execution", f"Take-profit order failed on {sym}", str(e))
        self.positions[sym] = p
        self._persist_position(p, True)
        self._log_decision(s, steps, "executed")
        self.store.audit("execution_intelligence", "position_opened", {"signal": s.to_dict(), "decision": dec.to_dict()},
                         "approved", p.to_dict())
        self.bus.publish("position_opened", p.to_dict())
        self.alert("info", "trade", f"{p.side.name} {sym} opened ({s.bot_id})",
                   f"qty {p.qty} @ {p.entry_price:.6g}, stop {p.stop:.6g}, lev {p.leverage}x")
        return {"ok": True, "position": p.to_dict()}

    def _log_decision(self, s: Signal, steps: list[dict[str, Any]], outcome: str) -> None:
        self.store.audit("decision_pipeline", "trade_decision", {"signal_id": s.id, "bot": s.bot_id,
                         "symbol": s.symbol, "steps": steps}, outcome)
        self.bus.publish("decision", {"signal_id": s.id, "outcome": outcome, "steps": steps})

    def _replace_stop(self, p: Position, new_stop: float, reason: str) -> None:
        rules = self.rules.get(p.symbol)
        if rules is None:
            return
        exit_side = "SELL" if p.side == Side.LONG else "BUY"
        v = int(p.exchange_ids.get("sl_v", 0)) + 1
        new_id = cid("S", p.id, str(v))
        old_id = p.exchange_ids.get("sl")
        try:
            self.ex.conditional_order(p.symbol, exit_side, "STOP_MARKET", rules.round_price(new_stop), new_id,
                                      close_position=True)
            if old_id:
                self.ex.cancel_conditional(p.symbol, old_id)
        except ExchangeError:
            # some venues allow one close-position stop only: cancel then place
            try:
                if old_id:
                    self.ex.cancel_conditional(p.symbol, old_id)
                self.ex.conditional_order(p.symbol, exit_side, "STOP_MARKET", rules.round_price(new_stop), new_id,
                                          close_position=True)
            except ExchangeError as e:
                self.alert("critical", "execution", f"Stop update failed on {p.symbol} — closing", str(e))
                self.close_position(p.symbol, "stop_update_failed")
                return
        p.exchange_ids["sl"], p.exchange_ids["sl_v"] = new_id, v
        p.stop = new_stop
        self._persist_position(p, True)
        self.bus.publish("stop_moved", {"symbol": p.symbol, "stop": new_stop, "reason": reason})

    def _manage_position(self, sym: str, o: float, h: float, l: float, c: float, ts: int) -> None:
        p = self.positions[sym]
        try:
            amt = abs(self.ex.position_amount(sym))
        except ExchangeError as e:
            self.errors.append(f"position sync {sym}: {e}")
            return
        if amt <= 0:
            self._finalize(p, c, "exchange_exit")
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
        p = self.positions.get(sym)
        if not p:
            return {"ok": False, "error": "no position"}
        exit_side = "SELL" if p.side == Side.LONG else "BUY"
        try:
            amt = abs(self.ex.position_amount(sym))
            if amt > 0:
                self.ex.market_order(sym, exit_side, amt, cid("X", p.id, reason), reduce_only=True)
            self.ex.cancel_all(sym)
        except ExchangeError as e:
            self.alert("critical", "execution", f"Close failed on {sym}", str(e))
            return {"ok": False, "error": str(e)}
        price = float(self.bars[sym].close[-1]) if sym in self.bars else p.entry_price
        self._finalize(p, price, reason)
        return {"ok": True}

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
        self.pending.clear()
        results = {}
        if flatten:
            for sym in list(self.positions):
                results[sym] = self.close_position(sym, "emergency_stop")
        self.store.audit(by, "emergency_stop", {"flatten": flatten}, "executed", results)
        self.alert("critical", "risk", "EMERGENCY STOP", f"by {by}; flatten={flatten}")
        return {"ok": True, "closed": results}

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
            "live_unlocked": self.s.live_trading_unlocked, "errors": self.errors[-5:],
        }


def _j(x: Any) -> str:
    import json
    return json.dumps(x, default=str)
