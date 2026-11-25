"""The decision pipeline — one code path for backtest, paper and live.

    engines → regime gate → Orchestrator.propose → PortfolioManager.rank
            → ExecutionSpecialist.pre_trade → RiskManager.review (veto, sizing, signed approval)

The backtester and the live runtime both call :meth:`DecisionPipeline.symbol_view` and
:meth:`DecisionPipeline.decide`; only the source of market data and the order venue differ.
"""
from __future__ import annotations

from ..agents.execution_specialist import ExecutionSpecialist
from ..agents.market_analyst import MarketAnalyst
from ..agents.portfolio_manager import PortfolioManager
from ..config import AppConfig
from ..core.logging import get_logger
from ..core.types import ContractSpec, Decision, Direction, EngineSignal, Outcome, Position, RegimeState, new_id
from ..engines.base import Engine, MarketContext, fval
from ..risk.manager import MarketCheck, RiskManager
from ..risk.portfolio import PortfolioState
from .decision import Orchestrator, SymbolView, explain

log = get_logger("pipeline")
SIGNAL_ENGINES = ("vwap", "trend", "breakout", "order_flow")


class DecisionPipeline:
    def __init__(self, cfg: AppConfig, engines: dict[str, Engine], orchestrator: Orchestrator, risk: RiskManager,
                 portfolio: PortfolioManager, execution: ExecutionSpecialist, analyst: MarketAnalyst | None = None) -> None:
        self.cfg = cfg
        self.engines = engines
        self.orch = orchestrator
        self.risk = risk
        self.pm = portfolio
        self.ex = execution
        self.analyst = analyst or MarketAnalyst()

    def update_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.orch.update_config(cfg)
        self.risk.update_config(cfg)
        self.pm.cfg = cfg
        self.ex.cfg = cfg
        mapping = {"vwap": cfg.engines.vwap, "trend": cfg.engines.trend, "breakout": cfg.engines.breakout,
                   "order_flow": cfg.engines.order_flow, "stat_arb": cfg.engines.stat_arb, "regime": cfg.engines.regime}
        for n, e in self.engines.items():
            e.cfg = mapping[n]

    def features(self, ctx: MarketContext) -> dict[str, dict]:
        return {n: self.engines[n].features(ctx) for n in ("regime", *SIGNAL_ENGINES)}

    def symbol_view(self, ctx: MarketContext, feats: dict[str, dict], i: int) -> SymbolView:
        reg_eng = self.engines["regime"]
        regime: RegimeState = reg_eng.classify(ctx, feats["regime"], i)
        reg_eng.last_states[ctx.symbol] = regime
        ctx.regime = regime
        signals: dict[str, EngineSignal] = {}
        for name in SIGNAL_ENGINES:
            e = self.engines[name]
            if i + 1 < e.min_bars():
                signals[name] = e.flat(ctx, i, "WARMUP", ["warming up"])
                continue
            signals[name] = e.finalize(ctx, e.evaluate(ctx, feats[name], i))
        of = self.engines["order_flow"]

        def assess(d: Direction) -> tuple[float, float]:
            s, o, _ = of.assess(ctx, feats["order_flow"], i, d)
            return s, o

        c = ctx.candles
        spread = ctx.micro.spread_bps if ctx.micro is not None else None
        return SymbolView(ctx.symbol, int(c.close_ts[i]), float(c.close[i]), signals, regime, assess, spread,
                          ctx.funding_rate, float(c.quote_volume[i]), c.tf_ms,
                          extra={"atr": fval(feats["vwap"], "atr", i), "data_age_s": ctx.data_age_s, "book": None})

    def decide(self, views: list[SymbolView], pf: PortfolioState, specs: dict[str, ContractSpec], *,
               books: dict[str, dict] | None = None, trading_paused: bool = False,
               market_block: bool = False) -> list[Decision]:
        books = books or {}
        active = self.risk.breakers.evaluate(pf, views[0].ts if views else None)
        if market_block or "EMERGENCY_STOP" in active:
            why = "emergency stop" if "EMERGENCY_STOP" in active else "market analyst: majority of universe unsafe"
            d = Decision(new_id("dec_"), views[0].ts if views else 0, "*", Outcome.BLOCK_ALL_TRADING, reasons=[why])
            d.explanation = explain(d)
            return [d]
        proposals = [self.orch.propose(v) for v in views]
        by_symbol = {v.symbol: v for v in views}
        ranked = self.pm.rank(proposals, pf)
        provisional: list[Position] = []
        try:
            for d in ranked:
                v = by_symbol[d.symbol]
                spec = specs.get(d.symbol) or ContractSpec(d.symbol)
                atr = d.signal.atr if d.signal else 0.0
                mkt = MarketCheck(spread_bps=v.spread_bps, funding_rate=v.funding_rate,
                                  data_age_s=v.extra.get("data_age_s", 0.0), bar_quote_volume=v.bar_quote_volume,
                                  atr=atr, regime_multiplier=v.regime.risk_multiplier, price=v.price)
                verdict = self.risk.review(d, pf, spec, mkt, trading_paused=trading_paused)
                if verdict.approved:
                    ex = self.ex.pre_trade(d, verdict.qty, book=books.get(d.symbol), spread_bps=v.spread_bps,
                                           bar_quote_volume=v.bar_quote_volume, atr=atr)
                    if not ex["ok"]:
                        d.outcome = Outcome.REJECT
                        d.risk_verdict = "APPROVED, EXECUTION REJECTED"
                        d.reasons.extend(f"execution: {n}" for n in ex["notes"])
                        d.explanation = explain(d)
                        continue
                    d.qty, d.risk_usdt, d.approval = verdict.qty, verdict.risk_usdt, verdict.approval
                    d.risk_verdict = f"APPROVED (risk {verdict.risk_usdt:.2f} USDT, {verdict.leverage:.0f}x, liq {verdict.liquidation_price:.6g})"
                    d.costs = {**d.costs, "order_type": ex["order_type"], "expected_slippage_bps": ex["expected_slippage_bps"],
                               "leverage": verdict.leverage}
                    provisional.append(Position(new_id("prov_"), d.symbol, d.direction, d.engine, d.setup, d.entry,
                                                verdict.qty, verdict.qty, d.stop, d.stop, d.targets, d.ts,
                                                leverage=verdict.leverage))
                    pf.positions.append(provisional[-1])
                else:
                    d.outcome = Outcome.REJECT
                    d.risk_verdict = "VETO"
                    d.risk_reasons = verdict.reasons
                d.explanation = explain(d)
        finally:
            for p in provisional:
                pf.positions.remove(p)
        return proposals
