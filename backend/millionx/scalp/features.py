"""1-second scalping features (2.2). Pure numpy, causal, identical for live and backtest.

Input: contiguous 1 s arrays (one element per second, empty seconds carry the previous close with
zero volume). Every feature at index i uses only seconds <= i. Higher timeframes (1m, 3m, 5m, 15m) are
built from the same seconds and read from the last CLOSED bar only. 60m is not used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

REGIMES = ("TRENDING", "RANGING", "HIGH_VOL", "LOW_VOL", "MOMENTUM_EXPANSION", "LIQUIDITY_SWEEP", "DISLOCATION")


@dataclass
class Sec:
    t: np.ndarray        # second start, ms
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    buy: np.ndarray      # aggressive buy qty
    sell: np.ndarray     # aggressive sell qty
    n: np.ndarray        # trades

    def __len__(self) -> int:
        return len(self.t)

    def slice(self, a: int, b: int) -> "Sec":
        return Sec(*(x[a:b] for x in (self.t, self.o, self.h, self.l, self.c, self.buy, self.sell, self.n)))

    @staticmethod
    def concat(parts: list["Sec"]) -> "Sec":
        return Sec(*(np.concatenate([getattr(p, k) for p in parts]) for k in ("t", "o", "h", "l", "c", "buy", "sell", "n")))


def from_trades(day_start_ms: int, t_ms: np.ndarray, price: np.ndarray, qty: np.ndarray, buyer_maker: np.ndarray,
                prev_close: float = np.nan, seconds: int = 86_400) -> Sec:
    """1 s bars of one day from aggregated trades. buyer_maker=True means an aggressive SELL."""
    s = ((t_ms - day_start_ms) // 1000).astype(np.int64)
    ok = (s >= 0) & (s < seconds)
    s, price, qty, bm = s[ok], price[ok], qty[ok], buyer_maker[ok]
    order = np.argsort(s, kind="stable")  # trades are time ordered; stable keeps intra-second order
    s, price, qty, bm = s[order], price[order], qty[order], bm[order]
    o = np.full(seconds, np.nan)
    h = np.full(seconds, -np.inf)
    lo = np.full(seconds, np.inf)
    c = np.full(seconds, np.nan)
    buy = np.zeros(seconds)
    sell = np.zeros(seconds)
    n = np.zeros(seconds)
    if len(s):
        first = np.r_[True, s[1:] != s[:-1]]
        last = np.r_[s[1:] != s[:-1], True]
        o[s[first]] = price[first]
        c[s[last]] = price[last]
        np.maximum.at(h, s, price)
        np.minimum.at(lo, s, price)
        np.add.at(buy, s[~bm], qty[~bm])
        np.add.at(sell, s[bm], qty[bm])
        np.add.at(n, s, 1)
    # empty seconds: carry previous close (no trade = no price change, zero volume)
    idx = np.where(np.isfinite(c), np.arange(seconds), -1)
    np.maximum.accumulate(idx, out=idx)
    filled = np.where(idx >= 0, c[np.maximum(idx, 0)], prev_close)
    empty = ~np.isfinite(o)
    o = np.where(empty, filled, o)
    h = np.where(np.isfinite(h) & ~empty, h, filled)
    lo = np.where(np.isfinite(lo) & ~empty, lo, filled)
    t = day_start_ms + np.arange(seconds, dtype=np.int64) * 1000
    return Sec(t, o, h, lo, filled, buy, sell, n)


def _rsum(x: np.ndarray, n: int) -> np.ndarray:
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))])
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = c[n:] - c[:-n]
    return out


def _lag(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = x[:-n]
    return out


def _rmax_prev(x: np.ndarray, n: int) -> np.ndarray:
    """max of the n values BEFORE i (excludes i)."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).max(axis=1)[:-1]
    return out


def _rmin_prev(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).min(axis=1)[:-1]
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2 / (n + 1)
    out = np.empty(len(x))
    m = x[0]
    for i, v in enumerate(x):
        m = a * v + (1 - a) * m
        out[i] = m
    return out


