"""2.2 scalping core: 1 s bars, causal features, engines, exits (large-R/R runners), maker fills, ranking."""
import numpy as np

from millionx.scalp import engines as E
from millionx.scalp import exits as X
from millionx.scalp import features as F
from millionx.scalp import opportunity as O

DAY0 = 1_735_689_600_000  # 2025-01-01


def _trades(seconds=7200, seed=0, impulse_at=None):
    rng = np.random.default_rng(seed)
    n = seconds * 3
    t = DAY0 + np.sort(rng.integers(0, seconds * 1000, n))
    steps = rng.normal(0, 0.5, n)
    if impulse_at is not None:
        k = np.searchsorted(t, DAY0 + impulse_at * 1000)
        steps[k:k + 120] += 4.0  # strong buying impulse
    price = 100_000 + np.cumsum(steps)
    qty = rng.exponential(0.05, n)
    bm = rng.random(n) < 0.5
    if impulse_at is not None:
        bm[k:k + 120] = rng.random(120) < 0.1
        qty[k:k + 120] *= 6
    return t, price, qty, bm


def test_from_trades_bars():
    t = np.array([DAY0 + 100, DAY0 + 900, DAY0 + 3_500])
    s = F.from_trades(DAY0, t, np.array([10.0, 11.0, 9.0]), np.array([1.0, 2.0, 3.0]), np.array([False, True, False]),
                      seconds=5)
    assert list(s.o[:4]) == [10.0, 10.0, 10.0, 11.0][:1] + [11.0, 11.0, 11.0][:0] + list(s.o[1:4])
    assert s.c[0] == 11.0 and s.h[0] == 11.0 and s.l[0] == 10.0
    assert s.buy[0] == 1.0 and s.sell[0] == 2.0 and s.n[0] == 2
    assert s.c[1] == 11.0 and s.buy[1] == 0 and s.c[3] == 9.0 and s.c[4] == 9.0, "empty seconds carry the close"


def test_features_causal():
    t, p, q, bm = _trades(5000, seed=1)
    s = F.from_trades(DAY0, t, p, q, bm, seconds=5000)
    full = F.compute(s)
    part = F.compute(s.slice(0, 3500))
    for k in ("v30z", "relvol30", "taker30", "cvd_acc", "break_up", "ext_up", "ema21_1m", "mom3m", "atr1m", "sigma300"):
        a, b = np.nan_to_num(full[k][:3500], nan=-7), np.nan_to_num(part[k], nan=-7)
        assert np.allclose(a, b), f"{k} uses future seconds"
    assert set(np.unique(full["regime"])) <= set(F.REGIMES)


def test_engine_a_finds_impulse_and_b_runs():
    t, p, q, bm = _trades(7200, seed=2, impulse_at=5000)
    s = F.from_trades(DAY0, t, p, q, bm, seconds=7200)
    f = F.compute(s)
    a = E.engine_a(f, s.c, E.GRID["A"][0])
    assert any(4990 <= i <= 5200 and side == 1 for i, side, _, _ in a), a[:5]
    assert all(st > 0 for _, _, st, _ in a)
    E.engine_b(f, s.c, E.GRID["B"][0])  # runs without error on the same data


def test_engine_c_incomplete_without_book():
    assert E.engine_c(None, {"buy": 1, "sell": 0})["status"] == "INCOMPLETE"
    old = {"age_ms": 9_000, "imb10": 0.5}
    assert E.engine_c(old, {"buy": 1, "sell": 0})["status"] == "INCOMPLETE"
    ok = {"age_ms": 300, "imb10": 0.5, "removed_ask10": 2.0, "removed_bid10": 0.0, "spread_bps": 0.5}
    assert E.engine_c(ok, {"buy": 9, "sell": 1, "move_bps": 2.0})["side"] == 1
    assert E.engine_c(ok, {"buy": 9, "sell": 1, "move_bps": 0.0})["side"] == 0, "absorbed aggression is not entered"


def _path(moves, start=100.0):
    c = start + np.r_[0.0, np.cumsum(moves)]
    return c, c + 0.001, c - 0.001


def _sim(c, h, lo, side=1, stop=10.0, rr=3.0, cost=X.Costs("T", latency_s=0), outage=None):
    z = np.zeros(len(c))
    return X.simulate(0, side, stop, rr, c, h, lo, z, z, cost, outage)


