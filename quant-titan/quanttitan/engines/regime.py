"""ENGINE 6 — Market Regime & Volatility Intelligence.

Classifies each symbol's market environment from measurable quantities and decides which engines
may operate. It controls *eligibility* and *risk scaling*; it does not predict profit.

Measured inputs (5m execution timeframe unless noted)
  ADX(14), +DI/-DI          trend strength and direction
  Efficiency ratio ER(20)   net move / path length (trendiness vs noise)
  EMA(50) slope / ATR       direction of drift
  1h EMA50 vs EMA200        higher-timeframe structure
  RV_short / RV_long        realized-vol ratio (1 hour vs 1 day of bars) -> expansion / compression
  ATR% percentile           volatility level relative to the last ``vol_lookback`` bars
  BB-width percentile       range compression
  max |return| / sigma      jump detection over the last ``jump_lookback`` bars
  spread, funding, data age from live context when available

Classification is an ordered rule list (first match wins) so every label has a precise, logged
reason. Secondary conditions are kept as ``flags`` (e.g. a STRONG_TREND that is also HIGH_VOL).
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..config import RegimeConfig
from ..core.types import Direction, EngineSignal, Regime, RegimeState
from ..data.candles import DAY_MS, HOUR_MS, bars_in
from .base import Engine, MarketContext, fval


class RegimeEngine(Engine):
    name = "regime"
    title = "Market Regime & Volatility Intelligence"
    description = "Classifies trend/range/volatility/safety and gates which engines may trade."

    def __init__(self, cfg: RegimeConfig) -> None:
        super().__init__(cfg)
        self.last_states: dict[str, RegimeState] = {}

    def min_bars(self) -> int:
        return self.cfg.min_bars

    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        c, cfg = ctx.candles, self.cfg
        adx, pdi, mdi = ind.adx(c.high, c.low, c.close, cfg.adx_period)
        atr = ind.atr(c.high, c.low, c.close, 14)
        ema50 = ind.ema(c.close, 50)
        h1 = ctx.htf("1h")
        h1_fast = ctx.htf_aligned("1h", ind.ema(h1.close, 50)) if len(h1) else np.full(len(c), np.nan)
        h1_slow = ctx.htf_aligned("1h", ind.ema(h1.close, 200)) if len(h1) else np.full(len(c), np.nan)
        r = ind.log_returns(c.close)
        rv_s = ind.rolling_std(r, bars_in(c.timeframe, HOUR_MS), ddof=1)
        rv_l = ind.rolling_std(r, bars_in(c.timeframe, DAY_MS), ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = atr / c.close
            jump = np.abs(r) / ind.shift(rv_l, 1)
            slope = (ema50 - ind.shift(ema50, 6)) / atr
        lb = cfg.vol_lookback
        return {
            "adx": adx, "pdi": pdi, "mdi": mdi, "atr": atr,
            "er": ind.efficiency_ratio(c.close, cfg.er_period),
            "slope": slope,
            "h1_trend": np.sign(h1_fast - h1_slow),
            "vol_ratio": rv_s / rv_l,
            "atr_pct_rank": ind.rolling_percentile_rank(atr_pct, lb),
            "bbw_rank": ind.rolling_percentile_rank(ind.bollinger(c.close, 20, 2.0)[3], lb),
            "jump_max": ind.rolling_max(np.nan_to_num(jump, nan=0.0), cfg.jump_lookback),
        }

    def classify(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> RegimeState:
        cfg = self.cfg
        m = {k: fval(f, k, i) for k in ("adx", "pdi", "mdi", "er", "slope", "h1_trend", "vol_ratio",
                                         "atr_pct_rank", "bbw_rank", "jump_max")}
        spread = ctx.micro.spread_bps if ctx.micro is not None else float("nan")
        m["spread_bps"] = spread
        m["funding"] = ctx.funding_rate
        m["data_age_s"] = ctx.data_age_s
        ts = int(ctx.candles.close_ts[i]) if len(ctx.candles) else 0
        reasons: list[str] = []
        flags: list[str] = []

        trend_dir = Direction.FLAT
        if np.isfinite(m["pdi"]) and np.isfinite(m["mdi"]):
            if m["pdi"] > m["mdi"] and (m["slope"] > 0 or not np.isfinite(m["slope"])):
                trend_dir = Direction.LONG
            elif m["mdi"] > m["pdi"] and (m["slope"] < 0 or not np.isfinite(m["slope"])):
                trend_dir = Direction.SHORT

        def done(reg: Regime, mult: float = 1.0) -> RegimeState:
            permitted = list(cfg.eligibility.get(reg.value, []))
            if "HIGH_VOLATILITY" in flags and reg is not Regime.HIGH_VOLATILITY:
                mult = min(mult, cfg.high_vol_risk_mult)
            st = RegimeState(ctx.symbol, reg, flags, trend_dir, permitted, round(mult, 3),
                             {k: (round(v, 4) if np.isfinite(v) else None) for k, v in m.items()}, reasons, ts)
            return st

        # 1. data sufficiency
        if i + 1 < cfg.min_bars or not np.isfinite(m["adx"]) or not np.isfinite(m["atr_pct_rank"]):
            reasons.append(f"insufficient history ({i + 1} bars; need {cfg.min_bars})")
            return done(Regime.UNKNOWN, 0.0)
        # 2. operational safety
        if ctx.data_age_s > 0 and ctx.data_age_s > 3 * ctx.candles.tf_ms / 1000:
            reasons.append(f"market data stale ({ctx.data_age_s:.0f}s)")
            return done(Regime.UNSAFE_MARKET, 0.0)
        if np.isfinite(spread) and spread > cfg.max_spread_bps:
            reasons.append(f"spread {spread:.1f}bps > {cfg.max_spread_bps}bps")
            return done(Regime.UNSAFE_MARKET, 0.0)
        if abs(ctx.funding_rate) > cfg.unsafe_funding:
            reasons.append(f"funding {ctx.funding_rate:.4%} beyond ±{cfg.unsafe_funding:.2%}")
            return done(Regime.UNSAFE_MARKET, 0.0)
        # 3. dislocation: extreme vol percentile together with a jump
        if m["atr_pct_rank"] >= cfg.dislocation_vol_pct and m["jump_max"] >= cfg.jump_sigma:
            reasons.append(f"ATR% at {m['atr_pct_rank']:.0f}th pct and {m['jump_max']:.1f}σ jump")
            return done(Regime.MARKET_DISLOCATION, 0.0)
        if m["jump_max"] >= cfg.jump_sigma * 1.5:
            reasons.append(f"{m['jump_max']:.1f}σ single-bar jump in last {cfg.jump_lookback} bars")
            return done(Regime.MARKET_DISLOCATION, 0.0)

        if m["atr_pct_rank"] >= cfg.high_vol_pct:
            flags.append("HIGH_VOLATILITY")
        if m["atr_pct_rank"] <= cfg.low_vol_pct:
            flags.append("LOW_VOLATILITY")
        if m["bbw_rank"] <= 20:
            flags.append("COMPRESSED_RANGE")
        if np.isfinite(m["vol_ratio"]) and m["vol_ratio"] >= cfg.expansion_ratio:
            flags.append("VOL_EXPANDING")
        h1_dir = Direction.LONG if m["h1_trend"] > 0 else Direction.SHORT if m["h1_trend"] < 0 else Direction.FLAT
        if h1_dir is not Direction.FLAT and h1_dir is trend_dir:
            flags.append("HTF_ALIGNED")

        # 4. volatility states that override structure
        if np.isfinite(m["vol_ratio"]) and m["vol_ratio"] >= cfg.expansion_ratio:
            reasons.append(f"short/long realized vol {m['vol_ratio']:.2f} ≥ {cfg.expansion_ratio}")
            return done(Regime.VOLATILITY_EXPANSION, cfg.expansion_risk_mult)
        if m["atr_pct_rank"] >= cfg.high_vol_pct and m["adx"] < cfg.strong_adx:
            reasons.append(f"ATR% at {m['atr_pct_rank']:.0f}th percentile without strong trend")
            return done(Regime.HIGH_VOLATILITY, cfg.high_vol_risk_mult)
        # 5. trend structure
        if m["adx"] >= cfg.strong_adx and m["er"] >= cfg.strong_er and trend_dir is not Direction.FLAT:
            reasons.append(f"ADX {m['adx']:.1f} ≥ {cfg.strong_adx}, ER {m['er']:.2f} ≥ {cfg.strong_er}, {trend_dir.value}")
            return done(Regime.STRONG_TREND)
        # 6. compression
        if m["bbw_rank"] <= 20 and np.isfinite(m["vol_ratio"]) and m["vol_ratio"] <= cfg.compression_ratio + 0.25:
            reasons.append(f"BB width at {m['bbw_rank']:.0f}th pct, vol ratio {m['vol_ratio']:.2f}")
            return done(Regime.VOLATILITY_COMPRESSION)
        if m["adx"] >= cfg.weak_adx and trend_dir is not Direction.FLAT:
            reasons.append(f"ADX {m['adx']:.1f} in [{cfg.weak_adx}, {cfg.strong_adx}) or ER {m['er']:.2f} weak, {trend_dir.value}")
            return done(Regime.WEAK_TREND)
        if m["atr_pct_rank"] <= cfg.low_vol_pct:
            reasons.append(f"ATR% at {m['atr_pct_rank']:.0f}th percentile")
            return done(Regime.LOW_VOLATILITY)
        reasons.append(f"ADX {m['adx']:.1f} < {cfg.weak_adx} or no direction; ER {m['er']:.2f}")
        return done(Regime.RANGE)

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        st = self.classify(ctx, f, i)
        self.last_states[ctx.symbol] = st
        sig = EngineSignal(self.name, ctx.symbol, Direction.FLAT, 0.0, setup="", state=st.regime.value,
                           role="filter", reasons=st.reasons,
                           features={k: v for k, v in st.metrics.items() if v is not None}, ts=st.ts)
        return sig

    def classify_series(self, ctx: MarketContext, f: dict[str, np.ndarray] | None = None) -> list[str]:
        """Label every bar (used by backtests for performance-by-regime and by tests)."""
        f = f or self.features(ctx)
        return [self.classify(ctx, f, i).regime.value for i in range(len(ctx.candles))]

    def state(self, ctx: MarketContext) -> RegimeState:
        if len(ctx.candles) < 2:
            return RegimeState(ctx.symbol, Regime.UNKNOWN, reasons=["no data"])
        f = self.features(ctx)
        st = self.classify(ctx, f, len(ctx.candles) - 1)
        self.last_states[ctx.symbol] = st
        self.stats.evaluations += 1
        self.stats.state_counts[st.regime.value] += 1
        return st
