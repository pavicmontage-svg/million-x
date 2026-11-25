from collections import Counter

import numpy as np
import pytest

from quanttitan.config import AppConfig, BreakoutConfig, StatArbConfig, VWAPConfig, rescale_timeframe

CFG5 = lambda: rescale_timeframe(AppConfig(), "5m")  # synthetic fixtures are 5m bars
from quanttitan.core.types import Direction, Regime
from quanttitan.data.candles import Candles
from quanttitan.data.synthetic import cointegrated_pair, generate, random_walk_pair, synthetic_book
from quanttitan.data.micro import micro_stats
from quanttitan.engines import build_engines
from quanttitan.engines.base import MarketContext
from quanttitan.engines.breakout import BreakoutEngine
from quanttitan.engines.regime import RegimeEngine
from quanttitan.engines.stat_arb import StatArbEngine, analyze_pair
from quanttitan.engines.vwap import VWAPEngine


@pytest.fixture(scope="module")
def synth():
    return generate(6000, seed=11)


def _signals(engine, candles):
    ctx = MarketContext(candles.symbol, candles)
    f = engine.features(ctx)
    out = []
    for i in range(engine.min_bars(), len(candles)):
        s = engine.evaluate(ctx, f, i)
        out.append((i, s))
    return out


@pytest.mark.parametrize("name", ["vwap", "trend", "breakout", "order_flow"])
def test_each_engine_is_independent_and_produces_signals(synth, name):
    candles, _ = synth
    engines = build_engines(CFG5())
    eng = engines[name]
    sigs = _signals(eng, candles)
    directional = [s for _, s in sigs if s.direction is not Direction.FLAT]
    assert directional, f"{name} produced no signals on 6000 regime-switching bars"
    for s in directional:
        assert s.engine == name
        assert 0 <= s.confidence <= 100
        assert s.stop > 0 and s.entry > 0 and s.targets
        # stop must be on the losing side
        assert (s.entry - s.stop) * s.direction.sign > 0
        assert s.reasons and s.components
    # engines label states for neutral bars too
    assert len({s.state for _, s in sigs}) >= 2


@pytest.mark.parametrize("name", ["vwap", "trend", "breakout", "order_flow", "regime"])
def test_live_path_equals_backtest_path(synth, name):
    """analyze() on a truncated buffer must equal evaluate() on full-history features (no lookahead)."""
    candles, _ = synth
    full_eng = build_engines(CFG5())[name]
    ctx_full = MarketContext(candles.symbol, candles)
    f_full = full_eng.features(ctx_full)
    for i in (2500, 3333, 4999):
        a = full_eng.evaluate(ctx_full, f_full, i)
        live = build_engines(CFG5())[name]
        ctx = MarketContext(candles.symbol, candles.slice(0, i + 1))
        b = live.evaluate(ctx, live.features(ctx), i)
        assert a.direction == b.direction and a.state == b.state
        assert a.confidence == pytest.approx(b.confidence)
        assert a.stop == pytest.approx(b.stop)