def test_exit_stop_tp_and_runner():
    c, h, lo = _path(np.r_[np.full(50, -0.003), np.zeros(100)])      # straight down: stop at -10 bps
    r = _sim(c, h, lo)
    assert r["reason"] == "stop" and abs(r["gross_bps"] + 10) < 0.5
    c, h, lo = _path(np.r_[np.full(40, 0.01), np.zeros(10)])          # +40 bps quickly: TP at 3R = +30 bps
    r = _sim(c, h, lo)
    assert r["reason"] == "take_profit" and abs(r["gross_bps"] - 30) < 0.5 and abs(r["r"] - 1.7) < 0.01, "net R after 13 bps costs"
    # trailing: rises 40 bps (4R, target 5R not reached), then falls back -> exits on the trail with profit
    c, h, lo = _path(np.r_[np.full(40, 0.01), np.full(60, -0.01)])
    r = _sim(c, h, lo, rr=5.0)
    assert r["reason"] == "trailing" and 25 < r["gross_bps"] < 31


def test_exit_break_even_max_hold_and_outage():
    c, h, lo = _path(np.r_[np.full(28, 0.01), np.full(60, -0.01)])    # +28 bps (> 2 x costs, < trail) then back
    r = _sim(c, h, lo, stop=20.0)
    assert r["reason"] == "break_even" and r["net_bps"] > -1.0
    c, h, lo = _path(np.zeros(3000))
    r = _sim(c, h, lo)
    assert r["reason"] == "max_hold" and r["hold_s"] == X.MAX_HOLD_S
    c, h, lo = _path(np.r_[np.full(50, -0.003), np.full(200, -0.003)])
    out = np.zeros(len(c), bool)
    out[5:120] = True
    r = _sim(c, h, lo, outage=out)
    assert r["reason"].endswith("_after_outage") and r["gross_bps"] < -10, "outage delays the exit; the loss is real"


def test_maker_fill_needs_trade_through():
    c = np.array([100.0, 100.0, 100.0, 99.9, 99.8])
    h, lo = c.copy(), c.copy()
    sell = np.array([0, 5.0, 5.0, 5.0, 5.0])
    buy = np.zeros(5)
    assert X.maker_fill(0, 1, c[:3], h[:3], lo[:3], buy[:3], sell[:3], 1.0, wait_s=2) is None, "touch is not a fill"
    k, share = X.maker_fill(0, 1, c, h, lo, buy, sell, 1.0, wait_s=4)
    assert k == 4 and share == 1.0


def test_ranking_prefers_money_not_win_rate():
    rows = []
    for i in range(200):   # engine X: 30 % winners at +5R, 70 % losers at -1R  -> +0.8R gross
        rows.append({"engine": "X", "cfg": 0, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0,
                     "gross_bps": 100.0 if i % 10 < 3 else -20.0, "hold_s": 300, "mfe_bps": 60})
    for i in range(200):   # engine Y: 80 % winners at +0.3R, 20 % losers at -1R -> +0.04R gross
        rows.append({"engine": "Y", "cfg": 0, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0,
                     "gross_bps": 6.0 if i % 10 < 8 else -20.0, "hold_s": 300, "mfe_bps": 8})
    cal = O.build_calibration(rows)
    cost = X.SCENARIOS["BASE"]
    x = O.rank({"engine": "X", "cfg": 0, "side": 1, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0, "rr": 5}, cal, cost, True)
    y = O.rank({"engine": "Y", "cfg": 0, "side": 1, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0, "rr": 1}, cal, cost, True)
    assert x["decision"] == "ACCEPT" and x["p_win"] < y["p_win"]
    assert y["decision"] == "REJECT" and x["expected_net_eur"] > 0
    z = O.rank({"engine": "X", "cfg": 0, "side": 1, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0, "rr": 5}, cal, cost, False)
    assert z["decision"] == "REJECT" and any("not validated" in r for r in z["reasons"])
    u = O.rank({"engine": "Q", "cfg": 0, "side": 1, "score": 1.0, "regime": "TRENDING", "stop_bps": 20.0, "rr": 5}, cal, cost, True)
    assert u["decision"] == "REJECT" and any("not calibrated" in r for r in u["reasons"])
