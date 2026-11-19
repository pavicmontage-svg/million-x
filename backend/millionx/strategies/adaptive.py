"""BOT 10 — ADAPTIVE AI SCALPER.

A meta-strategy: identifies the current regime (trend x volatility), asks the
RegimeStats book (built only from trades that already closed — no look-ahead)
which strategy has a *validated* edge in that regime, and only then relays that
strategy's own signal. With no validated strategy for the regime it stays flat.
It never invents a signal of its own.
"""
from __future__ import annotations

from typing import Optional

from ..models import Signal
from ..regime import regime_key
from .base import MarketData, Strategy


class AdaptiveAIScalper(Strategy):
    bot_id = "bot10_adaptive"
    name = "Adaptive AI Scalper"
    description = "Regime-aware selector that relays the best validated strategy for the current regime."
    default_params = {"timeframe": 1, "min_trades": 30, "min_pf": 1.2, "min_exp_r": 0.05}
    param_grid = {"min_trades": [20, 30, 50], "min_pf": [1.1, 1.2, 1.4]}
    warmup_bars = 0

    def __init__(self, params=None, members: Optional[list[Strategy]] = None, stats=None):
        super().__init__(params)
        self.members = members or []
        self.stats = stats  # competition.RegimeStats

    def prepare(self, md: MarketData) -> None:
        # members are prepared by the engine; we only keep a handle on the data
        self.md = md
        self.j_of_i = None

    def compute(self, b):
        pass

    def signal_at(self, i: int) -> Optional[Signal]:
        if self.md is None or self.stats is None or not self.members:
            return None
        trend, vol = self.md.regime_at_1m(i)
        key = regime_key(trend, vol)
        best = self.stats.best(key, [m.bot_id for m in self.members], int(self.params["min_trades"]),
                               float(self.params["min_pf"]), float(self.params["min_exp_r"]))
        if best is None:
            return None
        member = next((m for m in self.members if m.bot_id == best), None)
        if member is None:
            return None
        s = member.signal_at(i)
        if s is None:
            return None
        st = self.stats.get(key, best)
        relay = Signal(bot_id=self.bot_id, symbol=s.symbol, side=s.side, entry=s.entry, stop=s.stop,
                       tp1=s.tp1, tp2=s.tp2, confidence=min(1.0, s.confidence * min(1.5, st["pf"] / 1.2)),
                       bar_time=s.bar_time, timeframe=s.timeframe,
                       reason=f"regime {key}: relaying {best} (n={st['n']}, PF={st['pf']:.2f}) — {s.reason}",
                       features=dict(s.features, source_bot=best))
        return relay