def _bars(closes, *, lows=None, highs=None, vols=None, taker=None, start_ts=1_767_225_600_000):
    closes = np.asarray(closes, float)
    n = len(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.02 if highs is None else np.asarray(highs, float)
    lows = np.minimum(opens, closes) - 0.02 if lows is None else np.asarray(lows, float)
    vols = np.full(n, 1000.0) if vols is None else np.asarray(vols, float)
    tb = vols * 0.5 if taker is None else np.asarray(taker, float) * vols
    ts = start_ts + np.arange(n, dtype=np.int64) * 300_000
    return Candles("TESTUSDT", "5m", ts, opens, highs, lows, closes, vols, vols * closes, np.full(n, 100.0), tb)


def test_vwap_reversion_long_after_rejection():
    # one UTC session with an intraday swing (sigma >> ATR, as in real sessions), then a sharp
    # 2-bar dip below the lower band and a bullish rejection bar that closes back inside the band
    rng = np.random.default_rng(0)
    n = 288
    closes = 100 + 1.5 * np.sin(np.linspace(0, 4 * np.pi, n)) + 0.05 * rng.standard_normal(n)
    closes[-3:-1] = [98.8, 98.6]
    closes[-1] = 99.0
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.02
    lows = np.minimum(opens, closes) - 0.02
    lows[-1] = 98.3  # long lower wick = rejection
    vols = np.full(n, 1000.0); vols[-1] = 2500
    taker = np.full(n, 0.5); taker[-1] = 0.65
    c = _bars(closes, lows=lows, highs=highs, vols=vols, taker=taker, start_ts=1_767_225_600_000)
    eng = VWAPEngine(CFG5().engines.vwap.copy(update={'min_confidence': 0}))
    ctx = MarketContext("TESTUSDT", c)
    sig = eng.evaluate(ctx, eng.features(ctx), n - 1)
    assert sig.direction is Direction.LONG, (sig.state, sig.reasons)
    assert sig.setup == "VWAP_REVERSION"
    assert sig.targets[0].price == pytest.approx(sig.features["vwap"])
    assert sig.stop < 98.3
    assert sig.reward_r(0) >= 1.0


def test_vwap_does_not_fade_band_riding_trend():
    n = 400
    closes = np.concatenate([np.full(300, 100.0) + np.sin(np.arange(300)) * 0.1, 100 - np.arange(1, 101) * 0.08])
    closes[-1] = closes[-2] + 0.05  # small up bar inside a down-trend
    c = _bars(closes, start_ts=1_767_225_600_000 - 112 * 300_000)
    eng = VWAPEngine(CFG5().engines.vwap.copy(update={'min_confidence': 0}))
    ctx = MarketContext("TESTUSDT", c)
    sig = eng.evaluate(ctx, eng.features(ctx), n - 1)
    assert sig.direction is Direction.FLAT
    assert sig.state in ("STRONG_TREND_CONTINUATION", "NORMAL_DEVIATION", "OVEREXTENDED")


def _compression_then_breakout(breakout_volume):
    rng = np.random.default_rng(5)
    n = 800
    closes = np.linspace(80, 100, n - 80) + 0.3 * rng.standard_normal(n - 80)  # rising, normally volatile history
    closes = np.concatenate([closes, closes[-1] + 0.08 * rng.standard_normal(79)])  # tight box
    lvl = closes[-1]
    closes = np.concatenate([closes, [lvl + 0.45]])
    vols = np.full(n, 1000.0); vols[-1] = breakout_volume
    taker = np.full(n, 0.5); taker[-1] = 0.7
    return _bars(closes, vols=vols, taker=taker)


def test_breakout_valid_and_low_volume():
    cfg = CFG5().engines.breakout.copy(update={'min_confidence': 0, 'max_box_atr': 20})
    for vol, expected in ((4000.0, Direction.LONG), (900.0, Direction.FLAT)):
        c = _compression_then_breakout(vol)
        eng = BreakoutEngine(cfg)
        ctx = MarketContext("TESTUSDT", c)
        sig = eng.evaluate(ctx, eng.features(ctx), len(c) - 1)
        assert sig.direction is expected, (vol, sig.state, sig.reasons)
        if expected is Direction.FLAT:
            assert sig.state == "LOW_VOLUME_BREAKOUT"
        else:
            assert sig.stop < sig.entry < sig.targets[0].price


def test_regime_classifier_tracks_ground_truth(synth):
    candles, truth = synth
    eng = RegimeEngine(CFG5().engines.regime)
    labels = eng.classify_series(MarketContext(candles.symbol, candles))
    pairs = Counter()
    for lab, t in zip(labels[400:], truth[400:]):
        pairs[(t, lab)] += 1
    trend_bars = sum(v for (t, _), v in pairs.items() if t.startswith("trend"))
    trend_hits = sum(v for (t, l), v in pairs.items() if t.startswith("trend") and l in ("STRONG_TREND", "WEAK_TREND", "VOLATILITY_EXPANSION"))
    range_bars = sum(v for (t, _), v in pairs.items() if t in ("range", "compression"))
    range_trend = sum(v for (t, l), v in pairs.items() if t in ("range", "compression") and l == "STRONG_TREND")
    assert trend_hits / trend_bars > 0.5
    assert range_trend / range_bars < 0.2
    shock_unsafe = sum(v for (t, l), v in pairs.items() if t == "shock" and l in ("MARKET_DISLOCATION", "HIGH_VOLATILITY", "VOLATILITY_EXPANSION"))
    shock_bars = sum(v for (t, _), v in pairs.items() if t == "shock")
    assert shock_unsafe / shock_bars > 0.5


def test_regime_unsafe_on_wide_spread_and_unknown_on_short_history(synth):
    candles, _ = synth
    eng = RegimeEngine(CFG5().engines.regime)
    book = synthetic_book(float(candles.close[-1]), tick=float(candles.close[-1]) * 0.002)  # 40bps spread
    ctx = MarketContext(candles.symbol, candles, micro=micro_stats(book, []))
    st = eng.state(ctx)
    assert st.regime is Regime.UNSAFE_MARKET and st.permitted_engines == []
    st2 = eng.state(MarketContext(candles.symbol, candles.slice(0, 100)))
    assert st2.regime is Regime.UNKNOWN and st2.risk_multiplier == 0


def test_stat_arb_requires_cointegration_not_just_correlation():
    cfg = StatArbConfig(enabled=True, min_correlation=0.0, lookback_bars=3000)
    y, x = cointegrated_pair(3000)
    st = analyze_pair(y, x, cfg)
    assert st.tradeable, st.reasons
    assert st.beta == pytest.approx(1.5, rel=0.15)
    a, b = random_walk_pair(3000)
    st2 = analyze_pair(a, b, cfg)
    assert not st2.tradeable
    assert any("cointegration" in r or "half-life" in r for r in st2.reasons)


def test_stat_arb_signal_direction_and_legs():
    cfg = StatArbConfig(enabled=True, min_correlation=0.0, lookback_bars=3000, min_confidence=0)
    y, x = cointegrated_pair(3000)
    eng = StatArbEngine(cfg)
    st = analyze_pair(y, x, cfg)
    st.z = 2.5
    sig = eng.evaluate_pair(st)
    assert sig.direction is Direction.SHORT
    legs = {l["symbol"]: l["direction"] for l in sig.pair["legs"]}
    assert legs == {y.symbol: "SHORT", x.symbol: "LONG"}
    st.z = 4.0
    assert eng.evaluate_pair(st).state == "SPREAD_BROKEN"


def test_order_flow_assess_reflects_book_and_flow(synth):
    candles, _ = synth
    eng = build_engines(CFG5())["order_flow"]
    mid = float(candles.close[-1])
    trades = [{"price": mid, "qty": 1.0, "ts": k, "buyer_maker": k % 5 == 0} for k in range(200)]
    ctx = MarketContext(candles.symbol, candles, micro=micro_stats(synthetic_book(mid, imbalance=0.6), trades))
    f = eng.features(ctx)
    sup_long, opp_long, feats = eng.assess(ctx, f, len(candles) - 1, Direction.LONG)
    sup_short, _, _ = eng.assess(ctx, f, len(candles) - 1, Direction.SHORT)
    assert feats["book_imbalance"] > 0.3 and feats["aggr_imbalance"] > 0.3
    assert sup_long > sup_short
    assert opp_long == pytest.approx(sup_short)


def test_engine_cooldown_and_disable(synth):
    candles, _ = synth
    cfg = CFG5()
    cfg.engines.trend.min_confidence = 0
    eng = build_engines(cfg)["trend"]
    sigs = [s for _, s in _signals(eng, candles) if s.direction is not Direction.FLAT]
    assert sigs
    s = sigs[0]
    ctx = MarketContext(candles.symbol, candles)
    first = eng.finalize(ctx, s)
    assert first.direction is not Direction.FLAT
    import copy
    again = eng.finalize(ctx, copy.deepcopy(s))
    assert again.direction is Direction.FLAT and any("cooldown" in r for r in again.reasons)
    eng.cfg.enabled = False
    eng.reset_state()
    off = eng.finalize(ctx, copy.deepcopy(s))
    assert off.direction is Direction.FLAT and any("disabled" in r for r in off.reasons)
