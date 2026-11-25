"""Central Decision Orchestrator.

Receives every engine's signal for a symbol and produces one structured :class:`Decision`.
It does **not** average indicator scores (that is kept only as a baseline policy to test against).

Default policy ``regime_gated``
  1. Safety gate      UNKNOWN / UNSAFE_MARKET / MARKET_DISLOCATION → WAIT (no new risk).
  2. Eligibility      only engines the regime engine permits may supply a *primary* signal.
                      Order flow is primary only in standalone mode with enough validated trades.
  3. Conflict         opposing eligible primaries → WAIT, unless the stronger exceeds the weaker by
                      ``conflict_margin`` confidence points.
  4. Confirmation     order flow ``assess()``: opposition ≥ oppose_threshold → REJECT; support adds a
                      bounded boost; in ``required`` mode support < 50 → WAIT.
  5. Costs            fees + spread + slippage + funding in R; > max_cost_r → REJECT.
  6. Evidence/edge    validated (n ≥ min_validated_trades) → shrunk net expectancy must be
                      ≥ min_net_edge_r. Unvalidated → allowed only as *exploration* on PAPER/TESTNET
                      with TP1 reward − costs ≥ min_rr_after_costs; never on LIVE.
  7. Direction mode   LONG/SHORT/BOTH filter.
The result is a *proposal*; the Portfolio Manager, Execution Specialist and finally the Risk Manager
(veto) decide whether it is executed.

Alternative policies (for research comparison, selectable in config):
  ``majority_vote``      direction with most engine votes (ignores regime eligibility)
  ``confidence_average`` mean signed confidence across engines (naive baseline)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import AppConfig, DirectionMode, ExchangeEnv
from ..core.types import Decision, Direction, EngineSignal, Outcome, Regime, RegimeState, new_id
from .costs import estimate_costs
from .edge import EdgeBook

BLOCKING_REGIMES = {Regime.UNKNOWN, Regime.UNSAFE_MARKET, Regime.MARKET_DISLOCATION}


@dataclass
class SymbolView:
    symbol: str
    ts: int
    price: float
    signals: dict[str, EngineSignal]
    regime: RegimeState
    assess_flow: Callable[[Direction], tuple[float, float]] | None = None
    spread_bps: float | None = None
    funding_rate: float = 0.0
    bar_quote_volume: float = 0.0
    tf_ms: int = 300_000
    extra: dict = field(default_factory=dict)


def _view_label(sig: EngineSignal | None, eligible: bool) -> str:
    if sig is None:
        return "NO DATA"
    if sig.direction is Direction.FLAT:
        lab = f"NEUTRAL ({sig.state})" if sig.state else "NEUTRAL"
    else:
        lab = f"{sig.direction.value} {sig.confidence:.0f}/100"
    return lab if eligible or sig.direction is Direction.FLAT else f"{lab} — not eligible in regime"


class Orchestrator:
    def __init__(self, cfg: AppConfig, edge_book: EdgeBook) -> None:
        self.cfg = cfg
        self.edge = edge_book
        self.decisions_made = 0

    def update_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.edge.prior_strength = cfg.orchestrator.edge_prior_strength
        self.edge.prior_mean = cfg.orchestrator.edge_prior_mean_r

    def _primary_allowed(self, name: str, sig: EngineSignal, regime: RegimeState, policy: str) -> bool:
        if name in ("regime", "stat_arb"):
            return False  # regime is a filter; stat-arb is routed through the pairs path
        if name == "order_flow":
            oc = self.cfg.engines.order_flow
            if oc.role != "standalone":
                return False
            est = self.edge.estimate("order_flow", sig.setup, sig.direction.value)
            if est.n < oc.min_samples_standalone:
                return False
        if policy == "regime_gated":
            return name in regime.permitted_engines
        return True

    def propose(self, v: SymbolView) -> Decision:
        cfg = self.cfg
        oc = cfg.orchestrator
        policy = oc.policy
        self.decisions_made += 1
        d = Decision(id=new_id("dec_"), ts=v.ts, symbol=v.symbol, outcome=Outcome.WAIT, regime=v.regime.regime.value)
        eligible = {n: (n in v.regime.permitted_engines) for n in v.signals}
        d.engine_views = {n: _view_label(s, eligible.get(n, False)) for n, s in v.signals.items() if n != "regime"}
        d.engine_views["regime"] = f"{v.regime.regime.value}" + (f" [{', '.join(v.regime.flags)}]" if v.regime.flags else "")

        if v.regime.regime in BLOCKING_REGIMES:
            d.reasons.append(f"regime {v.regime.regime.value}: no new risk ({'; '.join(v.regime.reasons)})")
            return self._done(d)

        primaries = [(n, s) for n, s in v.signals.items()
                     if s is not None and s.is_actionable and s.direction is not Direction.FLAT
                     and self._primary_allowed(n, s, v.regime, policy)]
        if not primaries:
            d.reasons.append("no eligible engine has a qualified setup — waiting (no forced trades)")
            return self._done(d)

        chosen_dir = self._select_direction(primaries, v, d, policy)
        if chosen_dir is None:
            return self._done(d)
        cands = [(n, s) for n, s in primaries if s.direction is chosen_dir]
        best: tuple | None = None
        for n, s in cands:
            ev = self._evaluate_candidate(n, s, v)
            if best is None or (ev["net_edge"], ev["confidence"]) > (best[2]["net_edge"], best[2]["confidence"]):
                best = (n, s, ev)
        n, s, ev = best
        d.engine, d.setup, d.direction, d.signal = n, s.setup, s.direction, s
        d.entry, d.stop, d.targets = s.entry, s.stop, s.targets
        d.confidence = ev["confidence"]
        d.costs = ev["costs"]
        d.expected_r, d.net_edge_r, d.edge_samples, d.exploration = ev["expected_r"], ev["net_edge"], ev["n"], ev["exploration"]
        d.reasons.extend(s.reasons[:4])
        d.reasons.extend(ev["notes"])
        dm = cfg.system.direction
        if (dm is DirectionMode.LONG and s.direction is Direction.SHORT) or (dm is DirectionMode.SHORT and s.direction is Direction.LONG):
            d.outcome = Outcome.REJECT
            d.reasons.append(f"direction mode {dm.value} excludes {s.direction.value}")
            return self._done(d)
        d.outcome = ev["outcome"]
        if d.outcome in (Outcome.APPROVE_LONG, Outcome.APPROVE_SHORT):
            d.outcome = Outcome.APPROVE_LONG if s.direction is Direction.LONG else Outcome.APPROVE_SHORT
        return self._done(d)

    def _select_direction(self, primaries, v: SymbolView, d: Decision, policy: str) -> Direction | None:
        oc = self.cfg.orchestrator
        longs = [s for _, s in primaries if s.direction is Direction.LONG]
        shorts = [s for _, s in primaries if s.direction is Direction.SHORT]
        if policy == "majority_vote":
            if len(longs) == len(shorts):
                d.reasons.append(f"vote tie {len(longs)}–{len(shorts)}")
                return None
            return Direction.LONG if len(longs) > len(shorts) else Direction.SHORT
        if policy == "confidence_average":
            vals = [s.confidence * s.direction.sign for s in v.signals.values()
                    if s is not None and s.engine not in ("regime", "stat_arb")]
            avg = sum(vals) / max(len(vals), 1)
            d.reasons.append(f"average signed confidence {avg:+.1f}")
            if abs(avg) < 15:
                return None
            return Direction.LONG if avg > 0 else Direction.SHORT
        # regime_gated
        if longs and shorts:
            bl, bs = max(s.confidence for s in longs), max(s.confidence for s in shorts)
            if abs(bl - bs) < oc.conflict_margin:
                d.reasons.append(f"conflicting eligible engines (LONG {bl:.0f} vs SHORT {bs:.0f}) — waiting")
                return None
            d.reasons.append(f"conflict resolved by {abs(bl - bs):.0f}-point confidence margin")
            return Direction.LONG if bl > bs else Direction.SHORT
        return Direction.LONG if longs else Direction.SHORT

    def _evaluate_candidate(self, name: str, s: EngineSignal, v: SymbolView) -> dict:
        cfg, oc = self.cfg, self.cfg.orchestrator
        notes: list[str] = []
        conf = s.confidence
        outcome = Outcome.APPROVE_LONG
        # order-flow confirmation
        if oc.orderflow_confirmation != "off" and v.assess_flow is not None and name != "order_flow":
            support, opposition = v.assess_flow(s.direction)
            ofc = cfg.engines.order_flow
            if opposition >= ofc.oppose_threshold and opposition > support:
                outcome = Outcome.REJECT
                notes.append(f"order flow opposes ({opposition:.0f}/100 vs support {support:.0f})")
            elif oc.orderflow_confirmation == "required" and support < 50:
                outcome = Outcome.WAIT
                notes.append(f"order-flow confirmation required; support only {support:.0f}/100")
            elif support > 50:
                boost = ofc.confirm_boost * (support - 50) / 50
                conf = min(100.0, conf + boost)
                notes.append(f"order flow confirms ({support:.0f}/100, +{boost:.1f} confidence)")
            else:
                notes.append(f"order flow neutral ({support:.0f}/100)")
        # costs
        hold_h = max(s.time_stop_bars, 1) * v.tf_ms / 3_600_000 / 2
        cost = estimate_costs(cfg.costs, direction=s.direction, entry=s.entry, stop=s.stop, atr=s.atr,
                              spread_bps=v.spread_bps, funding_rate=v.funding_rate, hold_hours=hold_h,
                              bar_quote_volume=v.bar_quote_volume)
        cost_r = cost.total_r
        if cost_r > oc.max_cost_r:
            outcome = Outcome.REJECT
            notes.append(f"round-trip costs {cost_r:.2f}R exceed {oc.max_cost_r}R of risk")
        # evidence
        est = self.edge.estimate(name, s.setup, s.direction.value)
        exploration = False
        rr1 = s.reward_r(0)
        if est.n >= oc.min_validated_trades:
            net = est.shrunk_r
            if net < oc.min_net_edge_r and outcome is Outcome.APPROVE_LONG:
                outcome = Outcome.REJECT
                notes.append(f"validated net expectancy {net:+.3f}R (n={est.n}) below {oc.min_net_edge_r}R")
            else:
                notes.append(f"validated net expectancy {net:+.3f}R over {est.n} trades")
        else:
            net = est.shrunk_r
            env = cfg.execution.exchange_env
            if oc.paper_exploration and env in (ExchangeEnv.PAPER, ExchangeEnv.TESTNET):
                exploration = True
                if rr1 - cost_r < oc.min_rr_after_costs and outcome is Outcome.APPROVE_LONG:
                    outcome = Outcome.REJECT
                    notes.append(f"exploration: TP1 {rr1:.2f}R − costs {cost_r:.2f}R < {oc.min_rr_after_costs}R")
                else:
                    notes.append(f"exploration trade (evidence {est.n}/{oc.min_validated_trades}); TP1 {rr1:.2f}R, costs {cost_r:.2f}R")
            elif outcome is Outcome.APPROVE_LONG:
                outcome = Outcome.WAIT
                notes.append(f"insufficient validated evidence ({est.n}/{oc.min_validated_trades} trades) for {env.value}")
        return {"confidence": round(conf, 1), "costs": cost.to_dict(), "net_edge": net, "expected_r": rr1 - cost_r,
                "n": est.n, "exploration": exploration, "outcome": outcome, "notes": notes}

    def _done(self, d: Decision) -> Decision:
        d.explanation = explain(d)
        return d

    # ----------------------------------------------------------------- open positions
    def review_position(self, pos, v: SymbolView, trend_exit: str | None = None) -> Decision | None:
        """CLOSE / REDUCE decisions for an open position (risk-reducing only)."""
        reasons = []
        if v.regime.regime is Regime.MARKET_DISLOCATION:
            reasons.append(f"market dislocation on {v.symbol}: {'; '.join(v.regime.reasons)}")
        if trend_exit and pos.engine == "trend":
            reasons.append(trend_exit)
        if not reasons:
            return None
        d = Decision(id=new_id("dec_"), ts=v.ts, symbol=v.symbol, outcome=Outcome.CLOSE, regime=v.regime.regime.value,
                     engine=pos.engine, setup=pos.setup, direction=pos.direction, reasons=reasons)
        d.explanation = explain(d)
        return d


def explain(d: Decision) -> str:
    lines = [f"INSTRUMENT: {d.symbol}", f"REGIME: {d.regime}"]
    for name, label in d.engine_views.items():
        if name == "regime":
            continue
        lines.append(f"{name.upper().replace('_', ' ')} ENGINE: {label}")
    if d.risk_verdict:
        lines.append(f"RISK MANAGER: {d.risk_verdict}")
    final = {Outcome.APPROVE_LONG: "LONG", Outcome.APPROVE_SHORT: "SHORT"}.get(d.outcome, d.outcome.value)
    lines.append(f"FINAL DECISION: {final}")
    if d.engine and d.outcome in (Outcome.APPROVE_LONG, Outcome.APPROVE_SHORT):
        lines.append(f"PRIMARY: {d.engine} / {d.setup} @ {d.entry:.6g}, stop {d.stop:.6g}, confidence {d.confidence:.0f}")
        if d.costs:
            lines.append(f"COSTS: {d.costs.get('total_r', 0):.3f}R  NET EDGE: {d.net_edge_r:+.3f}R (n={d.edge_samples}"
                         f"{', exploration' if d.exploration else ''})")
    reasons = d.reasons + [f"risk: {r}" for r in d.risk_reasons]
    lines.append("REASON: " + ("; ".join(reasons) if reasons else "—"))
    return "\n".join(lines)