def _closed_tf(c: np.ndarray, t: np.ndarray, sec: int) -> tuple[np.ndarray, np.ndarray]:
    """Closes of CLOSED `sec`-second bars and, per second, the index of the last closed one (-1 if none)."""
    k = (t // 1000) // sec                       # bar id of each second
    last_in_bar = np.r_[k[1:] != k[:-1], False]  # last second of a bar is known closed only after it ends
    closes = c[last_in_bar]
    close_sec = np.flatnonzero(last_in_bar)
    j = np.searchsorted(close_sec, np.arange(len(c)), side="left") - 1  # bars closed strictly before second i
    return closes, j


def compute(s: Sec) -> dict[str, np.ndarray]:
    c, h, lo = s.c, s.h, s.l
    vol = s.buy + s.sell
    f: dict[str, np.ndarray] = {}
    lr = np.log(c)
    r1 = np.r_[np.nan, np.diff(lr)] * 1e4                      # 1 s return, bps
    f["sigma300"] = np.sqrt(_rsum(r1 ** 2, 300) / 300)          # 1 s volatility, bps
    sig = np.maximum(f["sigma300"], 0.05)
    for n in (10, 30):
        f[f"v{n}"] = (lr - _lag(lr, n)) * 1e4                   # velocity, bps over n s
        f[f"v{n}z"] = f[f"v{n}"] / (sig * np.sqrt(n))
    f["acc10"] = f["v10"] - _lag(f["v10"], 10)                  # momentum acceleration
    v30 = _rsum(vol, 30)
    f["relvol30"] = v30 / np.maximum(1e-12, _rsum(vol, 1800) / 60)
    f["taker30"] = _rsum(s.buy, 30) / np.maximum(1e-12, v30)
    d10 = _rsum(s.buy - s.sell, 10) / np.maximum(1e-12, _rsum(vol, 10))
    f["cvd10"] = d10
    f["cvd_acc"] = d10 - _lag(d10, 10)
    f["hi180"], f["lo180"] = _rmax_prev(h, 180), _rmin_prev(lo, 180)  # 3m range before now
    f["hi60"], f["lo60"] = _rmax_prev(h, 60), _rmin_prev(lo, 60)
    f["break_up"] = (c - f["hi180"]) / c * 1e4                 # breakout strength, bps
    f["break_dn"] = (f["lo180"] - c) / c * 1e4
    lo300, hi300 = _rmin_prev(lo, 300), _rmax_prev(h, 300)
    # liquidity sweep: in the last 30 s price traded beyond the 3m extreme that stood 30 s ago, and is back inside
    ref_hi, ref_lo = _lag(f["hi180"], 30), _lag(f["lo180"], 30)
    took_hi = np.fmax(_rmax_prev(h, 30), h)
    took_lo = np.fmin(_rmin_prev(lo, 30), lo)
    f["sweep_up"] = np.nan_to_num((took_hi > ref_hi) & (c < ref_hi), nan=0).astype(bool)   # buy-side swept, back below
    f["sweep_dn"] = np.nan_to_num((took_lo < ref_lo) & (c > ref_lo), nan=0).astype(bool)
    f["sweep"] = f["sweep_up"] | f["sweep_dn"]
    f["ext_up"] = (c - lo300) / c * 1e4 / (sig * np.sqrt(300))  # exhaustion: move from the 5m low in sigmas
    f["ext_dn"] = (hi300 - c) / c * 1e4 / (sig * np.sqrt(300))
    # 1m EMA 9/21 and 3m / 5m / 15m context from CLOSED bars
    c1, j1 = _closed_tf(c, s.t, 60)
    if len(c1) >= 2:
        e9, e21 = _ema(c1, 9), _ema(c1, 21)
        tr1 = np.abs(np.diff(np.r_[c1[0], c1]))
        atr1 = _rsum(tr1, 14) / 14
        ok = j1 >= 0
        for name, arr in (("ema9_1m", e9), ("ema21_1m", e21), ("atr1m", atr1)):
            f[name] = np.where(ok, arr[np.maximum(j1, 0)], np.nan)
        e21_lag5 = np.r_[np.full(5, np.nan), e21[:-5]] if len(e21) > 5 else np.full(len(e21), np.nan)
        f["trend5"] = np.where(ok, np.sign(e21 - e21_lag5)[np.maximum(j1, 0)], 0.0)   # 5m short-term trend
    else:
        for name in ("ema9_1m", "ema21_1m", "atr1m"):
            f[name] = np.full(len(c), np.nan)
        f["trend5"] = np.zeros(len(c))
    c3, j3 = _closed_tf(c, s.t, 180)
    if len(c3) >= 2:
        m3 = np.r_[np.nan, np.diff(np.log(c3))] * 1e4
        f["mom3m"] = np.where(j3 >= 0, m3[np.maximum(j3, 0)], np.nan)                 # 3m momentum confirmation
    else:
        f["mom3m"] = np.full(len(c), np.nan)
    f["ret15m"] = (lr - _lag(lr, 900)) * 1e4                    # 15m context (not a blocker)
    pv = _rsum(c * vol, 14_400)
    f["vwap4h"] = pv / np.maximum(1e-12, _rsum(vol, 14_400))
    f["vol_contract"] = _rsum(vol, 120) / np.maximum(1e-12, _rsum(vol, 600) / 5)       # pullback volume vs recent
    f["regime"] = regime(f)
    return f


def regime(f: dict[str, np.ndarray]) -> np.ndarray:
    """One label per second (priority order: dislocation, sweep, expansion, trend, vol, range)."""
    n = len(f["v10"])
    lab = np.full(n, "RANGING", dtype=object)
    sig = np.maximum(f["sigma300"], 0.05)
    s15 = sig * np.sqrt(900)
    vol_q = np.nan_to_num(f["sigma300"])
    ref = _rsum(vol_q, 14_400) / 14_400          # causal reference: mean 1 s volatility of the last 4 h
    ok = np.isfinite(ref) & (ref > 0)
    lab[ok & (vol_q < 0.6 * np.nan_to_num(ref))] = "LOW_VOL"
    lab[ok & (vol_q > 1.8 * np.nan_to_num(ref))] = "HIGH_VOL"
    lab[np.abs(np.nan_to_num(f["ret15m"])) > 2.0 * s15] = "TRENDING"
    lab[(np.nan_to_num(f["relvol30"]) > 3) & (np.abs(np.nan_to_num(f["v30z"])) > 3)] = "MOMENTUM_EXPANSION"
    lab[f["sweep"]] = "LIQUIDITY_SWEEP"
    lab[np.abs(np.nan_to_num(f["v10z"])) > 8] = "DISLOCATION"
    return lab
