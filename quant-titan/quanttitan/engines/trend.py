"""ENGINE 2 — Trend & Momentum.

Structure: EMA(fast) / EMA(mid) / EMA(slow) stack on 5m plus 1h EMA(50)/EMA(200) alignment.
Strength: ADX with +DI/−DI. Momentum: 12-bar rate of change and its 6-bar acceleration.

Market states: STRONG_BULL / STRONG_BEAR, WEAK_BULL / WEAK_BEAR, TREND_REVERSAL, TREND_EXHAUSTION,
MOMENTUM_ACCELERATION, NO_TREND.

Setups (LONG; SHORT mirrors):
  PULLBACK      trend aligned; within the last 5 bars a low dipped into the EMA(fast)…EMA(mid) zone
                (± pullback_atr·ATR) without a close below EMA(mid); signal bar closes above the prior
                high and above EMA(fast).
  CONTINUATION  close breaks the highest high of the prior ``bos_lookback`` bars (break of
                structure) with rising ADX, positive momentum acceleration and volume.
Late-entry guard: no entry when close is more than ``max_extension_atr`` ATR from EMA(fast).
Stop: below the 10-bar swing low (−0.25 ATR), clipped to [1.0, stop_atr] ATR. TP1 at tp1_r R
(partial), remainder trails with a chandelier stop of ``trail_atr`` ATR. Exits on deterioration are
signalled through :meth:`exit_signal` (close back through EMA(mid) or opposite DI dominance).
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..config import TrendConfig
from ..core.types import Direction, EngineSignal, TargetLevel
from .base import Engine, MarketContext, combine, common_features, fval, score_linear


class TrendEngine(Engine):
    name = "trend"
    title = "Trend & Momentum"
    description = "Systematic trend-following: pullbacks and break-of-structure continuation with ATR risk."

    def __init__(self, cfg: TrendConfig) -> None:
        super().__init__(cfg)

    def min_bars(self) -> int:
        return max(self.cfg.ema_slow + 20, 250)

    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        c, cfg = ctx.candles, self.cfg
        f = common_features(c)
        adx, pdi, mdi = ind.adx(c.high, c.low, c.close, cfg.adx_period)
        roc = (c.close / ind.shift(c.close, 12) - 1.0) * 100
        h1 = ctx.htf("1h")
        if len(h1) > 10:
            hf = ctx.htf_aligned("1h", ind.ema(h1.close, cfg.htf_ema_fast))
            hs = ctx.htf_aligned("1h", ind.ema(h1.close, cfg.htf_ema_slow))
            h1_dir = np.sign(hf - hs)
        else:
            h1_dir = np.zeros(len(c))
        f.update(
            ema_f=ind.ema(c.close, cfg.ema_fast), ema_m=ind.ema(c.close, cfg.ema_mid), ema_s=ind.ema(c.close, cfg.ema_slow),
            adx=adx, pdi=pdi, mdi=mdi, roc=roc, roc_acc=roc - ind.shift(roc, 6), h1_dir=h1_dir,
            hh=ind.shift(ind.rolling_max(c.high, cfg.bos_lookback), 1),
            ll=ind.shift(ind.rolling_min(c.low, cfg.bos_lookback), 1),
            swing_lo=ind.rolling_min(c.low, 10), swing_hi=ind.rolling_max(c.high, 10),
        )
        return f

    def market_state(self, f: dict[str, np.ndarray], i: int) -> tuple[str, Direction]:
        cfg = self.cfg
        ef, em, es = fval(f, "ema_f", i), fval(f, "ema_m", i), fval(f, "ema_s", i)
        adx, adx_p = fval(f, "adx", i, 0), fval(f, "adx", i - 3, 0)
        pdi, mdi = fval(f, "pdi", i, 0), fval(f, "mdi", i, 0)
        bull = ef > em > es and pdi > mdi
        bear = ef < em < es and mdi > pdi
        d = Direction.LONG if bull else Direction.SHORT if bear else Direction.FLAT
        efp, emp = fval(f, "ema_f", i - 1), fval(f, "ema_m", i - 1)
        if adx > 40 and adx < adx_p - 2:
            return "TREND_EXHAUSTION", d
        if (efp > emp and ef < em) or (efp < emp and ef > em):
            return "TREND_REVERSAL", Direction.FLAT
        if d is Direction.FLAT or adx < cfg.min_adx:
            return "NO_TREND", Direction.FLAT
        if fval(f, "roc_acc", i, 0) * d.sign > 0 and adx > adx_p:
            label = "MOMENTUM_ACCELERATION"
        elif adx >= cfg.strong_adx:
            label = "STRONG_BULL" if bull else "STRONG_BEAR"
        else:
            label = "WEAK_BULL" if bull else "WEAK_BEAR"
        return label, d

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        state, d = self.market_state(f, i)
        atr = fval(f, "atr", i)
        feats = {k: fval(f, k, i) for k in ("adx", "pdi", "mdi", "roc", "roc_acc", "ema_f", "ema_m", "ema_s", "h1_dir")}
        feats["atr"] = atr
        if d is Direction.FLAT or not np.isfinite(atr) or atr <= 0:
            return self.flat(ctx, i, state, [f"no qualified trend (ADX {feats['adx']:.1f})"], **feats)
        if state == "TREND_EXHAUSTION":
            return self.flat(ctx, i, state, ["ADX rolling over from >40 — exhaustion risk"], **feats)
        s = d.sign
        cl = float(c.close[i])
        h1_dir = feats["h1_dir"]
        if h1_dir == -s:
            return self.flat(ctx, i, state, ["1h trend opposes 5m trend"], **feats)
        ext = (cl - feats["ema_f"]) * s / atr
        feats["extension_atr"] = ext
        if ext > cfg.max_extension_atr:
            return self.flat(ctx, i, state, [f"overextended {ext:.1f} ATR from EMA{cfg.ema_fast} — late entry rejected"], **feats)
        relvol = fval(f, "relvol", i, 0)
        setup = ""
        reasons: list[str] = []
        # pullback
        if cfg.allow_pullback:
            w0 = max(0, i - 5)
            zone_hi = np.maximum(f["ema_f"][w0:i], f["ema_m"][w0:i])
            zone_lo = np.minimum(f["ema_f"][w0:i], f["ema_m"][w0:i])
            if d is Direction.LONG:
                touched = np.any(c.low[w0:i] <= zone_hi + cfg.pullback_atr * atr)
                held = np.all(c.close[w0:i] >= f["ema_m"][w0:i] - 0.25 * atr)
                trigger = cl > c.high[i - 1] and cl > feats["ema_f"]
            else:
                touched = np.any(c.high[w0:i] >= zone_lo - cfg.pullback_atr * atr)
                held = np.all(c.close[w0:i] <= f["ema_m"][w0:i] + 0.25 * atr)
                trigger = cl < c.low[i - 1] and cl < feats["ema_f"]
            if touched and held and trigger and relvol >= cfg.min_rel_volume:
                setup = "TREND_PULLBACK"
                reasons.append(f"pullback into EMA{cfg.ema_fast}/{cfg.ema_mid} zone held; resumption bar broke prior {'high' if s > 0 else 'low'}")
        if not setup and cfg.allow_continuation:
            lvl = fval(f, "hh", i) if d is Direction.LONG else fval(f, "ll", i)
            bos = (cl > lvl) if d is Direction.LONG else (cl < lvl)
            adx_up = fval(f, "adx", i, 0) > fval(f, "adx", i - 3, 0)
            if bos and adx_up and feats["roc_acc"] * s > 0 and relvol >= 1.2 * cfg.min_rel_volume:
                setup = "TREND_CONTINUATION"
                reasons.append(f"break of {cfg.bos_lookback}-bar structure with rising ADX and accelerating momentum")
        if not setup:
            return self.flat(ctx, i, state, ["trend present, no entry trigger"], **feats)
        swing = fval(f, "swing_lo", i) if d is Direction.LONG else fval(f, "swing_hi", i)
        dist = abs(cl - swing) + 0.25 * atr
        dist = float(np.clip(dist, 1.0 * atr, cfg.stop_atr * atr))
        stop = cl - s * dist
        tp1 = cl + s * cfg.tp1_r * dist
        far = cl + s * 10 * dist  # runner target; normally exited by the trailing stop
        conf, comps = combine({
            "trend_strength": (score_linear(feats["adx"], cfg.min_adx, cfg.strong_adx + 10), 1.2),
            "di_spread": (score_linear(abs(feats["pdi"] - feats["mdi"]), 5, 25), 0.8),
            "htf_alignment": (1.0 if h1_dir == s else 0.5, 1.0),
            "momentum": (score_linear(feats["roc_acc"] * s, 0, 0.6), 0.8),
            "volume": (score_linear(relvol, cfg.min_rel_volume, 2.5), 0.8),
            "entry_quality": (score_linear(ext, cfg.max_extension_atr, 0.3), 1.0),
            "setup": (1.0 if setup == "TREND_PULLBACK" else 0.7, 0.6),
        })
        reasons += [f"{state}: ADX {feats['adx']:.1f}, +DI {feats['pdi']:.0f} / −DI {feats['mdi']:.0f}, 1h {'aligned' if h1_dir == s else 'neutral'}",
                    f"stop {dist / atr:.2f} ATR, TP1 {cfg.tp1_r}R, runner trails {cfg.trail_atr} ATR"]
        feats["relvol"] = relvol
        return EngineSignal(self.name, ctx.symbol, d, conf, setup=setup, state=state, entry=cl, stop=float(stop),
                            targets=[TargetLevel(float(tp1), cfg.tp1_fraction), TargetLevel(float(far), 1.0)],
                            trail_atr=cfg.trail_atr, atr=atr, time_stop_bars=cfg.time_stop_bars, components=comps,
                            features=feats, reasons=reasons, ts=int(c.close_ts[i]))

    def exit_signal(self, f: dict[str, np.ndarray], i: int, direction: Direction, close: float) -> str | None:
        """Deterioration exit for an open trend position."""
        em = fval(f, "ema_m", i)
        pdi, mdi = fval(f, "pdi", i, 0), fval(f, "mdi", i, 0)
        if direction is Direction.LONG and close < em and mdi > pdi:
            return "trend deterioration: close below EMA-mid with −DI dominant"
        if direction is Direction.SHORT and close > em and pdi > mdi:
            return "trend deterioration: close above EMA-mid with +DI dominant"
        return None
