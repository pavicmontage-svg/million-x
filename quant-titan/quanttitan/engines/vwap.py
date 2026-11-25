"""ENGINE 1 — VWAP Institutional Mean Reversion.

Band construction (see ``indicators.session_vwap`` and docs/ENGINES.md):
    p_i = (H_i + L_i + C_i) / 3,  VWAP = Σ v_i p_i / Σ v_i,
    σ  = sqrt(Σ v_i p_i² / Σ v_i − VWAP²),  band_k = VWAP ± k σ,  summed over the session
    (UTC day by default, or a rolling window). Deviation is measured as z = (price − VWAP)/σ.

LONG (SHORT is the mirror image):
  1. deviation   — a low inside the last ``touch_lookback`` bars reached z ≤ −entry_band
  2. suitability — the excursion is ≥ ``min_deviation_atr`` ATR below VWAP (large enough to pay costs)
                    and the session has ≥ ``min_session_bars`` bars (σ is meaningful)
  3. trend guard — not STRONG_TREND_CONTINUATION (≥ ride_bars closes beyond the band in ride_window),
                    1h ADX ≤ max_htf_adx when the 1h trend opposes, regime is not a strong opposing trend
  4. confirmation — the signal bar closes back inside the band, is bullish, and shows rejection
                    (lower wick ≥ min_wick_ratio of range or close above the prior high)
  5. flow/momentum — relative volume ≥ min_rel_volume, taker-buy ratio ≥ threshold, RSI turning up
  6. reward      — TP1 at VWAP must offer ≥ min_rr_tp1 R; TP2 at VWAP + 1σ
States reported: NORMAL_DEVIATION, OVEREXTENDED, STRONG_TREND_CONTINUATION, POTENTIAL_REVERSAL,
FALSE_REJECTION, VWAP_RECLAIM, VWAP_BREAKDOWN, HIGH_VOLATILITY, INSIDE_BANDS.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..config import VWAPConfig
from ..core.types import Direction, EngineSignal, Regime, TargetLevel
from ..data.candles import DAY_MS, bars_in
from .base import Engine, MarketContext, combine, common_features, fval, score_linear


class VWAPEngine(Engine):
    name = "vwap"
    title = "VWAP Institutional Mean Reversion"
    description = "Fades statistically meaningful deviations from session VWAP after confirmed rejection."

    def __init__(self, cfg: VWAPConfig) -> None:
        super().__init__(cfg)

    def min_bars(self) -> int:
        return 300

    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        c, cfg = ctx.candles, self.cfg
        vw, sd, n_in = ind.session_vwap(c.ts, c.high, c.low, c.close, c.volume, anchor=cfg.session_anchor,
                                        rolling_bars=cfg.rolling_bars)
        f = common_features(c)
        with np.errstate(divide="ignore", invalid="ignore"):
            f["z_close"] = (c.close - vw) / sd
            f["z_low"] = (c.low - vw) / sd
            f["z_high"] = (c.high - vw) / sd
        h1 = ctx.htf("1h")
        if len(h1) > 30:
            a, p, mi = ind.adx(h1.high, h1.low, h1.close, 14)
            f["h1_adx"] = ctx.htf_aligned("1h", a)
            f["h1_dir"] = ctx.htf_aligned("1h", np.sign(p - mi))
        else:
            f["h1_adx"] = np.full(len(c), np.nan)
            f["h1_dir"] = np.zeros(len(c))
        f.update(vwap=vw, sd=sd, n_in=n_in, rsi=ind.rsi(c.close, 14),
                 atr_rank=ind.rolling_percentile_rank(f["atr"] / c.close, bars_in(c.timeframe, 7 * DAY_MS)))
        return f

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        vw, sd, atr = fval(f, "vwap", i), fval(f, "sd", i), fval(f, "atr", i)
        if not (np.isfinite(vw) and np.isfinite(sd) and sd > 0 and atr > 0) or fval(f, "n_in", i, 0) < cfg.min_session_bars:
            return self.flat(ctx, i, "INSUFFICIENT_SESSION", ["VWAP session too young or σ undefined"])
        z = fval(f, "z_close", i)
        zp = fval(f, "z_close", i - 1)
        feats = {"vwap": vw, "sigma": sd, "z": z, "atr": atr}
        # informational structure states
        info_state = "INSIDE_BANDS"
        if np.isfinite(zp) and zp < 0 <= z and fval(f, "relvol", i, 0) >= 1.0:
            info_state = "VWAP_RECLAIM"
        elif np.isfinite(zp) and zp > 0 >= z and fval(f, "relvol", i, 0) >= 1.0:
            info_state = "VWAP_BREAKDOWN"
        if fval(f, "atr_rank", i, 50) >= 95:
            return self.flat(ctx, i, "HIGH_VOLATILITY", ["ATR% above 95th percentile — deviations unreliable"], **feats)

        long_sig = self._side(ctx, f, i, Direction.LONG)
        if long_sig.direction is not Direction.FLAT:
            return long_sig
        short_sig = self._side(ctx, f, i, Direction.SHORT)
        if short_sig.direction is not Direction.FLAT:
            return short_sig
        # report the more informative of the two neutral states
        for s in (long_sig, short_sig):
            if s.state not in ("INSIDE_BANDS", ""):
                return s
        return self.flat(ctx, i, info_state, [f"z={z:+.2f} inside ±{cfg.entry_band}σ"], **feats)

    def _side(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int, d: Direction) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        s = d.sign  # +1 long (fade a move down), -1 short
        lb = cfg.touch_lookback
        lo_i = max(0, i - lb + 1)
        vw, sd, atr = fval(f, "vwap", i), fval(f, "sd", i), fval(f, "atr", i)
        # extreme excursion in the fade direction
        if d is Direction.LONG:
            ext_z = float(np.nanmin(f["z_low"][lo_i:i + 1]))
            extreme = float(np.min(c.low[lo_i:i + 1]))
        else:
            ext_z = -float(np.nanmax(f["z_high"][lo_i:i + 1]))
            extreme = float(np.max(c.high[lo_i:i + 1]))
        feats = {"vwap": vw, "sigma": sd, "z": fval(f, "z_close", i), "excursion_z": ext_z, "atr": atr}
        if ext_z > -cfg.entry_band:
            return self.flat(ctx, i, "INSIDE_BANDS", [], **feats)
        dev_atr = abs(vw - extreme) / atr
        feats["deviation_atr"] = dev_atr
        if dev_atr < cfg.min_deviation_atr:
            return self.flat(ctx, i, "NORMAL_DEVIATION",
                             [f"deviation {dev_atr:.2f} ATR < {cfg.min_deviation_atr} ATR — too small to pay costs"], **feats)
        # band riding = trend continuation, do not fade
        w0 = max(0, i - cfg.ride_window + 1)
        zc = f["z_close"][w0:i + 1] * s
        riding = int(np.sum(zc <= -cfg.entry_band))
        feats["ride_count"] = riding
        if riding >= cfg.ride_bars:
            return self.flat(ctx, i, "STRONG_TREND_CONTINUATION",
                             [f"{riding} of last {cfg.ride_window} closes beyond the {cfg.entry_band}σ band — not fading a trend"], **feats)
        # higher-timeframe trend guard
        h1_adx, h1_dir = fval(f, "h1_adx", i), fval(f, "h1_dir", i, 0.0)
        if np.isfinite(h1_adx) and h1_adx > cfg.max_htf_adx and h1_dir == -s:
            return self.flat(ctx, i, "STRONG_TREND_CONTINUATION",
                             [f"1h ADX {h1_adx:.0f} > {cfg.max_htf_adx} with opposing 1h trend"], **feats)
        reg = ctx.regime
        if reg is not None and reg.regime is Regime.STRONG_TREND and reg.trend_direction.sign == -s:
            return self.flat(ctx, i, "STRONG_TREND_CONTINUATION", ["regime engine: strong opposing trend"], **feats)
        overext = ext_z <= -cfg.overextended_band
        # confirmation on bar i
        o, h, l, cl = c.open[i], c.high[i], c.low[i], c.close[i]
        rng = max(h - l, 1e-12)
        wick = ((min(o, cl) - l) if d is Direction.LONG else (h - max(o, cl))) / rng
        z_now = fval(f, "z_close", i) * s
        back_inside = z_now > -cfg.entry_band
        bar_dir_ok = (cl > o) if d is Direction.LONG else (cl < o)
        beyond_prev = (cl > c.high[i - 1]) if d is Direction.LONG else (cl < c.low[i - 1])
        rejection = wick >= cfg.min_wick_ratio or beyond_prev
        feats.update(wick_ratio=wick, back_inside=float(back_inside))
        # false rejection: previous bar looked like a reversal, this bar closed back beyond the band
        zp = fval(f, "z_close", i - 1) * s
        zpp = fval(f, "z_close", i - 2) * s
        if not back_inside and np.isfinite(zp) and zp > -cfg.entry_band and np.isfinite(zpp) and zpp <= -cfg.entry_band:
            return self.flat(ctx, i, "FALSE_REJECTION", ["reclaimed the band then closed back beyond it"], **feats)
        if not (back_inside and bar_dir_ok and rejection):
            st = "OVEREXTENDED" if overext else "NORMAL_DEVIATION"
            return self.flat(ctx, i, st, [f"beyond {cfg.entry_band}σ, awaiting rejection confirmation"], **feats)
        relvol = fval(f, "relvol", i, 0.0)
        taker = fval(f, "taker", i, 0.5)
        flow = taker if d is Direction.LONG else 1.0 - taker
        rsi_now, rsi_prev = fval(f, "rsi", i, 50), fval(f, "rsi", i - 1, 50)
        rsi_turn = (rsi_now > rsi_prev) if d is Direction.LONG else (rsi_now < rsi_prev)
        rsi_ext = float(np.nanmin(f["rsi"][lo_i:i + 1])) if d is Direction.LONG else 100 - float(np.nanmax(f["rsi"][lo_i:i + 1]))
        feats.update(relvol=relvol, taker_flow=flow, rsi=rsi_now)
        if relvol < cfg.min_rel_volume:
            return self.flat(ctx, i, "POTENTIAL_REVERSAL", [f"rejection without volume (relvol {relvol:.2f})"], **feats)
        if flow < cfg.min_taker_ratio_confirm:
            return self.flat(ctx, i, "POTENTIAL_REVERSAL", [f"aggressive flow does not confirm ({flow:.2f})"], **feats)
        if overext and not (wick >= cfg.min_wick_ratio and beyond_prev and relvol >= 1.5 * cfg.min_rel_volume):
            return self.flat(ctx, i, "OVEREXTENDED", ["beyond overextension band; needs exceptional reversal evidence"], **feats)
        # levels
        entry = float(cl)
        stop = extreme - s * cfg.stop_buffer_atr * atr
        risk = abs(entry - stop)
        tp1 = vw
        tp2 = vw + s * sd
        if risk <= 0 or (tp1 - entry) * s <= 0:
            return self.flat(ctx, i, "POTENTIAL_REVERSAL", ["VWAP already reached — no reward left"], **feats)
        rr1 = (tp1 - entry) * s / risk
        feats["rr_tp1"] = rr1
        if rr1 < cfg.min_rr_tp1:
            return self.flat(ctx, i, "POTENTIAL_REVERSAL", [f"reward to VWAP {rr1:.2f}R < {cfg.min_rr_tp1}R"], **feats)
        htf_ok = 1.0 if h1_dir == s or not np.isfinite(h1_adx) else (0.6 if h1_dir == 0 else score_linear(h1_adx, cfg.max_htf_adx, 15))
        conf, comps = combine({
            "deviation": (score_linear(-ext_z, cfg.entry_band, cfg.entry_band + 1.5), 1.0),
            "rejection": (score_linear(wick, 0.2, 0.6) * 0.7 + 0.3 * float(beyond_prev), 1.2),
            "volume": (score_linear(relvol, cfg.min_rel_volume, 2.5), 1.0),
            "flow": (score_linear(flow, 0.5, 0.65), 1.0),
            "momentum": (0.5 * float(rsi_turn) + 0.5 * score_linear(rsi_ext, 45, 25), 0.8),
            "htf_trend": (htf_ok, 1.0),
            "reward": (score_linear(rr1, cfg.min_rr_tp1, 3.0), 0.8),
        })
        reasons = [
            f"{'low' if s > 0 else 'high'} reached {ext_z:+.2f}σ vs VWAP ({dev_atr:.1f} ATR)",
            f"closed back inside the {cfg.entry_band}σ band with {'rejection wick' if wick >= cfg.min_wick_ratio else 'break of prior bar'}",
            f"relative volume {relvol:.2f}, taker flow {flow:.2f}",
            f"TP1 at VWAP = {rr1:.2f}R; stop beyond the excursion extreme",
        ]
        return EngineSignal(self.name, ctx.symbol, d, conf, setup="VWAP_REVERSION", state="POTENTIAL_REVERSAL",
                            entry=entry, stop=float(stop),
                            targets=[TargetLevel(float(tp1), cfg.tp1_fraction), TargetLevel(float(tp2), 1.0)],
                            atr=atr, time_stop_bars=cfg.time_stop_bars, components=comps, features=feats,
                            reasons=reasons, ts=int(c.close_ts[i]))
