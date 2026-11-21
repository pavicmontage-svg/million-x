"""Unified copy-trading engine (Forex + Crypto).

Write-ahead execution: an Order row (with a deterministic client order id) is committed BEFORE the
venue is called. If the process dies or the network fails mid-request, the reconciler finds the
order by client id on the venue and completes or rejects it. Re-delivered signals map to the same
client ids, so they can never open a second position.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.base import AdapterError, AdapterUnavailable, ExecutionAdapter, OpenRequest, OrderResult, VenuePosition
from ..audit import audit
from ..config import get_settings
from ..db import session_scope
from ..events import emit
from ..markets import crypto as cm
from ..markets import forex as fx
from ..models import (
    Allocation, Order, OrderStatus, Portfolio, PortfolioStatus, Position, PositionStatus, RiskEvent, Signal, SignalKind,
    SignalStatus, Trader, TraderStatus,
)
from ..notify import notify
from ..risk.engine import SizedTrade, TradeIntent, post_size, pre_trade
from ..services import state as st
from ..services.portfolio import build_state, limits_for, minutes_to_rollover, risk_at_stop

log = logging.getLogger("titan.copy")
MAX_ENTRY_DRIFT_R = 0.3     # reject entries whose price moved > 0.3R against us since the source fill
UNKNOWN_ORDER_TIMEOUT = timedelta(minutes=10)


def client_id(*parts) -> str:
    """28-char id valid for Binance (^[.A-Z:/a-z0-9_-]{1,36}$ incl. suffixes), MT5 (31) and cTrader (50)."""
    return "tc" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:26]


@dataclass
class Outcome:
    portfolio_id: int
    action: str                 # opened / closed / modified / rejected / skipped / failed / unknown
    reason: str | None = None
    detail: dict = field(default_factory=dict)


class CopyEngine:
    def __init__(self, adapters: dict[int, ExecutionAdapter] | None = None, now=None):
        self.adapters: dict[int, ExecutionAdapter] = adapters or {}
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._locks: dict[int, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, pid: int) -> threading.RLock:
        """Serialises every state mutation of one portfolio (signals, reconciliation, triggers)."""
        with self._locks_guard:
            return self._locks.setdefault(pid, threading.RLock())

    # ================================================================== signal processing
    def process(self, signal_id: int) -> list[Outcome]:
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is None or sig.status != SignalStatus.RECEIVED:
                return []
            sig_kind, trader_id, market = sig.kind, sig.trader_id, sig.market
            age = (self._now() - _aw(sig.source_ts)).total_seconds()
            max_age = (get_settings().max_signal_age_forex_s if market.value == "forex"
                       else get_settings().max_signal_age_crypto_s)
            portfolios = [(a.portfolio_id, a.id) for a in s.scalars(
                select(Allocation).join(Portfolio).where(Allocation.trader_id == trader_id,
                                                         Portfolio.market == market))]
        outcomes: list[Outcome] = []
        if sig_kind == SignalKind.OPEN and age > max_age:
            outcomes = [Outcome(pid, "rejected", f"stale signal: {age:.1f}s old > {max_age}s") for pid, _ in portfolios]
        elif age < -5:
            outcomes = [Outcome(pid, "rejected", "signal timestamp is in the future (clock skew)") for pid, _ in portfolios]
        elif not portfolios:
            outcomes = [Outcome(0, "skipped", "no portfolio follows this trader")]
        else:
            for pid, alloc_id in portfolios:
                try:
                    with self.lock(pid):
                        if sig_kind == SignalKind.OPEN:
                            outcomes.append(self._open(signal_id, pid, alloc_id))
                        elif sig_kind in (SignalKind.CLOSE, SignalKind.PARTIAL_CLOSE):
                            outcomes.append(self._close(signal_id, pid))
                        else:
                            outcomes.append(self._modify(signal_id, pid))
                except Exception as e:  # one portfolio failing must not block others
                    log.exception("signal %s portfolio %s failed", signal_id, pid)
                    outcomes.append(Outcome(pid, "failed", f"{type(e).__name__}: {e}"))
        self._finish(signal_id, outcomes)
        return outcomes

    def _finish(self, signal_id: int, outcomes: list[Outcome]):
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            acts = {o.action for o in outcomes}
            done = {"opened", "closed", "modified"}
            if acts and acts <= done:
                sig.status = SignalStatus.EXECUTED
            elif acts & done:
                sig.status = SignalStatus.PARTIALLY_EXECUTED
            elif acts & {"failed", "unknown"}:
                sig.status = SignalStatus.FAILED
            elif acts == {"skipped"}:
                sig.status = SignalStatus.IGNORED
            else:
                sig.status = SignalStatus.REJECTED
            sig.outcome = [o.__dict__ for o in outcomes]
            audit(s, "agent:execution", "signal.processed", f"signal:{signal_id}", status=sig.status.value,
                  outcomes=sig.outcome)
            emit("signal", id=signal_id, status=sig.status.value)
            for o in outcomes:
                if o.action == "failed":
                    notify(s, "execution_failed", f"Copy failed ({sig.symbol})", o.reason or "", severity="warning",
                           signal_id=signal_id, portfolio_id=o.portfolio_id)

    # ------------------------------------------------------------------ OPEN
    def _open(self, signal_id: int, pid: int, alloc_id: int) -> Outcome:
        ad = self.adapters.get(pid)
        if ad is None:
            return Outcome(pid, "failed", "no execution adapter connected for this portfolio")
        with session_scope() as s:
            sig, p, alloc = s.get(Signal, signal_id), s.get(Portfolio, pid), s.get(Allocation, alloc_id)
            tr = s.get(Trader, sig.trader_id)
            cid = client_id(pid, sig.idempotency_key)
            if s.scalar(select(Order.id).where(Order.client_order_id == cid)):
                return Outcome(pid, "skipped", "duplicate: order for this signal already exists")
            if not alloc.enabled:
                return Outcome(pid, "skipped", f"copying disabled for trader ({alloc.stopped_reason or 'by user'})")
            if tr.status in (TraderStatus.REJECTED, TraderStatus.SUSPENDED):
                return Outcome(pid, "rejected", f"trader status is {tr.status.value}")
            if s.scalar(select(Position.id).where(Position.portfolio_id == pid, Position.status == PositionStatus.OPEN,
                                                  Position.source_position_id == sig.source_position_id,
                                                  Position.trader_id == tr.id)):
                return Outcome(pid, "skipped", "duplicate: source position already copied")
            symbol = fx.normalize_symbol(sig.symbol) if p.market.value == "forex" else sig.symbol.upper()
            if not ad.multiple_positions_per_symbol and s.scalar(select(Position.id).where(
                    Position.portfolio_id == pid, Position.status == PositionStatus.OPEN, Position.symbol == symbol)):
                return Outcome(pid, "rejected", f"{symbol} already has an open copied position (venue nets positions)")
            if p.market.value == "forex" and minutes_to_rollover(self._now()) < 15:
                return Outcome(pid, "rejected", "within 15 minutes of the daily forex rollover (no new overnight exposure)")
            try:
                bid, ask = ad.quote(symbol)
            except Exception as e:
                return Outcome(pid, "failed", f"no current price for {symbol}: {e}")
            px = ask if sig.side == "buy" else bid
            if sig.sl is not None and sig.entry_price:
                r_dist = abs(sig.entry_price - sig.sl)
                adverse = (px - sig.entry_price) if sig.side == "buy" else (sig.entry_price - px)
                if r_dist > 0 and adverse > MAX_ENTRY_DRIFT_R * r_dist:
                    return Outcome(pid, "rejected", f"late entry: price moved {adverse / r_dist:.2f}R against the "
                                                    f"source entry (max {MAX_ENTRY_DRIFT_R}R)")
                if (sig.side == "buy" and px <= sig.sl) or (sig.side == "sell" and px >= sig.sl):
                    return Outcome(pid, "rejected", "current price is already beyond the stop-loss")
            state, snap = build_state(s, p, ad)
            limits = limits_for(p)
            if not ad.multiple_positions_per_symbol:
                limits = limits.tightened({"max_positions_per_symbol": 1})
            overlay = st.current_overlay(s)
            currencies = (fx.spec(symbol).base, fx.spec(symbol).quote) if p.market.value == "forex" else ()
            intent = TradeIntent(symbol, sig.side, px, sig.sl, tr.id, alloc.max_risk_pct, alloc.capital_fraction,
                                 vol_ratio=(sig.payload or {}).get("vol_ratio"), currencies=currencies)
            d = pre_trade(intent, state, limits, overlay)
            if not d.approved:
                self._risk_event(s, pid, "trade_vetoed", reasons=d.reasons, signal_id=signal_id)
                return Outcome(pid, "rejected", "; ".join(d.reasons), {"checks": d.checks})
            qty, risk, notional, margin, legs, why = ad.size(symbol, sig.side, px, sig.sl, d.budget)
            if why:
                return Outcome(pid, "rejected", f"sizing: {why}", {"budget": d.budget})
            d2 = post_size(SizedTrade(intent, risk, notional, margin, legs), state, limits, d.budget)
            if not d2.approved:
                self._risk_event(s, pid, "trade_vetoed", reasons=d2.reasons, signal_id=signal_id)
                return Outcome(pid, "rejected", "; ".join(d2.reasons), {"checks": d2.checks})
            order = Order(portfolio_id=pid, signal_id=signal_id, client_order_id=cid, purpose="entry", symbol=symbol,
                          side=sig.side, order_type="MARKET", quantity=qty, stop_price=sig.sl, price=sig.tp,
                          expected_price=px, status=OrderStatus.SUBMITTED, attempts=1,
                          raw={"trader_id": tr.id, "source_position_id": sig.source_position_id, "initial_risk": risk,
                               "sl": sig.sl, "tp": sig.tp, "budget": d.budget, "multipliers": d.multipliers,
                               "source_ts": _aw(sig.source_ts).isoformat(), "simulated": ad.is_simulated})
            s.add(order)
            leverage = max(1.0, notional / max(snap.equity, 1e-9))
        # -------- venue call outside the DB transaction (order row is already committed)
        try:
            res = ad.open_position(OpenRequest(cid, symbol, sig.side, qty, sig.sl, sig.tp, px, leverage))
        except AdapterUnavailable as e:
            self._order_status(cid, OrderStatus.UNKNOWN, str(e))
            return Outcome(pid, "unknown", f"venue unreachable, order will be reconciled: {e}")
        except AdapterError as e:
            self._order_status(cid, OrderStatus.REJECTED, str(e))
            return Outcome(pid, "rejected", f"venue rejected: {e}")
        return self._record_open(cid, res)

    def _record_open(self, cid: str, res: OrderResult) -> Outcome:
        with session_scope() as s:
            o = s.scalar(select(Order).where(Order.client_order_id == cid))
            o.venue_order_id, o.filled_qty, o.avg_price, o.fee, o.fee_asset = (
                res.venue_order_id, res.filled_qty, res.avg_price, res.fee, res.fee_asset)
            o.status = res.status
            o.error = res.error
            o.raw = {**o.raw, "venue": _jsonable(res.raw), "protection": res.protection}
            if not res.ok or res.filled_qty <= 0:
                return Outcome(o.portfolio_id, "rejected" if res.status == OrderStatus.REJECTED else "unknown",
                               res.error or res.status.value)
            ad = self.adapters[o.portfolio_id]
            fill = res.avg_price or o.expected_price
            adverse = (fill - o.expected_price) if o.side == "buy" else (o.expected_price - fill)
            slip_cost = max(0.0, adverse) * _value_per_unit(ad, o.symbol, res.filled_qty)
            o.slippage = adverse
            risk = risk_at_stop(ad, o.symbol, o.side, res.filled_qty, fill, o.raw["sl"])
            delay = (datetime.now(timezone.utc) - datetime.fromisoformat(o.raw["source_ts"])).total_seconds() * 1000
            pos = Position(portfolio_id=o.portfolio_id, trader_id=o.raw["trader_id"],
                           source_position_id=o.raw["source_position_id"], venue_position_ref=res.position_ref,
                           symbol=o.symbol, side=o.side, quantity=res.filled_qty, initial_quantity=res.filled_qty,
                           entry_price=fill, sl=o.raw["sl"], tp=o.raw["tp"], initial_risk=risk, fees=res.fee,
                           slippage_cost=slip_cost, copy_delay_ms=delay, leverage=None,
                           protection=res.protection or {}, is_simulated=ad.is_simulated)
            s.add(pos)
            s.flush()
            o.position_id = pos.id
            tag = " [SIMULATED]" if ad.is_simulated else ""
            notify(s, "position_opened", f"Copied {o.side.upper()} {o.symbol}{tag}",
                   f"{res.filled_qty:g} @ {fill:g}, SL {o.raw['sl']:g}, risk {risk:.2f}", portfolio_id=o.portfolio_id,
                   position_id=pos.id)
            audit(s, "agent:execution", "position.opened", f"position:{pos.id}", client_order_id=cid, qty=res.filled_qty,
                  price=fill, risk=risk, simulated=ad.is_simulated)
            emit("position", id=pos.id, status="open")
            return Outcome(o.portfolio_id, "opened", None, {"position_id": pos.id, "qty": res.filled_qty, "price": fill,
                                                            "risk": risk, "partial": res.status == OrderStatus.PARTIALLY_FILLED})

    # ------------------------------------------------------------------ CLOSE / PARTIAL
    def _close(self, signal_id: int, pid: int) -> Outcome:
        ad = self.adapters.get(pid)
        if ad is None:
            return Outcome(pid, "failed", "no execution adapter connected")
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            positions = list(s.scalars(select(Position).where(
                Position.portfolio_id == pid, Position.status == PositionStatus.OPEN,
                Position.source_position_id == sig.source_position_id, Position.trader_id == sig.trader_id)))
            if not positions:
                return Outcome(pid, "skipped", "no copied position for this source position")
            frac = sig.close_fraction if sig.kind == SignalKind.PARTIAL_CLOSE else 1.0
            reason = "source_partial_close" if frac < 1 else "source_closed"
            jobs = [(pos.id, client_id(pid, sig.idempotency_key, pos.id)) for pos in positions]
        results = [self.close_position(pos_id, cid, fraction=frac, reason=reason) for pos_id, cid in jobs]
        ok = [r for r in results if r.action == "closed"]
        return Outcome(pid, "closed" if ok else results[0].action, None if ok else results[0].reason,
                       {"positions": [r.detail for r in results]})

    def close_position(self, position_id: int, cid: str, fraction: float = 1.0, reason: str = "manual") -> Outcome:
        with session_scope() as s:
            pos = s.get(Position, position_id)
            pid = pos.portfolio_id if pos else 0
        with self.lock(pid):
            return self._close_position(position_id, cid, fraction, reason)

    def _close_position(self, position_id: int, cid: str, fraction: float, reason: str) -> Outcome:
        with session_scope() as s:
            pos = s.get(Position, position_id)
            if pos is None or pos.status != PositionStatus.OPEN:
                return Outcome(0, "skipped", "position not open")
            pid = pos.portfolio_id
            ad = self.adapters.get(pid)
            if ad is None:
                return Outcome(pid, "failed", "no execution adapter connected")
            existing = s.scalar(select(Order).where(Order.client_order_id == cid))
            if existing and existing.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                return Outcome(pid, "skipped", "duplicate close")
            qty = pos.quantity if fraction >= 0.999 else _round_qty(ad, pos.symbol, pos.quantity * fraction)
            if qty <= 0:
                return Outcome(pid, "rejected", "partial close below minimum volume")
            if not existing:
                s.add(Order(portfolio_id=pid, position_id=pos.id, client_order_id=cid,
                            purpose="exit" if fraction >= 0.999 else "partial", symbol=pos.symbol,
                            side="sell" if pos.side == "buy" else "buy", order_type="MARKET", quantity=qty,
                            reduce_only=True, status=OrderStatus.SUBMITTED, attempts=1, raw={"reason": reason}))
            vp = VenuePosition(pos.venue_position_ref or pos.symbol, pos.symbol, pos.side, pos.quantity, pos.entry_price,
                               pos.sl, pos.tp, protection=pos.protection or {})
        try:
            res = ad.close_position(vp, qty, cid)
        except AdapterUnavailable as e:
            self._order_status(cid, OrderStatus.UNKNOWN, str(e))
            return Outcome(pid, "unknown", str(e))
        except AdapterError as e:
            self._order_status(cid, OrderStatus.REJECTED, str(e))
            return Outcome(pid, "failed", f"close rejected: {e}")
        return self._record_close(position_id, cid, res, reason)

    def _record_close(self, position_id: int, cid: str, res: OrderResult, reason: str, exit_price: float | None = None,
                      estimated: bool = False) -> Outcome:
        with session_scope() as s:
            pos = s.get(Position, position_id, with_for_update=True)
            o = s.scalar(select(Order).where(Order.client_order_id == cid))
            if pos.status != PositionStatus.OPEN:
                if o is not None and o.status == OrderStatus.SUBMITTED:
                    o.status, o.error = res.status, "position already closed"
                return Outcome(pos.portfolio_id, "skipped", "position already closed")
            if o is not None:
                o.status, o.filled_qty, o.avg_price, o.fee, o.error = res.status, res.filled_qty, res.avg_price, res.fee, res.error
                o.venue_order_id = res.venue_order_id
            if not res.ok or res.filled_qty <= 0:
                return Outcome(pos.portfolio_id, "failed", res.error or "close not filled")
            ad = self.adapters[pos.portfolio_id]
            px = exit_price or res.avg_price
            qty = min(res.filled_qty, pos.quantity)
            gross = res.raw.get("pnl") if isinstance(res.raw, dict) and "pnl" in res.raw else _pnl(ad, pos, px, qty)
            pos.realized_pnl = (pos.realized_pnl or 0) + gross - (res.fee or 0)
            pos.fees = (pos.fees or 0) + (res.fee or 0)
            pos.quantity = round(pos.quantity - qty, 10)
            if res.protection:
                pos.protection = res.protection
            closed = pos.quantity <= 1e-12
            if closed:
                pos.status, pos.closed_at, pos.exit_price, pos.close_reason = (
                    PositionStatus.CLOSED, datetime.now(timezone.utc), px, reason)
                pos.r_multiple = pos.realized_pnl / pos.initial_risk if pos.initial_risk else None
            tag = " [SIMULATED]" if ad.is_simulated else ""
            est = " (estimated from last price)" if estimated else ""
            notify(s, "position_closed", f"{'Closed' if closed else 'Partially closed'} {pos.symbol}{tag}",
                   f"{qty:g} @ {px:g} ({reason}); P&L {gross - (res.fee or 0):+.2f}{est}", portfolio_id=pos.portfolio_id,
                   position_id=pos.id)
            audit(s, "agent:execution", "position.closed" if closed else "position.reduced", f"position:{pos.id}",
                  qty=qty, price=px, pnl=gross, fee=res.fee, reason=reason, estimated=estimated)
            emit("position", id=pos.id, status=pos.status.value)
            return Outcome(pos.portfolio_id, "closed", None, {"position_id": pos.id, "qty": qty, "price": px,
                                                              "pnl": gross, "fully_closed": closed})

    # ------------------------------------------------------------------ MODIFY
    def _modify(self, signal_id: int, pid: int) -> Outcome:
        ad = self.adapters.get(pid)
        if ad is None:
            return Outcome(pid, "failed", "no execution adapter connected")
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            positions = list(s.scalars(select(Position).where(
                Position.portfolio_id == pid, Position.status == PositionStatus.OPEN,
                Position.source_position_id == sig.source_position_id, Position.trader_id == sig.trader_id)))
            if not positions:
                return Outcome(pid, "skipped", "no copied position for this source position")
            pos = positions[0]
            new_sl, new_tp = sig.sl, sig.tp
            notes = []
            if new_sl is not None:
                wrong = (pos.side == "buy" and new_sl >= _mark(ad, pos)) or (pos.side == "sell" and new_sl <= _mark(ad, pos))
                if wrong:
                    return Outcome(pid, "rejected", "new stop-loss is beyond the current price")
                new_risk = risk_at_stop(ad, pos.symbol, pos.side, pos.quantity, pos.entry_price, new_sl)
                if new_risk > pos.initial_risk * 1.0001:
                    notes.append(f"stop-loss widening ignored (would raise risk {pos.initial_risk:.2f} -> {new_risk:.2f})")
                    new_sl = None
            if new_sl is None and new_tp is None:
                return Outcome(pid, "rejected", "; ".join(notes) or "nothing to modify")
            vp = VenuePosition(pos.venue_position_ref or pos.symbol, pos.symbol, pos.side, pos.quantity, pos.entry_price,
                               pos.sl, pos.tp, protection=pos.protection or {})
            cid = client_id(pid, sig.idempotency_key, pos.id)
            pos_id = pos.id
        try:
            res = ad.modify_protection(vp, new_sl, new_tp, cid)
        except (AdapterError, AdapterUnavailable) as e:
            return Outcome(pid, "failed", f"modify failed: {e}")
        with session_scope() as s:
            pos = s.get(Position, pos_id)
            old = pos.sl
            if new_sl is not None:
                pos.sl = new_sl
            if new_tp is not None:
                pos.tp = new_tp
            if res.protection:
                pos.protection = res.protection
            notify(s, "stop_loss_modified", f"{pos.symbol}: SL {old} -> {pos.sl}, TP {pos.tp}",
                   "; ".join(notes) or "copied from source", portfolio_id=pid, position_id=pos.id)
            audit(s, "agent:execution", "position.modified", f"position:{pos.id}", sl=pos.sl, tp=pos.tp, notes=notes)
        return Outcome(pid, "modified", "; ".join(notes) or None)

    # ================================================================== reconciliation
    def reconcile(self, pid: int) -> dict:
        with self.lock(pid):
            return self._reconcile(pid)

    def _reconcile(self, pid: int) -> dict:
        ad = self.adapters.get(pid)
        if ad is None:
            return {"error": "no adapter"}
        report = {"closed_on_venue": [], "orphans": [], "unknown_resolved": [], "unknown_rejected": []}
        # 1) UNKNOWN / SUBMITTED orders -> query venue by client id
        with session_scope() as s:
            pending = [(o.client_order_id, o.symbol, o.purpose, o.created_at) for o in s.scalars(
                select(Order).where(Order.portfolio_id == pid, Order.status.in_([OrderStatus.UNKNOWN, OrderStatus.SUBMITTED])))]
        for cid, sym, purpose, created in pending:
            if _aw(created) > self._now() - timedelta(seconds=20):
                continue  # still in flight
            try:
                r = ad.get_order(cid, sym)
            except Exception as e:
                log.warning("reconcile lookup failed %s: %s", cid, e)
                continue
            if r and r.ok and r.filled_qty > 0:
                if purpose == "entry":
                    self._record_open(cid, r)
                report["unknown_resolved"].append(cid)
            elif r is None and _aw(created) < self._now() - UNKNOWN_ORDER_TIMEOUT:
                self._order_status(cid, OrderStatus.REJECTED, "not found on venue after timeout")
                report["unknown_rejected"].append(cid)
        # 2) positions closed on the venue (SL/TP hit, liquidation, manual)
        try:
            venue = ad.positions()
        except Exception as e:
            return {**report, "error": f"positions unavailable: {e}"}
        refs = {v.ref for v in venue}
        with session_scope() as s:
            open_rows = list(s.scalars(select(Position).where(Position.portfolio_id == pid,
                                                              Position.status == PositionStatus.OPEN)))
            known = {p.venue_position_ref for p in open_rows}
            jobs = [(p.id, p.symbol, p.side, p.sl, p.tp) for p in open_rows if (p.venue_position_ref or p.symbol) not in refs]
            for v in venue:
                if v.ref not in known and not (v.client_id or "").startswith("tc"):
                    report["orphans"].append({"ref": v.ref, "symbol": v.symbol, "qty": v.quantity})
        for pos_id, sym, side, sl, tp in jobs:
            try:
                bid, ask = ad.quote(sym)
                last = bid if side == "buy" else ask
            except Exception:
                last = sl
            # nearest protective level is the most likely trigger; flagged as an estimate
            px = min([x for x in (sl, tp) if x], key=lambda x: abs(x - last)) if (sl or tp) else last
            res = OrderResult(f"rc-{pos_id}", OrderStatus.FILLED, filled_qty=10 ** 12, avg_price=px)
            with session_scope() as s:
                qty = s.get(Position, pos_id).quantity
            res.filled_qty = qty
            self._record_close(pos_id, f"rc-{pos_id}", res, "closed_on_venue", exit_price=px, estimated=True)
            report["closed_on_venue"].append(pos_id)
        if report["orphans"]:
            with session_scope() as s:
                notify(s, "system", "Untracked positions on venue",
                       f"{len(report['orphans'])} position(s) not opened by TITAN", severity="warning",
                       portfolio_id=pid, orphans=report["orphans"])
        return report

    def reconcile_sources(self, pid: int, source_open_ids: dict[int, set[str]]) -> list[int]:
        """Close follower positions whose source position no longer exists (missed close signal)."""
        closed = []
        with session_scope() as s:
            rows = [(p.id, p.trader_id, p.source_position_id) for p in s.scalars(select(Position).where(
                Position.portfolio_id == pid, Position.status == PositionStatus.OPEN))]
        for pos_id, tid, src in rows:
            if tid in source_open_ids and src not in source_open_ids[tid]:
                o = self.close_position(pos_id, client_id("srcmiss", pos_id), reason="source_position_missing")
                if o.action == "closed":
                    closed.append(pos_id)
        return closed

    def paper_triggers(self, pid: int) -> list[Outcome]:
        with self.lock(pid):
            return self._paper_triggers(pid)

    def _paper_triggers(self, pid: int) -> list[Outcome]:
        ad = self.adapters.get(pid)
        if not ad or not hasattr(ad, "check_triggers"):
            return []
        out = []
        for r in ad.check_triggers():
            with session_scope() as s:
                pos = s.scalar(select(Position).where(Position.portfolio_id == pid, Position.venue_position_ref == r.position_ref,
                                                      Position.status == PositionStatus.OPEN))
                if pos is None:
                    continue
                s.add(Order(portfolio_id=pid, position_id=pos.id, client_order_id=r.client_id[:64], purpose=r.raw["reason"],
                            symbol=pos.symbol, side="sell" if pos.side == "buy" else "buy", order_type="STOP_MARKET",
                            quantity=r.filled_qty, status=OrderStatus.SUBMITTED, raw={"simulated": True}))
                pid_pos = pos.id
            out.append(self._record_close(pid_pos, r.client_id[:64], r, r.raw["reason"]))
        return out

    # ================================================================== helpers
    def _order_status(self, cid: str, status: OrderStatus, error: str | None):
        with session_scope() as s:
            o = s.scalar(select(Order).where(Order.client_order_id == cid))
            if o:
                o.status, o.error = status, error

    def _risk_event(self, s: Session, pid: int, kind: str, **detail):
        s.add(RiskEvent(portfolio_id=pid, kind=kind, severity="info", detail=detail))


def _aw(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _value_per_unit(ad: ExecutionAdapter, symbol: str, qty: float) -> float:
    if ad.market == "forex":
        return fx.value_per_price_unit(fx.spec(symbol), qty, ad.account_currency, ad.mids())
    if getattr(ad, "product", None) == cm.COINM:
        return 0.0
    return qty


def _pnl(ad: ExecutionAdapter, pos: Position, px: float, qty: float) -> float:
    if ad.market == "forex":
        return fx.pnl(fx.spec(pos.symbol), pos.side, qty, pos.entry_price, px, ad.account_currency, ad.mids())
    product = getattr(ad, "product", cm.USDM)
    f = getattr(ad, "filters", {}).get(pos.symbol)
    return cm.pnl_usd(product, pos.side, qty, pos.entry_price, px, float(f.contract_size) if f else 1.0)


def _mark(ad: ExecutionAdapter, pos: Position) -> float:
    bid, ask = ad.quote(pos.symbol)
    return bid if pos.side == "buy" else ask


def _round_qty(ad: ExecutionAdapter, symbol: str, qty: float) -> float:
    if ad.market == "forex":
        return fx.floor_step(qty, fx.spec(symbol).volume_step)
    f = getattr(ad, "filters", {}).get(symbol)
    return float(cm.q_down(qty, f.market_step_size)) if f else qty


def _jsonable(x):
    import json

    try:
        return json.loads(json.dumps(x, default=str))
    except Exception:
        return str(x)
