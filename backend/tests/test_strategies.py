"""Strategy correctness: no look-ahead, distinct modules, valid signals."""
import inspect

import numpy as np

from millionx.backtest.data import synthetic_bars
from millionx.strategies import BASE_CLASSES, MarketData, build_bots


def _signals(bars, upto):
    md = MarketData("TESTUSDT", bars.slice(0, upto))
    bots = [b for b in build_bots() if b.bot_id != "bot10_adaptive"]
    for b in bots:
        b.prepare(md)
    return {b.bot_id: {i: b.signal_at(i) for i in b.close_idx} for b in bots}


def test_no_lookahead():
    """A signal at bar i must be identical whether or not later bars exist."""
    bars = synthetic_bars(12_000, seed=11)
    full = _signals(bars, len(bars))
    checked = 0
    for cut in (6_000, 8_000, 10_000):
        part = _signals(bars, cut)
        for bot, sigs in part.items():
            for i, s in sigs.items():
                f = full[bot].get(i)
                if s is None:
                    assert f is None, f"{bot} at {i}: signal appears only with future data"
                else:
                    assert f is not None, f"{bot} at {i}: signal vanishes with future data"
                    assert (s.side, round(s.stop, 9), round(s.tp2, 9)) == (f.side, round(f.stop, 9), round(f.tp2, 9))
                    checked += 1
    assert checked > 50


def test_signals_are_geometrically_valid():
    bars = synthetic_bars(20_000, seed=3)
    md = MarketData("TESTUSDT", bars)
    for b in build_bots():
        if b.bot_id == "bot10_adaptive":
            continue
        b.prepare(md)
        for i in b.close_idx:
            s = b.signal_at(int(i))
            if s is None:
                continue
            if int(s.side) == 1:
                assert s.stop < s.entry < s.tp1 <= s.tp2
            else:
                assert s.stop > s.entry > s.tp1 >= s.tp2
            assert 0 <= s.confidence <= 1


def test_ten_distinct_modules():
    bots = build_bots()
    assert len(bots) == 10
    assert len({b.bot_id for b in bots}) == 10
    sources = {inspect.getsource(type(b).evaluate) if b.bot_id != "bot10_adaptive"
               else inspect.getsource(type(b).signal_at) for b in bots}
    assert len(sources) == 10, "each bot must have its own algorithm"
    assert len({inspect.getmodule(type(b)).__name__ for b in bots}) == 10


def test_bot9_is_not_presented_as_moneyline():
    slot9 = [c for c in BASE_CLASSES if c.bot_id == "bot9_rangeexp"][0]
    assert not slot9.is_replacement and "not Moneyline" in slot9.name
    assert "not implemented" in slot9.description


def test_adaptive_stays_flat_without_validated_stats():
    from millionx.competition import RegimeStats
    bars = synthetic_bars(8_000, seed=4)
    md = MarketData("T", bars)
    stats = RegimeStats()
    bots = build_bots(stats)
    for b in bots:
        b.prepare(md)
    ad = bots[-1]
    assert all(ad.signal_at(i) is None for i in range(len(bars)))


def test_tight_stops_widened_for_fees_keep_reward_risk():
    """A stop that would let fees eat ~45% of the risk is widened; targets scale with it."""
    from millionx.config import FeeModel
    from millionx.risk import RiskManager
    from millionx.config import RiskLimits
    from millionx.risk import SymbolRules
    from millionx.strategies import base as B
    from millionx.models import Side
    m = B.set_min_stop_from_fees(FeeModel(), 0.25)
    assert abs(m - 0.0042) < 1e-12

    class Tight(B.Strategy):
        bot_id = "t"
        warmup_bars = 0

        def compute(self, b):
            self.f["x"] = b.close

        def evaluate(self, j):
            e = float(self.b.close[j])
            return Side.LONG, e * (1 - 0.0018), e * (1 + 0.0036), e * (1 + 0.0054), 0.6, "tight"

    from millionx.backtest.data import synthetic_bars
    from millionx.strategies.base import MarketData
    st = Tight({"timeframe": 1})
    st.prepare(MarketData("XUSDT", synthetic_bars(400, seed=3)))
    sig = next(s for s in (st.signal_at(i) for i in range(100, 400)) if s is not None)
    d = sig.entry - sig.stop
    assert abs(d / sig.entry - 0.0042) < 1e-9
    assert abs((sig.tp1 - sig.entry) / d - 2.0) < 1e-9 and abs((sig.tp2 - sig.entry) / d - 3.0) < 1e-9
    assert "stop widened" in sig.reason
    rm = RiskManager(RiskLimits(), FeeModel())
    rm.state.equity = 43.0
    dec = rm.size(sig, SymbolRules("XUSDT", min_notional=5.0, step_size=0.0001, min_qty=0.0001), [], 0.004,
                  sig.bar_time)
    assert dec.approved, dec.reasons  # fees are now ~25% of risk, below the 35% limit
    assert dec.expected_cost / dec.risk_amount < 0.3
    B.set_min_stop_from_fees(FeeModel(), 0)  # disabled -> untouched
    st2 = Tight({"timeframe": 1})
    st2.prepare(MarketData("XUSDT", synthetic_bars(400, seed=3)))
    s2 = next(s for s in (st2.signal_at(i) for i in range(100, 400)) if s is not None)
    assert abs((s2.entry - s2.stop) / s2.entry - 0.0018) < 1e-9
    B.set_min_stop_from_fees(FeeModel(), 0.25)
