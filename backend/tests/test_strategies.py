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


def test_slot9_is_clearly_labelled_replacement():
    slot9 = [c for c in BASE_CLASSES if c.bot_id == "bot9_rangeexp"][0]
    assert slot9.is_replacement and "not Moneyline" in slot9.name


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
