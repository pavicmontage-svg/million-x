"""ENGINE 5 — Statistical Arbitrage & Relative Value.

Pair discovery is dynamic but sceptical: correlation is only a *pre-filter*. A pair is tradeable only
when, on log prices over ``lookback_bars``:
  1. rolling return correlation ≥ min_correlation,
  2. Engle–Granger cointegration p-value ≤ max_coint_pvalue (statsmodels ``coint``),
  3. the relationship is stable: cointegration holds in ≥ min_stable_windows of ``stability_windows``
     non-overlapping sub-windows and the hedge ratio drifts ≤ max_beta_drift across them,
  4. the OU half-life of the spread lies in [min_half_life_bars, max_half_life_bars].

Spread s_t = log Y_t − (α + β log X_t), z_t = (s_t − mean)/std over the fit window.
LONG spread (buy Y, sell β·X notional) when z ≤ −entry_z; SHORT spread when z ≥ entry_z. Exit at
|z| ≤ exit_z, stop at |z| ≥ stop_z, time stop after time_stop_half_lives half-lives.
Cost check: round-trip fees + slippage on both legs, expressed in z units, must leave at least
``min_edge_z`` of the (entry_z − exit_z) distance.
"""
from __future__ import annotations

import itertools
import warnings
from dataclasses import asdict, dataclass

import numpy as np

from .. import indicators as ind
from ..config import CostConfig, StatArbConfig
from ..core.types import Direction, EngineSignal
from ..data.candles import Candles, resample
from .base import Engine, MarketContext


@dataclass
class PairStats:
    y: str
    x: str
    n: int
    correlation: float
    alpha: float
    beta: float
    coint_pvalue: float
    adf_pvalue: float
    half_life: float
    stable_windows: int
    beta_drift: float
    spread_mean: float
    spread_std: float
    z: float
    tradeable: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _coint(ly: np.ndarray, lx: np.ndarray) -> float:
    from statsmodels.tsa.stattools import coint
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return float(coint(ly, lx, trend="c", autolag="AIC")[1])


def _adf(s: np.ndarray) -> float:
    from statsmodels.tsa.stattools import adfuller
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return float(adfuller(s, autolag="AIC")[1])


def analyze_pair(y: Candles, x: Candles, cfg: StatArbConfig) -> PairStats:
    """Statistical diagnostics for Y vs X using only the last ``lookback_bars`` common bars."""
    common, iy, ix = np.intersect1d(y.ts, x.ts, return_indices=True)
    reasons: list[str] = []
    n = min(len(common), cfg.lookback_bars)
    if n < 100:
        return PairStats(y.symbol, x.symbol, n, 0, 0, 0, 1, 1, np.inf, 0, np.inf, 0, 0, 0, False, ["insufficient overlap"])
    ly = np.log(y.close[iy][-n:])
    lx = np.log(x.close[ix][-n:])
    corr = float(np.corrcoef(np.diff(ly), np.diff(lx))[0, 1])
    a, b = ind.ols_beta(ly, lx)
    spread = ly - (a + b * lx)
    mu, sd = float(spread.mean()), float(spread.std())
    z = float((spread[-1] - mu) / sd) if sd > 0 else 0.0
    if corr < cfg.min_correlation:
        reasons.append(f"return correlation {corr:.2f} < {cfg.min_correlation}")
        return PairStats(y.symbol, x.symbol, n, corr, a, b, 1, 1, np.inf, 0, np.inf, mu, sd, z, False, reasons)
    p_coint = _coint(ly, lx)
    p_adf = _adf(spread)
    hl = ind.half_life(spread)
    # stability across sub-windows
    k = cfg.stability_windows
    size = n // k
    stable, betas = 0, []
    for w in range(k):
        sl = slice(w * size, (w + 1) * size)
        if size < 60:
            break
        _, bw = ind.ols_beta(ly[sl], lx[sl])
        betas.append(bw)
        if _coint(ly[sl], lx[sl]) <= max(cfg.max_coint_pvalue * 2, 0.1):
            stable += 1
    drift = float((max(betas) - min(betas)) / abs(b)) if betas and b != 0 else np.inf
    ok = True
    if p_coint > cfg.max_coint_pvalue:
        ok = False; reasons.append(f"Engle–Granger p={p_coint:.3f} > {cfg.max_coint_pvalue} — correlation without cointegration")
    if stable < cfg.min_stable_windows:
        ok = False; reasons.append(f"cointegration held in only {stable}/{k} sub-windows")
    if drift > cfg.max_beta_drift:
        ok = False; reasons.append(f"hedge ratio drift {drift:.0%} > {cfg.max_beta_drift:.0%} — structural instability")
    if not (cfg.min_half_life_bars <= hl <= cfg.max_half_life_bars):
        ok = False; reasons.append(f"half-life {hl:.1f} bars outside [{cfg.min_half_life_bars}, {cfg.max_half_life_bars}]")
    if b <= 0:
        ok = False; reasons.append("non-positive hedge ratio")
    if ok:
        reasons.append(f"cointegrated (p={p_coint:.3f}), half-life {hl:.1f} bars, β={b:.3f}, stable {stable}/{k}")
    return PairStats(y.symbol, x.symbol, n, corr, a, b, p_coint, p_adf, hl, stable, drift, mu, sd, z, ok, reasons)


