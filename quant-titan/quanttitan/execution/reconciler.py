"""Reconciliation of internal state against the exchange.

Checks, on start-up and every ``reconcile_interval_s``:
  * every internal position exists on the exchange with the same signed quantity;
  * every internal position has a live protective stop (re-placed automatically if missing);
  * the exchange has no positions the platform does not know about ("unexpected position");
  * no orphan platform orders remain (cancelled).
If anything cannot be resolved automatically: new orders are blocked, a SAFE_STATE circuit breaker
is tripped and the user is alerted. Unexpected positions are handled per configuration:
``alert_only`` | ``protect`` (place a reduce-only stop at max_stop_distance_pct) | ``flatten``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AppConfig
from ..core.logging import get_logger
from ..core.types import now_ms
from ..risk.breakers import CircuitBreakers
from .exchange import ExchangeError, OrderRequest
from .order_manager import OrderManager
from .position_manager import PositionManager

log = get_logger("reconciler")


@dataclass
class ReconcileReport:
    ok: bool
    ts: int
    issues: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Reconciler:
    def __init__(self, cfg: AppConfig, pm: PositionManager, om: OrderManager, breakers: CircuitBreakers, *, on_alert=None) -> None:
        self.cfg = cfg
        self.pm = pm
        self.om = om
        self.breakers = breakers
        self.on_alert = on_alert
        self.last: ReconcileReport | None = None
        self.failures = 0

    def _alert(self, kind: str, sev: str, msg: str) -> None:
        if self.on_alert:
            self.on_alert(kind, sev, msg)

    async def run(self) -> ReconcileReport:
        rep = ReconcileReport(True, now_ms())
        try:
            # positions that closed on the exchange (stop/TP filled) are picked up first
            await self.pm.sync()
            ex_pos = {p.symbol: p for p in await self.pm.ex.positions()}
            open_orders = await self.pm.ex.open_orders()
        except ExchangeError as e:
            self.failures += 1
            rep.ok = False
            rep.issues.append(f"exchange unreachable during reconciliation: {e}")
            if self.failures >= 3:
                self._block(rep, "exchange unreachable for 3 reconciliation cycles")
            self.last = rep
            return rep
        self.failures = 0
        internal: dict[str, float] = {}
        for m in self.pm.managed.values():
            internal[m.pos.symbol] = internal.get(m.pos.symbol, 0.0) + m.pos.qty * m.pos.direction.sign
        live_ids = {o.client_id for o in open_orders}
        # internal vs exchange
        for sym, q in internal.items():
            step = self.pm.spec(sym).step_size
            xq = ex_pos[sym].qty if sym in ex_pos else 0.0
            if abs(xq - q) > step / 2:
                rep.ok = False
                rep.issues.append(f"{sym}: internal qty {q:+g} vs exchange {xq:+g}")
        for m in list(self.pm.managed.values()):
            if m.pos.symbol in ex_pos and m.stop_id not in live_ids:
                rep.actions.append(f"{m.pos.symbol}: protective stop missing — re-placing")
                if not await self.pm._place_stop(m, m.pos.stop):
                    rep.ok = False
                    rep.issues.append(f"{m.pos.symbol}: protective stop missing and could not be re-placed")
        # unexpected exchange positions
        for sym, xp in ex_pos.items():
            if sym in internal:
                continue
            rep.ok = False
            msg = f"unexpected position on exchange: {sym} {xp.qty:+g} @ {xp.entry_price}"
            rep.issues.append(msg)
            self._alert("unexpected_position", "critical", msg)
            await self._handle_unexpected(sym, xp, rep)
        # orphan orders created by this platform
        known = set()
        for m in self.pm.managed.values():
            known |= {m.stop_id, *m.tp_ids}
        for o in open_orders:
            if o.client_id.startswith("qt") and o.client_id not in known:
                try:
                    await self.om.cancel(o.symbol, o.client_id, conditional=o.type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"))
                    rep.actions.append(f"cancelled orphan order {o.client_id}")
                except ExchangeError as e:
                    rep.issues.append(f"orphan order {o.client_id} could not be cancelled: {e}")
        if rep.ok:
            if "SAFE_STATE" in self.breakers.active and self.breakers.active["SAFE_STATE"].reason.startswith("reconciliation"):
                self.breakers.clear_safe_state("reconciliation clean")
                self.om.unblock()
        else:
            self._block(rep, "; ".join(rep.issues))
        self.last = rep
        return rep

    def _block(self, rep: ReconcileReport, why: str) -> None:
        self.om.block(f"reconciliation failed: {why}")
        self.breakers.safe_state(f"reconciliation failed: {why}")
        self._alert("reconciliation_failure", "critical", why)
        rep.actions.append("new orders blocked; SAFE_STATE active")

    async def _handle_unexpected(self, sym: str, xp, rep: ReconcileReport) -> None:
        policy = self.cfg.execution.on_unexpected_position
        side = "SELL" if xp.qty > 0 else "BUY"
        if policy == "flatten":
            await self.om.submit(OrderRequest(sym, side, "MARKET", abs(xp.qty), reduce_only=True, client_id=f"qtunx{now_ms() % 10**10}F"))
            rep.actions.append(f"{sym}: unexpected position flattened")
        elif policy == "protect":
            dist = self.cfg.risk.max_stop_distance_pct / 100
            px = xp.entry_price * (1 - dist) if xp.qty > 0 else xp.entry_price * (1 + dist)
            spec = self.pm.spec(sym)
            try:
                await self.om.submit(OrderRequest(sym, side, "STOP_MARKET", abs(xp.qty), stop_price=spec.round_price(px),
                                                  reduce_only=True, client_id=f"qtunx{now_ms() % 10**10}S"), wait_fill=False)
                rep.actions.append(f"{sym}: protective stop placed on unexpected position at {px:.6g}")
            except ExchangeError as e:
                rep.issues.append(f"{sym}: could not protect unexpected position: {e}")
