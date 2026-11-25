import numpy as np
import pytest

from quanttitan import indicators as ind
from quanttitan.data.candles import Candles, align_htf, check_integrity, resample
from quanttitan.data.synthetic import generate


@pytest.fixture(scope="module")
def candles():
    c, _ = generate(1500, seed=3)
    return c


def test_ema_matches_reference():
    x = np.arange(1, 51, dtype=float)
    out = ind.ema(x, 10)
    ref = [x[0]]
    a = 2 / 11
    for v in x[1:]:
        ref.append(a * v + (1 - a) * ref[-1])
    assert np.allclose(out, ref)


def test_atr_constant_range():
    n = 100
    h = np.full(n, 11.0); l = np.full(n, 9.0); c = np.full(n, 10.0)
    assert np.allclose(ind.atr(h, l, c, 14)[20:], 2.0)


def test_adx_strong_trend_high_and_flat_low():
    n = 300
    c = np.linspace(100, 200, n)
    a, pdi, mdi = ind.adx(c + 0.5, c - 0.5, c, 14)
    assert a[-1] > 50 and pdi[-1] > mdi[-1]
    rng = np.random.default_rng(0)
    c2 = 100 + rng.standard_normal(n) * 0.3  # iid noise around a level = range
    a2, _, _ = ind.adx(c2 + 0.5, c2 - 0.5, c2, 14)
    assert a2[-1] < 25


def test_session_vwap_formula_by_hand():
    # three bars in one UTC day; typical prices 10, 12, 14 with volumes 1, 2, 1
    ts = np.array([0, 300_000, 600_000])
    h = np.array([10.0, 12.0, 14.0]); l = h.copy(); c = h.copy(); v = np.array([1.0, 2.0, 1.0])
    vw, sd, n_in = ind.session_vwap(ts, h, l, c, v)
    assert vw[-1] == pytest.approx((10 + 24 + 14) / 4)
    var = (1 * (10 - 12) ** 2 + 2 * 0 + 1 * (14 - 12) ** 2) / 4
    assert sd[-1] == pytest.approx(np.sqrt(var))
    assert vw[0] == 10 and sd[0] == 0 and n_in[-1] == 3


def test_session_vwap_resets_at_utc_midnight():
    day = 86_400_000
    ts = np.array([day - 300_000, day])
    p = np.array([10.0, 20.0])
    vw, sd, n_in = ind.session_vwap(ts, p, p, p, np.array([1.0, 1.0]))
    assert vw[1] == 20 and n_in[1] == 1 and sd[1] == 0


def test_rolling_vwap_window():
    ts = np.arange(5) * 300_000
    p = np.array([1.0, 2, 3, 4, 5])
    vw, _, _ = ind.session_vwap(ts, p, p, p, np.ones(5), anchor="rolling", rolling_bars=2)
    assert vw[-1] == pytest.approx(4.5)


def test_half_life_of_ou_process():
    rng = np.random.default_rng(1)
    theta = 0.1
    s = np.zeros(5000)
    for i in range(1, len(s)):
        s[i] = s[i - 1] * (1 - theta) + rng.standard_normal()
    hl = ind.half_life(s)
    assert hl == pytest.approx(np.log(2) / -np.log(1 - theta), rel=0.25)


CAUSAL_FUNCS = {
    "ema": lambda c: ind.ema(c.close, 20),
    "atr": lambda c: ind.atr(c.high, c.low, c.close, 14),
    "adx": lambda c: ind.adx(c.high, c.low, c.close, 14)[0],
    "rsi": lambda c: ind.rsi(c.close, 14),
    "bbw": lambda c: ind.bollinger(c.close, 20, 2)[3],
    "pct": lambda c: ind.rolling_percentile_rank(ind.bollinger(c.close, 20, 2)[3], 200),
    "er": lambda c: ind.efficiency_ratio(c.close, 20),
    "rv": lambda c: ind.relative_volume(c.volume, 20),
    "vwap": lambda c: ind.session_vwap(c.ts, c.high, c.low, c.close, c.volume)[0],
    "vwap_sd": lambda c: ind.session_vwap(c.ts, c.high, c.low, c.close, c.volume)[1],
    "z": lambda c: ind.zscore(c.close, 50),
}


@pytest.mark.parametrize("name", list(CAUSAL_FUNCS))
def test_causality(candles, name):
    """f(full)[t] must equal f(truncated at t)[t] — no future data used."""
    f = CAUSAL_FUNCS[name]
    full = f(candles)
    for t in (300, 777, 1200, len(candles) - 1):
        part = f(candles.slice(0, t + 1))
        a, b = full[t], part[t]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, rel=1e-9, abs=1e-12), (name, t, a, b)


def test_resample_and_alignment_no_lookahead(candles):
    h1 = resample(candles, "1h")
    assert len(h1) > 0
    # each 1h bar aggregates 12 5m bars
    first = int(np.searchsorted(candles.ts, h1.ts[0]))
    assert h1.high[0] == pytest.approx(candles.high[first:first + 12].max())
    assert h1.volume[0] == pytest.approx(candles.volume[first:first + 12].sum())
    aligned = align_htf(candles, h1, h1.close)
    # the value visible at a 5m bar must come from an hour that closed at or before that bar's close
    for i in range(0, len(candles), 37):
        if np.isnan(aligned[i]):
            continue
        k = int(np.where(h1.close == aligned[i])[0][0])
        assert h1.close_ts[k] <= candles.close_ts[i]


def test_resample_drops_incomplete(candles):
    part = candles.slice(0, 13)  # 1 complete hour only if aligned
    h1 = resample(part, "1h")
    for t in h1.ts:
        assert ((part.ts >= t) & (part.ts < t + 3_600_000)).sum() == 12


def test_integrity_detects_gap(candles):
    assert check_integrity(candles).ok
    broken = Candles(candles.symbol, candles.timeframe, *(np.delete(getattr(candles, f), 50) for f in
                     ("ts", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy")))
    rep = check_integrity(broken)
    assert not rep.ok and rep.gaps == 1


def test_append_dedup(candles):
    a, b = candles.slice(0, 100), candles.slice(50, 150)
    m = a.append(b)
    assert len(m) == 150 and np.all(np.diff(m.ts) > 0)
