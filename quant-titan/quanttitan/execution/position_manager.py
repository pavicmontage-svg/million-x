"""Live/paper position lifecycle on top of the order manager.

open():  margin type + leverage → entry (MARKET or marketable LIMIT IOC, verified fill) →
         protective STOP_MARKET (reduce-only) on the exchange → TAKE_PROFIT_MARKET partials.
         If the protective stop cannot be placed, the position is flattened immediately.
Stops are always replaced *new-before-old* so the position is never unprotected.
State is derived from verified exchange order status, never from the fact that a request was sent.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AppConfig
from ..core.logging import get_logger
from ..core.types import ContractSpec, Decision, Direction, Position, TradeRecord, new_id, now_ms
from .exchange import Exchange, ExchangeError, OrderRequest
from .exits import direction_side, round_down, stop_reason, trail_level
from .order_manager import OrderManager

log = get_logger("positions")


@dataclass
class LegFill:
    qty: float
    price: float
    fee: float
    reason: str
    ref: float
    ts: int = field(default_factory=now_ms)


@dataclass
class Managed:
    pos: Position
    entry_id: str
    stop_id: str
    tp_ids: list[str] = field(default_factory=list)
    entry_fill: LegFill | None = None
    exits: list[LegFill] = field(default_factory=list)
    recorded_tps: set[str] = field(default_factory=set)
    stop_seq: int = 0
    decision_entry: float = 0.0
    expected_r: float = 0.0
    confidence: float = 0.0


class PositionManager:
    def __init__(self, cfg: AppConfig, exchange: Exchange, orders: OrderManager, specs: dict[str, ContractSpec],
                 *, on_closed=None, on_alert=None, mode: str = "paper") -> None:
        self.cfg = cfg
        self.ex = exchange
        self.om = orders
        self.specs = specs
        self.on_closed = on_closed
        self.on_alert = on_alert
        self.mode = mode
        self.managed: dict[str, Managed] = {}

    def _alert(self, kind: str, severity: str, msg: str) -> None:
        log.warning("%s: %s", kind, msg)
        if self.on_alert:
            self.on_alert(kind, severity, msg)

    def spec(self, symbol: str) -> ContractSpec:
        return self.specs.get(symbol) or ContractSpec(symbol)

    @property
    def positions(self) -> list[Position]:
        return [m.pos for m in self.managed.values()]

    @staticmethod
    def _cid(decision_id: str, leg: str) -> str:
        return f"qt{decision_id.replace('dec_', '')[:14]}{leg}"[:36]

    # ------------------------------------------------------------------ open
    async def open(self, d: Decision, *, ref_price: float, order_type: str = "MARKET", leverage: float = 1.0,
                   group_id: str = "") -> Position | None:
        spec = self.spec(d.symbol)
        side = direction_side(d.direction)
        ex_cfg = self.cfg.execution
        try:
            await self.ex.set_margin_type(d.symbol, ex_cfg.margin_type)
            await self.ex.set_leverage(d.symbol, int(max(1, round(leverage))))
        except ExchangeError as e:
            self._alert("exchange_failure", "warning", f"{d.symbol}: margin/leverage setup failed ({e}); trade skipped")
            return None
        entry_id = self._cid(d.id, "E")
        if order_type == "LIMIT_IOC":
            off = ex_cfg.limit_offset_bps / 1e4 * (1 if side == "BUY" else -1)
            req = OrderRequest(d.symbol, side, "LIMIT", d.qty, price=spec.round_price(ref_price * (1 + off)), tif="IOC", client_id=entry_id)
        else:
            req = OrderRequest(d.symbol, side, "MARKET", d.qty, client_id=entry_id)
        info = await self.om.submit(req, approval=d.approval, ref_price=ref_price)
        filled = spec.round_qty(info.filled_qty)
        if info.filled_qty <= 0:
            log.info("entry %s not filled (status %s)", entry_id, info.status)
            return None
        if filled <= 0:  # filled less than one step — flatten the dust
            await self._flatten(d.symbol, d.direction, info.filled_qty, f"{entry_id}X")
            return None
        stop_px = spec.round_price(d.stop)
        pos = Position(new_id("pos_"), d.symbol, d.direction, d.engine, d.setup, info.avg_price, filled, filled, stop_px,
                       stop_px, list(d.targets), now_ms(), decision_id=d.id,
                       trail_atr=d.signal.trail_atr if d.signal else 0.0, atr=d.signal.atr if d.signal else 0.0,
                       time_stop_bars=d.signal.time_stop_bars if d.signal else 0, best_price=info.avg_price,
                       regime=d.regime, leverage=leverage, group_id=group_id)
        fee = info.fee or filled * info.avg_price * self.cfg.costs.taker_fee
        m = Managed(pos, entry_id, "", entry_fill=LegFill(filled, info.avg_price, fee, "entry", ref_price),
                    decision_entry=d.entry, expected_r=d.expected_r, confidence=d.confidence)
        # protective stop — mandatory
        if not await self._place_stop(m, stop_px):
            self._alert("risk_violation", "critical", f"{d.symbol}: protective stop could not be placed — flattening")
            await self._flatten_managed(m, "stop_placement_failed")
            return None
        # take-profits (runner left to the trailing stop when configured)
        tps = pos.targets if pos.trail_atr <= 0 else pos.targets[:-1]
        remaining = filled
        for k, t in enumerate(tps):
            last = k == len(pos.targets) - 1
            q = remaining if last else spec.round_qty(min(remaining, filled * t.fraction))
            if q < spec.min_qty:
                continue
            cid = self._cid(d.id, f"T{k + 1}")
            try:
                await self.om.submit(OrderRequest(d.symbol, direction_side(d.direction, True), "TAKE_PROFIT_MARKET", q,
                                                  stop_price=spec.round_price(t.price), reduce_only=True, client_id=cid,
                                                  working_type=ex_cfg.working_type), wait_fill=False)
                m.tp_ids.append(cid)
                remaining -= q
            except ExchangeError as e:
                self._alert("exchange_failure", "warning", f"{d.symbol}: TP{k + 1} not placed ({e}); stop still protects")
        self.managed[pos.id] = m
        return pos

    async def _place_stop(self, m: Managed, price: float) -> bool:
        p = m.pos
        m.stop_seq += 1
        cid = self._cid(p.decision_id, f"S{m.stop_seq}")
        req = OrderRequest(p.symbol, direction_side(p.direction, True), "STOP_MARKET", p.initial_qty, stop_price=price,
                           reduce_only=True, client_id=cid, working_type=self.cfg.execution.working_type)
        for _ in range(2):
            try:
                info = await self.om.submit(req, wait_fill=False)
                if info.status in ("NEW", "FILLED", "PARTIALLY_FILLED"):
                    old = m.stop_id
                    m.stop_id = cid
                    p.stop = price
                    if old:
                        await self.om.cancel(p.symbol, old, conditional=True)
                    return True
            except ExchangeError as e:
                log.warning("stop placement %s failed: %s", cid, e)
        return False

    # ------------------------------------------------------------------ sync with exchange state
    async def sync(self) -> list[TradeRecord]:
        closed: list[TradeRecord] = []
        for pid, m in list(self.managed.items()):
            p = m.pos
            for k, cid in enumerate(m.tp_ids):
                if cid in m.recorded_tps:
                    continue
                o = await self.ex.get_order(p.symbol, cid, conditional=True)
                if o is not None and o.filled_qty > 0 and o.is_terminal:
                    m.recorded_tps.add(cid)
                    q = min(o.filled_qty, p.qty)
                    m.exits.append(LegFill(q, o.avg_price, o.fee or q * o.avg_price * self.cfg.costs.taker_fee,
                                           f"take_profit_{k + 1}", o.stop_price))
                    p.qty = self.spec(p.symbol).round_qty(p.qty - q)
                    p.tp_hits = max(p.tp_hits, k + 1)
                    if self.cfg.execution.move_stop_to_be_after_tp1 and p.tp_hits == 1 and p.qty > 0 \
                            and (p.entry_price - p.stop) * p.direction.sign > 0:
                        await self._place_stop(m, self.spec(p.symbol).round_price(p.entry_price))
            if m.stop_id and p.qty > 0:
                o = await self.ex.get_order(p.symbol, m.stop_id, conditional=True)
                if o is not None and o.filled_qty > 0 and o.is_terminal:
                    q = min(o.filled_qty, p.qty)
                    m.exits.append(LegFill(q, o.avg_price, o.fee or q * o.avg_price * self.cfg.costs.taker_fee,
                                           stop_reason(p), o.stop_price))
                    p.qty = self.spec(p.symbol).round_qty(p.qty - q)
            if p.qty <= 0:
                closed.append(await self._finalize(m))
        return closed

    async def on_bar(self, symbol: str, high: float, low: float, close: float, atr: float) -> list[TradeRecord]:
        out = []
        for m in [m for m in self.managed.values() if m.pos.symbol == symbol]:
            p = m.pos
            p.bars_held += 1
            p.best_price = max(p.best_price, high) if p.direction is Direction.LONG else min(p.best_price, low)
            p.atr = atr or p.atr
            if p.time_stop_bars and p.bars_held >= p.time_stop_bars:
                tr = await self.close(p.id, "time_stop")
                if tr:
                    out.append(tr)
                continue
            lvl = trail_level(p, p.atr)
            if lvl is not None:
                new = self.spec(symbol).round_price(lvl)
                if (new - p.stop) * p.direction.sign >= self.spec(symbol).tick_size:
                    await self._place_stop(m, new)
        return out

    # ------------------------------------------------------------------ close
    async def close(self, pid: str, reason: str) -> TradeRecord | None:
        m = self.managed.get(pid)
        if m is None:
            return None
        await self._flatten_managed(m, reason)
        if m.pos.qty <= 0:
            return await self._finalize(m)
        self._alert("reconciliation_failure", "critical", f"{m.pos.symbol}: close for {reason} not confirmed")
        return None

    async def close_all(self, reason: str) -> list[TradeRecord]:
        out = []
        for pid in list(self.managed):
            tr = await self.close(pid, reason)
            if tr:
                out.append(tr)
        return out

    async def _flatten_managed(self, m: Managed, reason: str) -> None:
        p = m.pos
        for cid in [*m.tp_ids, m.stop_id]:
            if cid:
                try:
                    await self.om.cancel(p.symbol, cid, conditional=True)
                except ExchangeError:
                    pass
        # the exchange position is the source of truth for how much to close
        ex_qty = 0.0
        for xp in await self.ex.positions():
            if xp.symbol == p.symbol:
                ex_qty = abs(xp.qty)
        if ex_qty <= 0:
            p.qty = 0.0
            if not m.exits:
                m.exits.append(LegFill(p.initial_qty, p.entry_price, 0.0, f"{reason}_already_flat", p.entry_price))
            return
        info = await self._flatten(p.symbol, p.direction, ex_qty, self._cid(p.decision_id, f"X{len(m.exits)}"))
        if info is not None and info.filled_qty > 0:
            m.exits.append(LegFill(info.filled_qty, info.avg_price, info.fee or info.filled_qty * info.avg_price * self.cfg.costs.taker_fee,
                                   reason, info.avg_price))
            p.qty = self.spec(p.symbol).round_qty(max(0.0, p.qty - info.filled_qty)) if info.filled_qty < ex_qty else 0.0

    async def _flatten(self, symbol: str, direction: Direction, qty: float, cid: str):
        try:
            return await self.om.submit(OrderRequest(symbol, direction_side(direction, True), "MARKET", qty,
                                                     reduce_only=True, client_id=cid))
        except ExchangeError as e:
            self._alert("exchange_failure", "critical", f"{symbol}: flatten failed: {e}")
            return None

    async def _finalize(self, m: Managed) -> TradeRecord:
        p = m.pos
        self.managed.pop(p.id, None)
        for cid in [*m.tp_ids, m.stop_id]:
            if cid:
                o = self.om.orders.get(cid)
                if o is None or not o.is_terminal:
                    try:
                        await self.om.cancel(p.symbol, cid, conditional=True)
                    except ExchangeError:
                        pass
        e = m.entry_fill
        s = p.direction.sign
        exit_qty = sum(x.qty for x in m.exits) or 1e-12
        exit_px = sum(x.qty * x.price for x in m.exits) / exit_qty
        gross = sum((x.price - e.price) * x.qty * s for x in m.exits)
        fees = e.fee + sum(x.fee for x in m.exits)
        funding = 0.0
        if hasattr(self.ex, "funding_paid"):
            funding = 0.0  # paper funding is applied to the wallet; attributed in the runtime's daily accounting
        slip = abs(e.price - e.ref) * e.qty + sum(abs(x.price - x.ref) * x.qty for x in m.exits if x.ref)
        net = gross - fees - funding
        init_risk = abs(e.price - p.initial_stop) * p.initial_qty
        reason = m.exits[-1].reason if m.exits else "unknown"
        tr = TradeRecord(new_id("trd_"), p.symbol, p.direction.value, p.engine, p.setup, p.regime, e.price, exit_px,
                         p.initial_qty, p.opened_ts, now_ms(), gross, fees, funding, slip, net,
                         net / init_risk if init_risk > 0 else 0.0, init_risk, reason, p.decision_id, self.mode,
                         p.group_id, m.confidence, m.expected_r,
                         {"exits": [x.__dict__ for x in m.exits], "tp_hits": p.tp_hits, "bars_held": p.bars_held,
                          "stop": p.initial_stop, "final_stop": p.stop, "targets": [t.price for t in p.targets]})
        p.status = "closed"
        if self.on_closed:
            self.on_closed(tr)
        return tr
