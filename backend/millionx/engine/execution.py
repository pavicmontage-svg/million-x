"""Execution safety layer.

Invariants enforced here:
  * An entry is claimed in the order journal *before* it is sent (atomic
    INSERT): the same signal can never produce two entries, even across
    restarts.
  * The governance pre-trade check runs immediately before every real order
    with a fresh executable price and the exchange's own position list.
  * Order outcome is resolved against the exchange (query by client id, then
    the exchange position) — an UNKNOWN status is never treated as filled or
    unfilled by assumption, and is never re-sent blindly.
  * Any filled quantity (also partial) is protected at once by an
    exchange-side STOP_MARKET closePosition; the stop is *verified* to exist.
  * If protection cannot be verified, the position is closed at market and
    flatness is verified; if even that fails, a critical incident is raised and
    all new entries are blocked.
  * Size is re-checked against the *actual* fill price; excess risk is reduced
    immediately; a fill beyond the stop is closed.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from ..exchange.base import ExchangeError, OrderResult
from ..governance import PreTradeContext, pre_trade
from ..models import Position, Side, Signal, now_ms
from ..regime import regime_key
from ..risk import SizingDecision

if TYPE_CHECKING:  # pragma: no cover
    from .live import TradingEngine

log = logging.getLogger("millionx.execution")
TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class ExecutionManager:
    def __init__(self, engine: "TradingEngine", sleep=time.sleep):
        self.e = engine
        self.sleep = sleep
        self.ex_pos: dict[str, tuple[float, float]] = {}
        self.ex_pos_ts = 0

    # ------------------------------------------------------------ helpers
    @property
    def ex(self):
        return self.e.ex

    @property
    def store(self):
        return self.e.store

    def incident(self, severity: str, symbol: str, kind: str, detail: str) -> None:
        self.store.x("INSERT INTO incidents(ts,severity,symbol,kind,detail) VALUES(?,?,?,?,?)",
                     (now_ms(), severity, symbol, kind, detail))
        self.store.audit("execution_safety", f"incident:{kind}", {"symbol": symbol, "detail": detail}, severity)
        self.e.alert("critical" if severity == "critical" else "warning", "execution", f"{kind} on {symbol}", detail)

    def refresh_positions(self) -> dict[str, tuple[float, float]]:
        self.ex_pos = self.ex.positions()
        self.ex_pos_ts = now_ms()
        return self.ex_pos

    def _cid(self, prefix: str, *parts: str) -> str:
        from .live import cid
        return cid(prefix, *parts)

    def _journal(self, client_id: str, **kw) -> None:
        self.store.upsert_order({"client_id": client_id, **kw})

    def _entries_since(self, ms: int) -> int:
        r = self.store.one("SELECT COUNT(*) n FROM orders WHERE purpose='entry' AND mode=? AND created_at>? "
                           "AND status IN ('SUBMITTED','NEW','FILLED','PARTIALLY_FILLED','FILLED_VIA_POSITION','UNKNOWN','CANCELED','EXPIRED')", (self.e.s.mode, now_ms() - ms))
        return int(r["n"]) if r else 0

    # ------------------------------------------------------- pre-trade
    def pretrade(self, s: Signal, dec: SizingDecision) -> tuple[list[str], dict[str, Any]]:
        side = "BUY" if s.side == Side.LONG else "SELL"
        try:
            book = self.ex.order_book(s.symbol, 20)
            exec_px = book.asks[0][0] if side == "BUY" else book.bids[0][0]
            positions = self.refresh_positions()
        except ExchangeError as ex:
            return [f"cannot verify market/positions before order: {ex}"], {}
        self.e.books[s.symbol] = book
        bid_d, ask_d = book.depth_within(10)
        rules = self.e.rules.get(s.symbol)
        taker = rules.taker if rules and rules.taker is not None else self.e.s.fees.taker
        ctx = PreTradeContext(
            symbol=s.symbol, side=int(s.side), qty=dec.qty, leverage=dec.leverage, signal_price=s.entry,
            stop=s.stop, tp1=s.tp1, exec_price=exec_px, equity=self.e.risk.state.equity,
            risk_fraction=dec.risk_fraction if dec.risk_fraction > 0 else self.e.risk.risk_fraction(),
            taker_fee=taker, exchange_positions=positions, spread_bps=book.spread_bps,
            depth_usdt=ask_d if side == "BUY" else bid_d,
            entries_last_minute=self._entries_since(60_000), entries_last_hour=self._entries_since(3_600_000),
            data_fresh=self.e.risk.state.data_fresh, connection_ok=self.e.risk.state.connection_ok,
            emergency=self.e.risk.state.emergency_stop, live_mode=self.e.s.mode == "live",
            live_gate_ok=self.e.gate.is_valid() if self.e.gate else False, corr=self.e._price_corr)
        ok_cro, why = True, ""
        if self.e.org is not None:
            ok_cro, _, why = self.e.org.cro_review(self.e, s, dec)
        v = pre_trade(self.e.risk.limits, ctx)
        if not self.ex.is_paper:
            # real Binance balance before every order: margin for this order must be available
            try:
                bal = self.ex.balance()
                need = dec.notional / max(1, dec.leverage) * 1.05 + dec.expected_cost
                if bal.get("available", 0.0) < need:
                    v.append(f"Binance available balance {bal.get('available', 0.0):.2f} < required margin {need:.2f} USDT")
            except ExchangeError as ex:
                v.append(f"cannot read Binance balance before order: {ex}")
        if not ok_cro:
            v.append(f"CRO veto: {why}")
        return v, {"exec_price": exec_px, "spread_bps": book.spread_bps, "depth": ctx.depth_usdt}

    # ---------------------------------------------------------- execute
    def execute(self, s: Signal, dec: SizingDecision, steps: list[dict[str, Any]]) -> dict[str, Any]:
        e = self.e
        sym = s.symbol
        side = "BUY" if s.side == Side.LONG else "SELL"
        entry_id = self._cid("E", s.id)
        if not self.store.claim_order(entry_id, sym, side, "MARKET", dec.qty, "entry", e.s.mode):
            steps.append({"step": 7, "dept": "execution", "result": "duplicate_blocked", "client_id": entry_id})
            e._log_decision(s, steps, "duplicate_blocked")
            return {"ok": False, "error": "duplicate order prevented"}
        violations, info = self.pretrade(s, dec)
        steps.append({"step": 6.5, "dept": "risk_governance", "result": "pass" if not violations else "blocked",
                      "violations": violations, **info})
        if violations:
            self._journal(entry_id, status="REJECTED_PRETRADE", raw={"violations": violations})
            e._log_decision(s, steps, "blocked_by_governance")
            return {"ok": False, "error": "; ".join(violations)}
        decision_px = info["exec_price"]
        try:
            if not self.ex.is_paper and e.leverage_set.get(sym) != dec.leverage:
                self.ex.set_isolated(sym)
                self.ex.set_leverage(sym, dec.leverage)
                e.leverage_set[sym] = dec.leverage
        except ExchangeError as ex:
            self._journal(entry_id, status="REJECTED", raw={"error": f"leverage: {ex}"})
            e._log_decision(s, steps, "execution_error")
            return {"ok": False, "error": str(ex)}
        t_submit = now_ms()
        self._journal(entry_id, status="SUBMITTED")
        try:
            r = self.ex.market_order(sym, side, dec.qty, entry_id)
        except ExchangeError as ex:
            if not ex.retriable:
                self._journal(entry_id, status="REJECTED", raw={"error": str(ex)})
                steps.append({"step": 7, "dept": "execution", "result": "rejected", "error": str(ex)})
                e._log_decision(s, steps, "rejected_by_exchange")
                return {"ok": False, "error": str(ex)}
            r = OrderResult(entry_id, None, sym, side, "MARKET", "UNKNOWN", dec.qty)
        filled, avg, status = self.resolve_entry(sym, entry_id, side, dec.qty, r)
        self._journal(entry_id, status=status, exchange_id=r.exchange_id, price=avg, filled_qty=filled,
                      avg_price=avg, raw=r.raw)
        steps.append({"step": 7, "dept": "execution", "result": "submitted", "client_id": entry_id})
        steps.append({"step": 8, "dept": "exchange", "result": status, "filled_qty": filled, "avg_price": avg})
        if filled <= 0:
            outcome = "status_unknown_pending_reconciliation" if status == "UNKNOWN" else "not_filled"
            if status == "UNKNOWN":
                self.incident("critical", sym, "order_status_unknown",
                              f"{entry_id}: outcome could not be confirmed; reconciliation continues, no re-send")
            e._log_decision(s, steps, outcome)
            return {"ok": False, "status": status}
        rules = e.rules[sym]
        md = e.mds.get(sym)
        trend, vol = md.regime_at_1m(len(md.b1) - 1) if md else ("SIDEWAYS", "NORMAL_VOL")
        p = Position(id=s.id, bot_id=s.bot_id, symbol=sym, side=s.side, qty=filled, entry_price=avg, stop=s.stop,
                     timeframe=s.timeframe,
                     tp1=s.tp1, tp2=s.tp2, initial_stop=s.stop, opened_at=t_submit, leverage=dec.leverage,
                     risk_amount=dec.risk_amount, qty_initial=filled, best_price=avg,
                     atr_at_entry=s.features.get("atr", 0.0), signal_id=s.id, regime=regime_key(trend, vol))
        if status != "FILLED":
            self.incident("warning", sym, "partial_fill", f"{entry_id}: filled {filled} of {dec.qty} ({status})")
        e.positions[sym] = p
        e._persist_position(p, True)
        # fill beyond the stop: never hold
        if (p.side == Side.LONG and avg <= p.stop) or (p.side == Side.SHORT and avg >= p.stop):
            self.incident("warning", sym, "filled_beyond_stop", f"avg {avg} vs stop {p.stop}; closing")
            self.emergency_close(sym, "filled_beyond_stop")
            e._log_decision(s, steps, "closed_fill_beyond_stop")
            return {"ok": False, "error": "fill beyond stop"}
        if not self.protect(p):
            steps.append({"step": 8.5, "dept": "execution_safety", "result": "protection_failed_closed"})
            e._log_decision(s, steps, "closed_unprotectable")
            return {"ok": False, "error": "protection could not be verified; position closed"}
        self.post_fill_risk(p, decision_px)
        steps.append({"step": 8.5, "dept": "execution_safety", "result": "protected", "stop": p.stop,
                      "risk_amount": p.risk_amount})
        e._persist_position(p, True)
        e._log_decision(s, steps, "executed")
        self.store.audit("execution_intelligence", "position_opened", {"signal": s.to_dict(), "decision": dec.to_dict()},
                         "approved", p.to_dict())
        e.bus.publish("position_opened", p.to_dict())
        e.alert("info", "trade", f"{p.side.name} {sym} opened ({s.bot_id})",
                f"qty {p.qty} @ {p.entry_price:.6g}, stop {p.stop:.6g} (verified), lev {p.leverage}x")
        return {"ok": True, "position": p.to_dict()}

    def resolve_entry(self, sym: str, cid: str, side: str, req_qty: float, r: OrderResult) -> tuple[float, float, str]:
        """Determine the real outcome of an entry order from the exchange."""
        for attempt in range(6):
            if r.status in TERMINAL:
                break
            if r.status == "PARTIALLY_FILLED" and attempt >= 2:
                # a market order should not rest; cancel the remainder and keep the fill
                try:
                    self.ex.cancel_order(sym, cid)
                except ExchangeError:
                    pass
            self.sleep(0.3 * (attempt + 1))
            try:
                r = self.ex.query_order(sym, cid)
            except ExchangeError as ex:
                if ex.code == -2013 and attempt >= 2:
                    # exchange says the order does not exist: it was not placed
                    return 0.0, 0.0, "NOT_PLACED"
                r = OrderResult(cid, r.exchange_id, sym, side, "MARKET", "UNKNOWN", req_qty)
        if r.status in TERMINAL or r.status == "PARTIALLY_FILLED":
            return float(r.filled_qty), float(r.avg_price), r.status
        # still unknown: the exchange position is the source of truth
        try:
            pos = self.refresh_positions().get(sym)
        except ExchangeError:
            return 0.0, 0.0, "UNKNOWN"
        if pos and (pos[0] > 0) == (side == "BUY") and sym not in self.e.positions:
            return abs(pos[0]), pos[1], "FILLED_VIA_POSITION"
        return 0.0, 0.0, "UNKNOWN"

    # -------------------------------------------------------- protection
    def _stop_verified(self, sym: str, client_id: str) -> bool:
        try:
            r = self.ex.query_conditional(sym, client_id)
            if r.status in ("NEW", "WORKING", "ACCEPTED"):
                return True
        except ExchangeError:
            pass
        try:
            return any(o["client_id"] == client_id for o in self.ex.open_conditionals(sym))
        except ExchangeError:
            return False

    def place_stop(self, p: Position, price: float, version: int) -> Optional[str]:
        rules = self.e.rules.get(p.symbol)
        exit_side = "SELL" if p.side == Side.LONG else "BUY"
        trig = rules.round_price(price) if rules else price
        for attempt in range(2):
            sid = self._cid("S", p.id, str(version), str(attempt))
            try:
                self.ex.conditional_order(p.symbol, exit_side, "STOP_MARKET", trig, sid, close_position=True)
            except ExchangeError as ex:
                log.warning("stop placement %s failed: %s", sid, ex)
                continue
            if self._stop_verified(p.symbol, sid):
                return sid
        return None

    def protect(self, p: Position) -> bool:
        sid = self.place_stop(p, p.stop, 0)
        if not sid:
            self.incident("critical", p.symbol, "stop_not_verified",
                          "exchange-side stop could not be placed/verified — closing position")
            self.emergency_close(p.symbol, "stop_not_verified")
            return False
        p.exchange_ids.update(sl=sid, sl_v=0, sl_verified_at=now_ms())
        self.place_targets(p)
        return True

    def place_targets(self, p: Position) -> None:
        rules = self.e.rules.get(p.symbol)
        exit_side = "SELL" if p.side == Side.LONG else "BUY"
        ok = True
        try:
            if not p.tp1_done and rules:
                q1 = rules.round_qty(p.qty * self.e.policy.partial_fraction)
                if q1 >= max(rules.min_qty, 1e-12):
                    t1 = self._cid("T1", p.id, str(p.qty))
                    self.ex.conditional_order(p.symbol, exit_side, "TAKE_PROFIT_MARKET", rules.round_price(p.tp1),
                                              t1, qty=q1)
                    p.exchange_ids["tp1"] = t1
            t2 = self._cid("T2", p.id)
            self.ex.conditional_order(p.symbol, exit_side, "TAKE_PROFIT_MARKET",
                                      rules.round_price(p.tp2) if rules else p.tp2, t2, close_position=True)
            p.exchange_ids["tp2"] = t2
        except ExchangeError as ex:
            ok = False
            self.incident("warning", p.symbol, "take_profit_not_placed",
                          f"{ex}; engine will manage targets from bar data")
        p.exchange_ids["tp_ok"] = ok

    def replace_stop(self, p: Position, new_stop: float, reason: str) -> bool:
        v = int(p.exchange_ids.get("sl_v", 0)) + 1
        old = p.exchange_ids.get("sl")
        sid = self.place_stop(p, new_stop, v)
        if sid:
            if old:
                try:
                    self.ex.cancel_conditional(p.symbol, old)
                except ExchangeError:
                    pass
        else:
            # venue may allow only one close-position stop: cancel old, place new, verify
            try:
                if old:
                    self.ex.cancel_conditional(p.symbol, old)
            except ExchangeError:
                pass
            sid = self.place_stop(p, new_stop, v + 100)
            if not sid:
                # restore the previous protection level, then escalate
                sid = self.place_stop(p, p.stop, v + 200)
                if not sid:
                    self.incident("critical", p.symbol, "stop_update_failed", "no verified stop — closing")
                    self.emergency_close(p.symbol, "stop_update_failed")
                    return False
                self.incident("warning", p.symbol, "stop_update_failed", "previous stop level restored")
                p.exchange_ids.update(sl=sid, sl_v=v + 200)
                return False
        p.exchange_ids.update(sl=sid, sl_v=v, sl_verified_at=now_ms())
        p.stop = new_stop
        self.e._persist_position(p, True)
        self.e.bus.publish("stop_moved", {"symbol": p.symbol, "stop": new_stop, "reason": reason})
        return True

    def post_fill_risk(self, p: Position, decision_px: float) -> None:
        e = self.e
        rules = e.rules[p.symbol]
        slip_bps = (p.entry_price - decision_px) / decision_px * 1e4 * int(p.side)
        if slip_bps > e.risk.limits.max_slippage_bps:
            self.incident("warning", p.symbol, "slippage_limit_breached",
                          f"{slip_bps:.1f} bps > {e.risk.limits.max_slippage_bps}")
        taker = rules.taker if rules.taker is not None else e.s.fees.taker
        per_unit = abs(p.entry_price - p.stop) + p.entry_price * 2 * taker
        allowed = e.risk.state.equity * e.risk.risk_fraction()
        actual = p.qty * per_unit
        p.risk_amount = actual
        if actual > allowed * 1.25 and per_unit > 0:
            keep = rules.round_qty(allowed / per_unit)
            excess = rules.round_qty(p.qty - keep)
            if excess > 0 and keep * p.entry_price >= rules.min_notional:
                exit_side = "SELL" if p.side == Side.LONG else "BUY"
                try:
                    self.ex.market_order(p.symbol, exit_side, excess, self._cid("R", p.id), reduce_only=True)
                    amt = abs(self.refresh_positions().get(p.symbol, (0.0, 0.0))[0])
                    p.qty = p.qty_initial = amt
                    p.risk_amount = amt * per_unit
                    self.incident("warning", p.symbol, "size_reduced_after_fill",
                                  f"risk {actual:.4f} > {allowed:.4f}; reduced by {excess}")
                    # targets were sized on the old quantity
                    for k in ("tp1", "tp2"):
                        if p.exchange_ids.get(k):
                            try:
                                self.ex.cancel_conditional(p.symbol, p.exchange_ids[k])
                            except ExchangeError:
                                pass
                    self.place_targets(p)
                except ExchangeError as ex:
                    self.incident("warning", p.symbol, "size_reduction_failed", str(ex))
            elif actual > allowed * 2:
                self.incident("critical", p.symbol, "risk_far_above_limit", "cannot reduce below minimum; closing")
                self.emergency_close(p.symbol, "risk_far_above_limit")

    # ------------------------------------------------------ closing
    def emergency_close(self, sym: str, reason: str) -> bool:
        """Close at market and verify the exchange position is flat."""
        flat = False
        for attempt in range(3):
            try:
                amt = self.refresh_positions().get(sym, (0.0, 0.0))[0]
                if amt == 0:
                    flat = True
                    break
                side = "SELL" if amt > 0 else "BUY"
                self.ex.market_order(sym, side, abs(amt), self._cid("X", sym, reason, str(now_ms()), str(attempt)),
                                     reduce_only=True)
            except ExchangeError as ex:
                log.error("emergency close %s attempt %d: %s", sym, attempt, ex)
            self.sleep(0.5 * (attempt + 1))
        if not flat:
            try:
                flat = self.refresh_positions().get(sym, (0.0, 0.0))[0] == 0
            except ExchangeError:
                flat = False
        try:
            self.ex.cancel_all(sym)
        except ExchangeError:
            pass
        if not flat:
            self.e.risk.emergency(True, f"unable to flatten {sym}")
            self.store.kv_set("emergency_stop", True)
            self.incident("critical", sym, "close_not_verified",
                          "position may still be open WITHOUT verified protection — manual action required; "
                          "all new entries blocked")
        p = self.e.positions.get(sym)
        if p and flat:
            ref = float(self.e.bars[sym].close[-1]) if sym in self.e.bars and len(self.e.bars[sym]) else p.entry_price
            self.e._finalize(p, ref, reason)
        return flat

    # --------------------------------------------------- reconciliation
    def reconcile(self, startup: bool = False) -> dict[str, Any]:
        """Exchange is the source of truth. Runs at startup and on every bar."""
        e = self.e
        report: dict[str, Any] = {"finalized": [], "adopted": [], "qty_fixed": [], "stop_restored": [], "orders": []}
        try:
            ex_pos = self.refresh_positions()
        except ExchangeError as ex:
            e.errors.append(f"reconcile: {ex}")
            return {"error": str(ex)}
        # 1) journal entries whose outcome is not yet known
        for o in self.store.q("SELECT * FROM orders WHERE purpose='entry' AND mode=? AND status IN "
                              "('SUBMITTED','SUBMITTING','UNKNOWN','NEW','PARTIALLY_FILLED')", (e.s.mode,)):
            if now_ms() - int(o["created_at"] or 0) < 5_000 and not startup:
                continue
            try:
                r = self.ex.query_order(o["symbol"], o["client_id"])
                self._journal(o["client_id"], status=r.status, filled_qty=r.filled_qty, avg_price=r.avg_price,
                              exchange_id=r.exchange_id, raw=r.raw)
                report["orders"].append((o["client_id"], r.status))
            except ExchangeError as ex:
                if ex.code == -2013:
                    self._journal(o["client_id"], status="NOT_PLACED")
                    report["orders"].append((o["client_id"], "NOT_PLACED"))
        # 2) internal positions closed on the exchange
        for sym in list(e.positions):
            if sym not in ex_pos:
                p = e.positions[sym]
                ref = float(e.bars[sym].close[-1]) if sym in e.bars and len(e.bars[sym]) else p.entry_price
                e._finalize(p, ref, "exchange_exit")
                report["finalized"].append(sym)
        # 3) exchange positions unknown to the engine (orphans) -> protect or close
        for sym, (amt, entry) in ex_pos.items():
            p = e.positions.get(sym)
            if p is None:
                report["adopted"].append(sym)
                self.adopt_orphan(sym, amt, entry)
                continue
            if (amt > 0) != (p.side == Side.LONG):
                self.incident("critical", sym, "direction_mismatch", f"engine {p.side.name}, exchange {amt}")
                self.emergency_close(sym, "direction_mismatch")
                continue
            if abs(abs(amt) - p.qty) > 1e-12:
                if abs(amt) > p.qty * 1.0001:
                    self.incident("warning", sym, "quantity_increased", f"engine {p.qty}, exchange {abs(amt)}")
                report["qty_fixed"].append((sym, p.qty, abs(amt)))
                p.qty = abs(amt)
                e._persist_position(p, True)
            # 4) verify the stop still exists
            sid = p.exchange_ids.get("sl")
            if not sid or not self._stop_verified(sym, sid):
                self.incident("warning", sym, "stop_missing", "exchange-side stop not found; re-placing")
                new = self.place_stop(p, p.stop, int(p.exchange_ids.get("sl_v", 0)) + 300)
                if new:
                    p.exchange_ids.update(sl=new, sl_verified_at=now_ms())
                    e._persist_position(p, True)
                    report["stop_restored"].append(sym)
                else:
                    self.emergency_close(sym, "stop_missing_unrestorable")
            else:
                p.exchange_ids["sl_verified_at"] = now_ms()
        if startup or any(report[k] for k in ("finalized", "adopted", "qty_fixed", "stop_restored")):
            self.store.audit("execution_safety", "reconciliation", {"startup": startup}, "n/a", report)
        return report

    def adopt_orphan(self, sym: str, amt: float, entry: float) -> None:
        e = self.e
        side = Side.LONG if amt > 0 else Side.SHORT
        if sym not in e.rules:
            try:
                e.rules.update({k: v for k, v in e.ex.symbols().items() if k == sym})
            except ExchangeError:
                pass
        # protective stop: 2 x ATR(1m) or 1 %, whichever is wider, from the current price
        ref = float(e.bars[sym].close[-1]) if sym in e.bars and len(e.bars[sym]) else entry
        dist = 0.01 * ref
        if sym in e.bars and len(e.bars[sym]) > 20:
            from .. import indicators as ind
            a = ind.atr(e.bars[sym], 14)[-1]
            if a == a:
                dist = max(dist, 2 * float(a))
        stop = ref - int(side) * dist
        p = Position(id=f"orphan-{sym}-{now_ms()}", bot_id="orphan", symbol=sym, side=side, qty=abs(amt),
                     entry_price=entry or ref, stop=stop, tp1=ref + int(side) * 50 * dist,
                     tp2=ref + int(side) * 100 * dist, initial_stop=stop, opened_at=now_ms(), qty_initial=abs(amt),
                     best_price=ref, tp1_done=True, regime="UNKNOWN")
        e.positions[sym] = p
        e._persist_position(p, True)
        self.incident("critical", sym, "orphan_position",
                      f"exchange position {amt} @ {entry} not known to the engine — protecting with stop {stop:.6g}")
        if not self.protect(p):
            return
        p.exchange_ids["orphan"] = True
        e._persist_position(p, True)
