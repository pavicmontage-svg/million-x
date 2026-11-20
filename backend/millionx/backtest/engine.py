"""Event-driven portfolio backtester.

Timeline per 1m bar (per symbol):
  1. pending market entries (decided at the previous close) fill at this OPEN
     with taker fee + slippage,
  2. exits are checked on this bar's high/low (stop first, conservative),
  3. funding is charged when an 8h boundary is crossed,
  4. at the CLOSE, bots emit signals; every signal opens a *virtual* position
     (competition), the supervisor + risk manager decide real entries for the
     next bar's open.

The same Strategy, Supervisor, RiskManager and exit logic run live.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..competition import CompetitionEngine
from ..config import Settings
from ..engine.position import ExitPolicy, update_on_bar
from ..models import Bars, BotStatus, Position, Signal, Side, Trade, MINUTE_MS
from ..regime import regime_key
from ..risk import RiskManager, SymbolRules
from ..strategies import MarketData, Strategy, build_bots
from ..supervisor import Supervisor
from . import metrics

FUNDING_MS = 8 * 60 * MINUTE_MS


@dataclass
class BacktestConfig:
    start_equity: float = 40.0
    bots: Optional[list[str]] = None          # subset of bot ids (None = all)
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    use_supervisor: bool = True               # False = each bot trades alone (research mode)
    virtual_only: bool = False
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    rules: dict[str, SymbolRules] = field(default_factory=dict)
    record_equity_every: int = 15             # minutes
    daily_competition: bool = True


@dataclass
class BacktestResult:
    trades: list[Trade]
    virtual_trades: list[Trade]
    equity_curve: list[tuple[int, float]]
    metrics: dict[str, Any]
    per_bot: dict[str, Any]
    per_regime: list[dict[str, Any]]
    rejections: dict[str, int]
    competition_history: list[dict[str, Any]]
    config: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return metrics.json_safe({"metrics": self.metrics, "per_bot": self.per_bot,
                                  "rejections": self.rejections, "per_regime": self.per_regime,
                                  "config": self.config})


class _Sim:
    def __init__(self, settings: Settings, cfg: BacktestConfig):
        self.s, self.cfg = settings, cfg
        self.fees = settings.fees
        self.slip = settings.fees.slippage_bps / 1e4

    def fill_entry(self, side: Side, ref: float) -> float:
        return ref * (1 + self.slip * int(side))

    def fill_exit(self, side: Side, ref: float, reason: str) -> float:
        # market-type exits (stops, time stop) pay slippage against us; TP orders
        # are TAKE_PROFIT_MARKET in live too, so they get the same treatment.
        return ref * (1 - self.slip * int(side))


def run(data: dict[str, Bars], settings: Settings, cfg: BacktestConfig) -> BacktestResult:
    sim = _Sim(settings, cfg)
    # one competition / regime book for the whole portfolio
    probe = build_bots()
    comp_ids = [b.bot_id for b in probe if cfg.bots is None or b.bot_id in cfg.bots]
    comp = CompetitionEngine(comp_ids)
    sup = Supervisor(comp, tradable_statuses=(BotStatus.PAPER, BotStatus.BACKTEST_PASSED, BotStatus.LIVE_ELIGIBLE))
    for b in comp_ids:
        sup.set_status(b, BotStatus.PAPER, "backtest")
    risk = RiskManager(settings.risk, settings.fees)
    from ..strategies.base import set_min_stop_from_fees
    set_min_stop_from_fees(settings.fees, settings.target_fee_share)

    # ---- per symbol preparation (strategies are causal, verified in tests)
    mds: dict[str, MarketData] = {}
    bots: dict[str, list[Strategy]] = {}
    sigs: dict[str, dict[str, dict[int, Signal]]] = {}
    for sym, b in data.items():
        md = MarketData(sym, b)
        mds[sym] = md
        allb = build_bots(comp.regimes, cfg.params)
        need_all = "bot10_adaptive" in comp_ids
        members = {m.bot_id: m for m in allb if m.bot_id != "bot10_adaptive"
                   and (need_all or m.bot_id in comp_ids)}
        for m in members.values():
            m.prepare(md)
        chosen = [members[i] for i in comp_ids if i in members]
        if "bot10_adaptive" in comp_ids:
            ad = next(x for x in allb if x.bot_id == "bot10_adaptive")
            ad.prepare(md)
            chosen.append(ad)
        bots[sym] = chosen
        # precompute deterministic signals of base bots
        sigs[sym] = {}
        for m in chosen:
            if m.bot_id == "bot10_adaptive":
                continue
            d: dict[int, Signal] = {}
            for i in m.close_idx:
                s = m.signal_at(int(i))
                if s is not None:
                    d[int(i)] = s
            sigs[sym][m.bot_id] = d
        # adaptive member lookups use the precomputed tables
        for m in members.values():
            table = sigs[sym].get(m.bot_id)
            if table is None:
                table = {}
                for i in m.close_idx:
                    s = m.signal_at(int(i))
                    if s is not None:
                        table[int(i)] = s
            m.signal_at = (lambda t: (lambda i: t.get(i)))(table)  # type: ignore[assignment]
        cfg.rules.setdefault(sym, SymbolRules(sym, tick_size=float(b.close[0]) * 1e-5,
                                              step_size=10 ** np.floor(np.log10(max(1e-8, 5 / b.close[0] / 100))),
                                              min_qty=0.0, min_notional=5.0))

    # ---- global clock
    times = np.unique(np.concatenate([b.open_time for b in data.values()]))
    pos_idx = {sym: {int(t): k for k, t in enumerate(b.open_time)} for sym, b in data.items()}
    equity = cfg.start_equity
    risk.reset(equity, int(times[0]))
    real: dict[str, Position] = {}
    virtual: dict[tuple[str, str], Position] = {}
    pending_real: list[tuple[Signal, Any]] = []
    pending_virt: list[Signal] = []
    trades: list[Trade] = []
    vtrades: list[Trade] = []
    rejections: dict[str, int] = {}
    curve: list[tuple[int, float]] = []
    last_day = int(times[0]) // 86_400_000
    taker = settings.fees.taker
    last_px: dict[str, float] = {}

    def close_pos(p: Position, px: float, ts: int, reason: str, store: list[Trade]):
        nonlocal equity
        fee = abs(p.qty) * px * taker
        gross = p.unrealized(px)
        p.fees_paid += fee
        total_gross = gross + p.realized_pnl
        net = total_gross - p.fees_paid - p.funding_paid
        t = Trade(id=p.id, bot_id=p.bot_id, symbol=p.symbol, side="LONG" if p.side == Side.LONG else "SHORT",
                  qty=p.qty_initial, entry_price=p.entry_price, exit_price=px, opened_at=p.opened_at, closed_at=ts,
                  gross_pnl=total_gross, fees=p.fees_paid, funding=p.funding_paid, net_pnl=net,
                  r_multiple=net / p.risk_amount if p.risk_amount > 0 else 0.0, risk_amount=p.risk_amount,
                  exit_reason=reason, regime=p.regime, virtual=p.virtual, mode="backtest")
        store.append(t)
        if not p.virtual:
            equity += gross - fee
            risk.on_trade_closed(net, ts)
        else:
            comp.on_virtual_trade(t)
        sup.on_trade(t)
        return t

    def open_pos(sig: Signal, px_open: float, ts: int, qty: float, lev: int, risk_amt: float, virtual_: bool) -> Position:
        nonlocal equity
        fill = sim.fill_entry(sig.side, px_open)
        # keep the strategy's stop/targets in price terms
        p = Position(id=uuid.uuid4().hex[:12], bot_id=sig.bot_id, symbol=sig.symbol, side=sig.side, qty=qty,
                     entry_price=fill, stop=sig.stop, tp1=sig.tp1, tp2=sig.tp2, initial_stop=sig.stop,
                     opened_at=ts, leverage=lev, risk_amount=risk_amt, qty_initial=qty, best_price=fill,
                     atr_at_entry=sig.features.get("atr", 0.0), signal_id=sig.id, virtual=virtual_)
        tr, vo = mds[sig.symbol].regime_at_1m(pos_idx[sig.symbol][ts] - 1) if pos_idx[sig.symbol][ts] > 0 else ("SIDEWAYS", "NORMAL_VOL")
        p.regime = regime_key(tr, vo)
        fee = qty * fill * taker
        p.fees_paid += fee
        if not virtual_:
            equity -= fee
        return p

    for t in times:
        t = int(t)
        day = t // 86_400_000
        if day != last_day:
            last_day = day
            if cfg.daily_competition:
                comp.daily_cycle(t)
        # 1) fills at open
        new_pending_real, new_pending_virt = [], []
        for sig, dec in pending_real:
            k = pos_idx[sig.symbol].get(t)
            if k is None:
                new_pending_real.append((sig, dec))
                continue
            b = data[sig.symbol]
            # invalidate if the open already gapped beyond the stop
            if (sig.side == Side.LONG and b.open[k] <= sig.stop) or (sig.side == Side.SHORT and b.open[k] >= sig.stop):
                rejections["gap_beyond_stop"] = rejections.get("gap_beyond_stop", 0) + 1
                continue
            real[sig.symbol] = open_pos(sig, float(b.open[k]), t, dec.qty, dec.leverage, dec.risk_amount, False)
        for sig in pending_virt:
            k = pos_idx[sig.symbol].get(t)
            if k is None:
                new_pending_virt.append(sig)
                continue
            b = data[sig.symbol]
            if (sig.side == Side.LONG and b.open[k] <= sig.stop) or (sig.side == Side.SHORT and b.open[k] >= sig.stop):
                continue
            fill = sim.fill_entry(sig.side, float(b.open[k]))
            # virtual books size at a fixed 0.5 % of a 100-unit account so R is comparable
            d = abs(fill - sig.stop) + fill * 2 * (taker + sim.slip)
            qty = 0.5 / d if d > 0 else 0.0
            virtual[(sig.bot_id, sig.symbol)] = open_pos(sig, float(b.open[k]), t, qty, 1, 0.5, True)
        pending_real, pending_virt = new_pending_real, new_pending_virt

        # 2) exits + 3) funding
        for book, store in ((real, trades), (virtual, vtrades)):
            for key in list(book):
                p = book[key]
                k = pos_idx[p.symbol].get(t)
                if k is None:
                    continue
                b = data[p.symbol]
                if t % FUNDING_MS == 0 and t > p.opened_at:
                    f = p.qty * float(b.open[k]) * settings.fees.funding_rate_8h * int(p.side)
                    p.funding_paid += f
                    if not p.virtual:
                        equity -= f
                atr = p.atr_at_entry  # trailing distance fixed at the signal-timeframe ATR
                for ev in update_on_bar(p, float(b.open[k]), float(b.high[k]), float(b.low[k]), float(b.close[k]),
                                        atr, t, cfg.exit_policy, taker):
                    if ev.kind == "PARTIAL":
                        q = ev.qty
                        px = sim.fill_exit(p.side, ev.price, ev.reason)
                        g = (px - p.entry_price) * q * int(p.side)
                        fee = q * px * taker
                        p.realized_pnl += g
                        p.fees_paid += fee
                        p.qty -= q
                        if not p.virtual:
                            equity += g - fee
                    elif ev.kind == "CLOSE":
                        px = sim.fill_exit(p.side, ev.price, ev.reason)
                        close_pos(p, px, t, ev.reason, store)
                        del book[key]
                        break

        # 4) signals at close
        all_sigs: list[Signal] = []
        for sym, bl in bots.items():
            k = pos_idx[sym].get(t)
            if k is None:
                continue
            last_px[sym] = float(data[sym].close[k])
            for m in bl:
                s = m.signal_at(k) if m.bot_id == "bot10_adaptive" else sigs[sym][m.bot_id].get(k)
                if s is not None:
                    all_sigs.append(s)
        for s in all_sigs:
            if (s.bot_id, s.symbol) not in virtual and not any(v.bot_id == s.bot_id and v.symbol == s.symbol for v in pending_virt):
                pending_virt.append(s)

        # mark-to-market equity for risk limits
        unreal = sum(p.unrealized(last_px.get(p.symbol, p.entry_price)) for p in real.values())
        risk.update_equity(equity + unreal, t)
        if all_sigs and not cfg.virtual_only:
            open_syms = {p.symbol: int(p.side) for p in real.values()}
            open_syms.update({s.symbol: int(s.side) for s, _ in pending_real})

            def regime_of(sym, _t=t):
                return mds[sym].regime_at_1m(pos_idx[sym][_t])

            if cfg.use_supervisor:
                verdicts = sup.review(all_sigs, regime_of, open_syms, enforce_status=True)
                cands = [(v.signal, v.size_multiplier) for v in verdicts if v.accepted]
                for v in verdicts:
                    if not v.accepted:
                        key = v.reasons[0].split(" ")[0]
                        rejections[key] = rejections.get(key, 0) + 1
            else:
                cands = [(s, 1.0) for s in all_sigs if s.symbol not in open_syms]
            for s, mult in cands:
                positions = list(real.values())
                ap = mds[s.symbol].atr_pct15_at_1m(pos_idx[s.symbol][t])
                dec = risk.size(s, cfg.rules[s.symbol], positions + [_pseudo(x, d) for x, d in pending_real],
                                ap, t, mult)
                if dec.approved:
                    pending_real.append((s, dec))
                else:
                    key = (dec.reasons[0] if dec.reasons else "rejected").split(" ")[0]
                    rejections[key] = rejections.get(key, 0) + 1
        if t % (cfg.record_equity_every * MINUTE_MS) == 0:
            curve.append((t, equity + unreal))

    span = (times[-1] - times[0]) / MINUTE_MS if len(times) > 1 else None
    m = metrics.compute(trades, cfg.start_equity, span)
    m["max_drawdown_mtm"] = metrics.max_drawdown([c[1] for c in curve]) if curve else 0.0
    per_bot = {}
    for b in comp_ids:
        per_bot[b] = {"real": metrics.compute([x for x in trades if x.bot_id == b], cfg.start_equity, span),
                      "virtual": metrics.compute([x for x in vtrades if x.bot_id == b], 100.0, span),
                      "status": sup.status.get(b).value, "status_reason": sup.status_reason.get(b, "")}
    return BacktestResult(trades, vtrades, curve, m, per_bot, comp.regimes.table(), rejections, comp.history,
                          {"start_equity": cfg.start_equity, "symbols": list(data), "bots": comp_ids,
                           "use_supervisor": cfg.use_supervisor, "fees": settings.fees.__dict__,
                           "risk": settings.risk.__dict__, "from": int(times[0]), "to": int(times[-1])})


def _pseudo(sig: Signal, dec) -> Position:
    return Position(id="pending", bot_id=sig.bot_id, symbol=sig.symbol, side=sig.side, qty=dec.qty,
                    entry_price=sig.entry, stop=sig.stop, tp1=sig.tp1, tp2=sig.tp2, initial_stop=sig.stop,
                    opened_at=0, leverage=dec.leverage)
