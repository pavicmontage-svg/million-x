"""Event-driven portfolio backtester.

It drives the *same* :class:`DecisionPipeline` as live trading. Bias controls:
  * look-ahead: features are causal (tested); a decision made at the close of bar t is filled at the
    open of bar t+1; higher-timeframe values come only from completed HTF bars;
  * the signed risk approval is verified against the actual fill price, so entries that gap beyond
    ``max_entry_drift_r`` are cancelled exactly as in live trading;
  * intrabar: stop-loss is assumed hit before targets; gaps through a stop fill at the open;
  * costs: taker fees on every fill, slippage = base + volatility + square-root impact, half spread,
    historical funding at funding timestamps (default rate when history is missing);
  * liquidity: orders above ``max_bar_participation`` of bar quote volume are vetoed by risk;
  * survivorship: the universe is whatever the caller passes — use instruments chosen *as of* the
    test start (see docs/BACKTESTING.md).
"""
from __future__ import annotations

import copy
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..agents import ExecutionSpecialist, PortfolioManager
from ..agents.portfolio_manager import correlation_matrix
from ..config import AppConfig
from ..core.logging import get_logger
from ..core.types import ContractSpec, Decision, Direction, Outcome, Position, TradeRecord, new_id
from ..data.candles import Candles
from ..engines import build_engines
from ..engines.base import MarketContext, fval
from ..execution.exits import ExitFill, simulate_bar, trail_level
from ..orchestrator.costs import slippage_bps
from ..orchestrator.decision import Orchestrator
from ..orchestrator.edge import EdgeBook
from ..orchestrator.pipeline import DecisionPipeline
from ..risk.approval import ApprovalError
from ..risk.manager import RiskManager
from ..risk.portfolio import PortfolioState
from .metrics import compute_metrics

log = get_logger("backtest")
FUNDING_HOURS = (0, 8, 16)