class StatArbEngine(Engine):
    name = "stat_arb"
    title = "Statistical Arbitrage & Relative Value"
    description = "Cointegration-validated pairs; trades spread z-score mean reversion with its own risk bucket."

    def __init__(self, cfg: StatArbConfig, costs: CostConfig | None = None) -> None:
        super().__init__(cfg)
        self.costs = costs or CostConfig()
        self.pairs: dict[tuple[str, str], PairStats] = {}

    def min_bars(self) -> int:
        return 100

    def _tf(self, c: Candles) -> Candles:
        return c if c.timeframe == self.cfg.timeframe else resample(c, self.cfg.timeframe)

    def scan(self, universe: dict[str, Candles], max_pairs_tested: int = 45) -> list[PairStats]:
        """Test all pairs of the universe (bounded) and keep diagnostics for each."""
        data = {s: self._tf(c) for s, c in universe.items() if len(c) > 0}
        results: list[PairStats] = []
        for k, (a, b) in enumerate(itertools.combinations(sorted(data), 2)):
            if k >= max_pairs_tested:
                break
            st = analyze_pair(data[a], data[b], self.cfg)
            self.pairs[(a, b)] = st
            results.append(st)
        results.sort(key=lambda p: (not p.tradeable, p.coint_pvalue))
        self.stats.evaluations += 1
        return results

    def cost_z(self, st: PairStats) -> float:
        c = self.costs
        per_leg = 2 * (c.taker_fee + c.base_slippage_bps / 1e4)  # in + out, per unit notional
        cost_log = per_leg * (1 + abs(st.beta))
        return cost_log / st.spread_std if st.spread_std > 0 else np.inf

    def evaluate_pair(self, st: PairStats, ts: int = 0) -> EngineSignal:
        cfg = self.cfg
        feats = {"z": st.z, "beta": st.beta, "half_life": st.half_life, "coint_p": st.coint_pvalue,
                 "corr": st.correlation, "spread_std": st.spread_std}
        sym = f"{st.y}/{st.x}"
        if not st.tradeable:
            return EngineSignal(self.name, sym, Direction.FLAT, 0.0, state="NOT_COINTEGRATED", reasons=st.reasons,
                                features=feats, ts=ts)
        cz = self.cost_z(st)
        feats["cost_z"] = cz
        if (cfg.entry_z - cfg.exit_z) - cz < cfg.min_edge_z:
            return EngineSignal(self.name, sym, Direction.FLAT, 0.0, state="COSTS_TOO_HIGH",
                                reasons=[f"expected reversion {cfg.entry_z - cfg.exit_z:.2f}z minus costs {cz:.2f}z < {cfg.min_edge_z}z"],
                                features=feats, ts=ts)
        if abs(st.z) < cfg.entry_z:
            return EngineSignal(self.name, sym, Direction.FLAT, 0.0, state="SPREAD_NORMAL",
                                reasons=[f"|z| {abs(st.z):.2f} < {cfg.entry_z}"], features=feats, ts=ts)
        if abs(st.z) >= cfg.stop_z:
            return EngineSignal(self.name, sym, Direction.FLAT, 0.0, state="SPREAD_BROKEN",
                                reasons=[f"|z| {abs(st.z):.2f} ≥ stop {cfg.stop_z} — relationship may be breaking"],
                                features=feats, ts=ts)
        d = Direction.SHORT if st.z > 0 else Direction.LONG  # direction of the *spread* (Y − βX)
        conf = float(np.clip(50 + 10 * (abs(st.z) - cfg.entry_z) + 20 * (cfg.max_coint_pvalue - st.coint_pvalue) / cfg.max_coint_pvalue
                             + 10 * min(st.stable_windows / max(cfg.stability_windows, 1), 1), 0, 100))
        legs = [{"symbol": st.y, "direction": d.value, "weight": 1.0},
                {"symbol": st.x, "direction": d.opposite().value, "weight": abs(st.beta)}]
        return EngineSignal(self.name, sym, d, round(conf, 1), setup="PAIR_REVERSION", state="SPREAD_EXTENDED",
                            reasons=[f"spread z {st.z:+.2f} beyond ±{cfg.entry_z}", *st.reasons],
                            features=feats, ts=ts, role="primary",
                            pair={"y": st.y, "x": st.x, "beta": st.beta, "alpha": st.alpha, "mean": st.spread_mean,
                                  "std": st.spread_std, "z": st.z, "entry_z": cfg.entry_z, "exit_z": cfg.exit_z,
                                  "stop_z": cfg.stop_z, "half_life": st.half_life,
                                  "time_stop_bars": int(cfg.time_stop_half_lives * st.half_life), "legs": legs})

    # single-symbol interface (not used for pairs; keeps the Engine contract)
    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        return {}

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        return self.flat(ctx, i, "PAIRS_ONLY", ["stat-arb evaluates pairs via scan()/evaluate_pair()"])
