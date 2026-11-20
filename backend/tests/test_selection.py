"""Strategy Selection Engine: NO_TRADE fallback, shrinkage, eligibility."""
import numpy as np

from millionx.selection import DAY, NO_TRADE, SelectionConfig, decayed_stats, select, shrink


def _tr(strategy, n, mu, seed, symbol="BTCUSDT", regime="TREND_UP"):
    rng = np.random.default_rng(seed)
    return [{"strategy": strategy, "symbol": symbol, "regime": regime, "r": float(x), "closed_at": i * DAY // 3}
            for i, x in enumerate(rng.normal(mu, 1.0, n))]


def test_no_trade_when_nothing_has_edge():
    trades = _tr("a", 400, 0.0, 1) + _tr("b", 400, -0.05, 2)
    out = select(trades, {"a", "b"}, now_ms=400 * DAY // 3)
    assert out["cells"]["BTCUSDT|TREND_UP"]["selected"] == NO_TRADE


def test_selects_real_edge_but_only_if_validated():
    trades = _tr("good", 600, 0.35, 3) + _tr("flat", 600, 0.0, 4)
    now = 600 * DAY // 3
    assert select(trades, {"good", "flat"}, now)["cells"]["BTCUSDT|TREND_UP"]["selected"] == "good"
    assert select(trades, {"flat"}, now)["cells"]["BTCUSDT|TREND_UP"]["selected"] == NO_TRADE


def test_small_sample_is_rejected_and_shrinkage_pulls_to_prior():
    trades = _tr("lucky", 12, 1.0, 5)
    out = select(trades, {"lucky"}, now_ms=12 * DAY)
    c = out["cells"]["BTCUSDT|TREND_UP"]
    assert c["selected"] == NO_TRADE
    assert any("sample" in r for r in c["candidates"][0]["reasons"])
    assert shrink(1.0, 12, 0.0, 50) < 0.25


def test_decay_weights_recent_more():
    r = np.r_[np.full(100, -1.0), np.full(100, 1.0)]
    t = np.arange(200) * DAY
    m, _, n_eff = decayed_stats(r, t, 200 * DAY, half_life_days=20)
    assert m > 0.5 and n_eff < 200
