"""Regime Intelligence 2.0: causality (no look-ahead) and label coverage."""
import numpy as np

from millionx.backtest.data import synthetic_bars
from millionx.regime2 import REGIMES, classify, rolling_percentile


def test_rolling_percentile_is_causal():
    x = np.random.default_rng(1).normal(size=500)
    full = rolling_percentile(x, 100, min_count=10)
    part = rolling_percentile(x[:300], 100, min_count=10)
    assert np.allclose(full[:300], part, equal_nan=True)


def test_regime_has_no_look_ahead_and_uses_closed_bars_only():
    b = synthetic_bars(30_000, seed=4)
    btc = synthetic_bars(30_000, seed=9)
    full = classify(b, btc)
    cut = 20_000 + 7  # in the middle of a 15-minute candle
    part = classify(b.slice(0, cut), btc.slice(0, cut))
    k = len(part.label)
    assert (full.label[:k] == part.label).all(), "labels of closed bars must not change with future data"
    for name in ("adx", "atr_pctile", "vwap_z", "structure", "ctx_1h_slope"):
        assert np.allclose(full.features[name][:k], part.features[name], equal_nan=True), name
    # the 15m bar containing minute `cut` is not closed yet -> regime comes from the previous bar
    lab, f = part.at_1m(cut - 1)
    assert lab == part.label[-1]
    assert set(full.label) <= set(REGIMES)