@dataclass
class BacktestResult:
    label: str
    trades: list[TradeRecord]
    equity_ts: np.ndarray
    equity: np.ndarray
    metrics: dict
    decisions: dict
    regimes: dict
    breaker_events: list[dict]
    start_ts: int
    end_ts: int
    equity0: float
    runtime_s: float
    config: dict = field(default_factory=dict)

    def summary(self) -> dict:
        m = self.metrics
        keys = ("trades", "net_pnl", "return_pct", "win_rate", "profit_factor", "expectancy_r", "max_drawdown_pct",
                "sharpe", "sortino", "calmar", "fees", "funding", "slippage")
        return {"label": self.label, **{k: m.get(k) for k in keys}}

    def to_dict(self, max_trades: int = 2000, max_points: int = 1500) -> dict:
        step = max(1, len(self.equity) // max_points)
        return {
            "label": self.label, "start_ts": self.start_ts, "end_ts": self.end_ts, "equity0": self.equity0,
            "metrics": self.metrics, "decisions": self.decisions, "regimes": self.regimes,
            "breaker_events": self.breaker_events[-100:], "runtime_s": round(self.runtime_s, 2),
            "equity_curve": {"ts": self.equity_ts[::step].tolist(), "equity": np.round(self.equity[::step], 4).tolist()},
            "trades": [t.to_dict() for t in self.trades[-max_trades:]],
        }


class Backtester:
    def __init__(self, cfg: AppConfig, data: dict[str, Candles], *, funding: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
                 specs: dict[str, ContractSpec] | None = None, equity0: float | None = None, label: str = "backtest",
                 correlation_refresh_bars: int = 288) -> None:
        self.cfg = copy.deepcopy(cfg)  # isolated: never mutates the caller's (live) configuration
        self.data = data
        self.funding = funding or {}
        self.specs = specs or {}
        self.equity0 = float(equity0 if equity0 is not None else cfg.system.paper_starting_equity)
        self.label = label
        self.corr_every = correlation_refresh_bars

    def _spec(self, s: str) -> ContractSpec:
        if s in self.specs:
            return self.specs[s]
        px = float(np.median(self.data[s].close[-100:]))
        # generic spec scaled to price so tiny/huge prices round sensibly
        step = 10 ** np.floor(np.log10(max(1e-8, 10 / px)))
        tick = 10 ** np.floor(np.log10(max(1e-10, px * 1e-5)))
        return ContractSpec(s, tick_size=float(tick), step_size=float(step), min_qty=float(step), min_notional=5.0)

    def run(self, start_ts: int | None = None, end_ts: int | None = None, *, progress=None) -> BacktestResult:
        t0 = time.time()
        cfg = self.cfg
        engines = build_engines(cfg)
        edge = EdgeBook(cfg.orchestrator.edge_prior_strength, cfg.orchestrator.edge_prior_mean_r)
        risk = RiskManager(cfg)
        breaker_events: list[dict] = []
        risk.breakers.on_event = lambda e, b: breaker_events.append({"event": e, **b.to_dict()})
        pipe = DecisionPipeline(cfg, engines, Orchestrator(cfg, edge), risk, PortfolioManager(cfg), ExecutionSpecialist(cfg))
        syms = sorted(self.data)
        specs = {s: self._spec(s) for s in syms}
        ctxs = {s: MarketContext(s, self.data[s], spec=specs[s]) for s in syms}
        feats = {s: pipe.features(ctxs[s]) for s in syms}
        idx_of = {s: {int(t): i for i, t in enumerate(self.data[s].close_ts)} for s in syms}
        timeline = np.unique(np.concatenate([self.data[s].close_ts for s in syms]))
        if start_ts is not None:
            timeline = timeline[timeline >= start_ts]
        if end_ts is not None:
            timeline = timeline[timeline <= end_ts]
        pf = PortfolioState(self.equity0)
        trades: list[TradeRecord] = []
        pending: dict[str, Decision] = {}
        meta: dict[str, dict] = {}
        eq_ts, eq_v = [], []
        outcome_counts: Counter = Counter()
        veto_reasons: Counter = Counter()
        regime_counts: Counter = Counter()
        prev_ts: int | None = None
        costs = cfg.costs
        tf_ms = self.data[syms[0]].tf_ms

        def fill_px(px: float, side_sign: int, s: str, i: int, notional: float) -> float:
            c = self.data[s]
            atr = fval(feats[s]["vwap"], "atr", i, 0.0)
            bps = slippage_bps(costs, px, atr, notional, float(c.quote_volume[i])) + 0.5  # + half of a 1bps spread
            return px * (1 + side_sign * bps / 1e4)

        def close_trade(p: Position, s: str, fills: list[tuple[float, float, float, str, float]], ts: int) -> None:
            m = meta.pop(p.id)
            s_ = p.direction.sign
            all_fills = m["all_exits"] + fills  # (qty, price, fee, reason, reference price)
            gross = sum((f[1] - m["entry_px"]) * f[0] * s_ for f in all_fills)
            qty_all = sum(f[0] for f in all_fills)
            exit_px = sum(f[0] * f[1] for f in all_fills) / qty_all
            fees = m["entry_fee"] + sum(f[2] for f in all_fills)
            slip = m["entry_slip"] + sum(abs(f[1] - f[4]) * f[0] for f in all_fills)
            net = gross - fees - m["funding"]
            r = net / m["init_risk"] if m["init_risk"] > 0 else 0.0
            tr = TradeRecord(new_id("bt_"), s, p.direction.value, p.engine, p.setup, p.regime, m["entry_px"], exit_px,
                             p.initial_qty, p.opened_ts, ts, gross, fees, m["funding"], slip, net, r, m["init_risk"],
                             all_fills[-1][3], p.decision_id, "backtest", "", m["confidence"], m["expected_r"],
                             {"tp_hits": p.tp_hits, "bars_held": p.bars_held, "exploration": m["exploration"]})
            trades.append(tr)
            pf.on_trade_closed(tr)
            pf.positions.remove(p)

        def book_exit(p: Position, s: str, i: int, ef: ExitFill, ts: int) -> None:
            side = -p.direction.sign  # closing side
            px = fill_px(ef.price, side, s, i, ef.qty * ef.price)
            fee = ef.qty * px * costs.taker_fee
            gross_part = (px - meta[p.id]["entry_px"]) * ef.qty * p.direction.sign
            pf.balance += gross_part - fee
            row = (ef.qty, px, fee, ef.reason, ef.price)
            if p.qty <= 1e-12:
                close_trade(p, s, [row], ts)
            else:
                meta[p.id]["all_exits"].append(row)

        for k, ts in enumerate(timeline):
            ts = int(ts)
            active_syms = [s for s in syms if ts in idx_of[s]]
            # 1) fills and exits on this bar
            for s in active_syms:
                i = idx_of[s][ts]
                c = self.data[s]
                o, h, l, cl = float(c.open[i]), float(c.high[i]), float(c.low[i]), float(c.close[i])
                d = pending.pop(s, None)
                if d is not None:
                    side = "BUY" if d.direction is Direction.LONG else "SELL"
                    try:
                        risk.signer.verify(d.approval, symbol=s, side=side, qty=d.qty, price=o)
                        valid = (d.stop - o) * d.direction.sign < 0
                    except ApprovalError as e:
                        valid = False
                        outcome_counts["ENTRY_CANCELLED"] += 1
                        veto_reasons[f"entry cancelled: {str(e)[:40]}"] += 1
                    if valid:
                        px = fill_px(o, d.direction.sign, s, i, d.qty * o)
                        fee = d.qty * px * costs.taker_fee
                        pf.balance -= fee
                        sig = d.signal
                        p = Position(new_id("btp_"), s, d.direction, d.engine, d.setup, px, d.qty, d.qty, d.stop, d.stop,
                                     copy.deepcopy(d.targets), ts - tf_ms, decision_id=d.id,
                                     trail_atr=sig.trail_atr if sig else 0.0, atr=sig.atr if sig else 0.0,
                                     time_stop_bars=sig.time_stop_bars if sig else 0, best_price=px, regime=d.regime,
                                     leverage=d.costs.get("leverage", 1.0))
                        pf.positions.append(p)
                        meta[p.id] = {"entry_px": px, "entry_fee": fee, "entry_slip": abs(px - o) * d.qty,
                                      "funding": 0.0, "init_risk": abs(px - d.stop) * d.qty, "all_exits": [],
                                      "confidence": d.confidence, "expected_r": d.expected_r, "exploration": d.exploration}
                for p in [p for p in pf.positions if p.symbol == s]:
                    for ef in simulate_bar(p, o, h, l, cl, move_to_be=cfg.execution.move_stop_to_be_after_tp1):
                        book_exit(p, s, i, ef, ts)
                pf.prices[s] = cl
            # 2) funding at 00/08/16 UTC crossed during (prev_ts, ts]
            if prev_ts is not None:
                for fh in FUNDING_HOURS:
                    day0 = (ts // 86_400_000) * 86_400_000
                    for ft in (day0 + fh * 3_600_000, day0 - 86_400_000 + fh * 3_600_000):
                        if prev_ts < ft <= ts:
                            for p in pf.positions:
                                rate = self._funding_rate(p.symbol, ft)
                                amt = p.qty * pf.prices.get(p.symbol, p.entry_price) * rate * p.direction.sign
                                pf.balance -= amt
                                if p.id in meta:
                                    meta[p.id]["funding"] += amt
            # 3) post-bar management: trailing, time stop, deterioration / dislocation exits
            for s in active_syms:
                i = idx_of[s][ts]
                c = self.data[s]
                atr = fval(feats[s]["vwap"], "atr", i, 0.0)
                for p in [p for p in pf.positions if p.symbol == s]:
                    p.bars_held += 1
                    lvl = trail_level(p, atr)
                    if lvl is not None:
                        p.stop = lvl
                    reason = None
                    if p.time_stop_bars and p.bars_held >= p.time_stop_bars:
                        reason = "time_stop"
                    elif p.engine == "trend":
                        reason = engines["trend"].exit_signal(feats[s]["trend"], i, p.direction, float(c.close[i]))
                        reason = "signal_exit" if reason else None
                    if reason:
                        q = p.qty
                        p.qty = 0.0
                        book_exit(p, s, i, ExitFill(q, float(c.close[i]), reason), ts)
            # 4) periods, correlations, breakers
            pf.roll(ts)
            if self.corr_every and k % self.corr_every == 0 and len(syms) > 1:
                sliced = {s: self.data[s].slice(0, idx_of[s][ts] + 1) for s in active_syms}
                pf.correlations = correlation_matrix(sliced)
            # 5) decisions at the close of this bar (filled next bar)
            views = []
            for s in active_syms:
                i = idx_of[s][ts]
                if i + 1 >= len(self.data[s]) or i < 300:
                    continue
                if any(p.symbol == s for p in pf.positions) or s in pending:
                    continue
                v = pipe.symbol_view(ctxs[s], feats[s], i)
                regime_counts[v.regime.regime.value] += 1
                # dislocation exits for open positions are handled via orchestrator.review_position in live; in
                # backtests positions are only opened on symbols without a position, so nothing more to do here
                views.append(v)
            if views and not cfg.system.trading_paused:
                for d in pipe.decide(views, pf, specs):
                    outcome_counts[d.outcome.value] += 1
                    if d.outcome in (Outcome.APPROVE_LONG, Outcome.APPROVE_SHORT):
                        pending[d.symbol] = d
                    elif d.risk_verdict == "VETO":
                        for r in d.risk_reasons[:2]:
                            veto_reasons[r.split(" ")[0] + " " + " ".join(r.split(" ")[1:4])] += 1
            eq_ts.append(ts)
            eq_v.append(pf.equity)
            prev_ts = ts
            if progress and k % 2000 == 0:
                progress(k / max(len(timeline), 1))
        # close anything still open at the last price
        for p in list(pf.positions):
            s = p.symbol
            i = len(self.data[s]) - 1 if end_ts is None else max(0, int(np.searchsorted(self.data[s].close_ts, end_ts, "right")) - 1)
            q = p.qty
            p.qty = 0.0
            book_exit(p, s, i, ExitFill(q, float(self.data[s].close[i]), "end_of_test"), int(timeline[-1]) if len(timeline) else 0)
        if eq_v:
            eq_v[-1] = pf.equity  # final point includes the end-of-test exit costs
        st =int(timeline[0]) if len(timeline) else 0
        en = int(timeline[-1]) if len(timeline) else 0
        metrics = compute_metrics(trades, self.equity0, start_ms=st, end_ms=en)
        eq_arr = np.array(eq_v)
        if len(eq_arr):
            peak = np.maximum.accumulate(np.concatenate([[self.equity0], eq_arr]))
            metrics["max_drawdown_mtm_pct"] = round(float(np.max((peak[1:] - eq_arr) / peak[1:]) * 100), 4)
        metrics["engine_signals"] = {n: e.stats.signals for n, e in engines.items() if n != "regime"}
        return BacktestResult(self.label, trades, np.array(eq_ts), eq_arr, metrics,
                              {"outcomes": dict(outcome_counts), "top_veto_reasons": dict(veto_reasons.most_common(12))},
                              dict(regime_counts), breaker_events, st, en, self.equity0, time.time() - t0)

    def _funding_rate(self, symbol: str, ts: int) -> float:
        f = self.funding.get(symbol)
        if f is None or len(f[0]) == 0:
            return self.cfg.costs.default_funding_rate
        j = int(np.searchsorted(f[0], ts, side="right")) - 1
        if j < 0 or ts - int(f[0][j]) > 9 * 3_600_000:
            return self.cfg.costs.default_funding_rate
        return float(f[1][j])
