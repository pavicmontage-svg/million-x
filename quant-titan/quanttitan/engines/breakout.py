"""ENGINE 3 — Volatility Breakout.

Compression: Bollinger-band width (20, 2σ) percentile over ``width_pct_lookback`` bars is at or
below ``compression_pct`` on at least ``min_compression_bars`` of the last ``box_lookback`` bars
(a count, not a strict streak, so one noisy bar does not reset it), and the range box
(highest high / lowest low of the prior ``box_lookback`` bars) is no wider than ``max_box_atr`` ATR.

Breakout (LONG; SHORT mirrors): close > box_high + breakout_buffer_atr·ATR, with
  relative volume ≥ min_rel_volume (else LOW_VOLUME_BREAKOUT — rejected),
  breakout-bar true range / ATR(50) ≥ min_atr_expansion (volatility actually expanding on the bar),
  1h trend not opposing.
States: COMPRESSION, VALID_BREAKOUT, LOW_VOLUME_BREAKOUT, FALSE_BREAKOUT (close back inside the box
within false_breakout_bars), BREAKOUT_CONTINUATION, BREAKOUT_RETEST, FAILED_RETEST, NO_SETUP.
Entry modes: ``breakout_close`` (enter on the breakout bar close) or ``retest`` (enter when price
returns to the broken edge within retest_window bars and holds).
Stop: box mid (or opposite edge) − stop_buffer_atr·ATR. TP1 = edge + target_box_mult·box height;
remainder trails at trail_atr ATR.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..config import BreakoutConfig
from ..core.types import Direction, EngineSignal, TargetLevel
from .base import Engine, MarketContext, combine, common_features, fval, score_linear


def _streak(mask: np.ndarray) -> np.ndarray:
    """Length of the run of True values ending at each index."""
    out = np.zeros(len(mask), dtype=float)
    run = 0
    for k, m in enumerate(mask):
        run = run + 1 if m else 0
        out[k] = run
    return out


class BreakoutEngine(Engine):
    name = "breakout"
    title = "Volatility Breakout"
    description = "Trades expansion out of measured volatility compression; filters low-volume and false breakouts."

    def __init__(self, cfg: BreakoutConfig) -> None:
        super().__init__(cfg)

    def min_bars(self) -> int:
        return max(300, self.cfg.box_lookback + 60)

    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        c, cfg = ctx.candles, self.cfg
        f = common_features(c)
        _, _, _, width = ind.bollinger(c.close, cfg.bb_period, cfg.bb_mult)
        rank = ind.rolling_percentile_rank(width, cfg.width_pct_lookback)
        comp = np.nan_to_num(rank, nan=100.0) <= cfg.compression_pct
        atr5 = ind.atr(c.high, c.low, c.close, 5)
        atr50 = ind.atr(c.high, c.low, c.close, 50)
        h1 = ctx.htf("1h")
        if len(h1) > 10:
            h1_dir = ctx.htf_aligned("1h", np.sign(ind.ema(h1.close, 20) - ind.ema(h1.close, 50)))
        else:
            h1_dir = np.zeros(len(c))
        f.update(
            width_rank=rank, comp_streak=_streak(comp),
            comp_count=ind.rolling_sum(comp.astype(float), cfg.box_lookback),
            box_hi=ind.shift(ind.rolling_max(c.high, cfg.box_lookback), 1),
            box_lo=ind.shift(ind.rolling_min(c.low, cfg.box_lookback), 1),
            atr_ratio=atr5 / atr50, tr_ratio=ind.true_range(c.high, c.low, c.close) / ind.shift(atr50, 1), h1_dir=h1_dir,
        )
        return f

    def _breakout_at(self, c, f, j: int) -> tuple[Direction, float, float]:
        """Was bar j a breakout bar out of a qualified compression box? Returns (dir, box_hi, box_lo)."""
        cfg = self.cfg
        hi, lo, atr = fval(f, "box_hi", j), fval(f, "box_lo", j), fval(f, "atr", j)
        if not (np.isfinite(hi) and np.isfinite(lo) and atr > 0):
            return Direction.FLAT, hi, lo
        if fval(f, "comp_count", j - 1, 0) < cfg.min_compression_bars or (hi - lo) > cfg.max_box_atr * atr:
            return Direction.FLAT, hi, lo
        if c.close[j] > hi + cfg.breakout_buffer_atr * atr:
            return Direction.LONG, hi, lo
        if c.close[j] < lo - cfg.breakout_buffer_atr * atr:
            return Direction.SHORT, hi, lo
        return Direction.FLAT, hi, lo

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        atr = fval(f, "atr", i)
        feats = {k: fval(f, k, i) for k in ("width_rank", "comp_streak", "comp_count", "box_hi", "box_lo", "atr_ratio", "relvol", "h1_dir")}
        feats["atr"] = atr
        if not np.isfinite(atr) or atr <= 0:
            return self.flat(ctx, i, "NO_SETUP", ["ATR undefined"], **feats)
        # recent breakouts (for false-breakout / retest / continuation states)
        recent = None
        for j in range(i - 1, max(0, i - max(cfg.retest_window, cfg.false_breakout_bars)) - 1, -1):
            d_j, hi_j, lo_j = self._breakout_at(c, f, j)
            if d_j is not Direction.FLAT:
                recent = (j, d_j, hi_j, lo_j)
                break
        d, hi, lo = self._breakout_at(c, f, i)
        if cfg.entry_mode == "breakout_close" and d is not Direction.FLAT:
            return self._entry(ctx, f, i, d, hi, lo, "VOLATILITY_BREAKOUT", feats)
        if recent is not None:
            j, dj, hij, loj = recent
            s = dj.sign
            edge = hij if dj is Direction.LONG else loj
            height = hij - loj
            cl = c.close[i]
            if (cl - edge) * s < 0 and i - j <= cfg.false_breakout_bars:
                # in retest mode a shallow dip back through the edge is part of a retest, not a failure
                tolerated = cfg.entry_mode == "retest" and (cl - edge) * s > -cfg.stop_buffer_atr * atr
                if not tolerated:
                    return self.flat(ctx, i, "FALSE_BREAKOUT", [f"closed back inside the box {i - j} bar(s) after breakout"], **feats)
            if cfg.entry_mode == "retest" and i - j <= cfg.retest_window:
                touched = (c.low[i] <= edge + 0.2 * atr) if dj is Direction.LONG else (c.high[i] >= edge - 0.2 * atr)
                held = (cl - edge) * s > 0
                if touched and held:
                    return self._entry(ctx, f, i, dj, hij, loj, "BREAKOUT_RETEST", feats)
                if touched and not held:
                    return self.flat(ctx, i, "FAILED_RETEST", ["retest of broken edge failed to hold"], **feats)
            if (cl - edge) * s > 0.5 * height:
                return self.flat(ctx, i, "BREAKOUT_CONTINUATION", ["breakout extended beyond half the box height"], **feats)
        if fval(f, "comp_count", i, 0) >= cfg.min_compression_bars:
            return self.flat(ctx, i, "COMPRESSION", [f"BB width ≤ {cfg.compression_pct:.0f}th pct on {feats['comp_count']:.0f} of the last {cfg.box_lookback} bars"], **feats)
        return self.flat(ctx, i, "NO_SETUP", [], **feats)

    def _entry(self, ctx, f, i, d: Direction, hi: float, lo: float, setup: str, feats: dict) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        s = d.sign
        atr = feats["atr"]
        relvol, ratio = fval(f, "relvol", i, 0), fval(f, "tr_ratio", i, 0)
        if relvol < cfg.min_rel_volume:
            return self.flat(ctx, i, "LOW_VOLUME_BREAKOUT", [f"relative volume {relvol:.2f} < {cfg.min_rel_volume}"], **feats)
        if ratio < cfg.min_atr_expansion:
            return self.flat(ctx, i, "LOW_VOLUME_BREAKOUT", [f"breakout bar range {ratio:.2f}× ATR(50) < {cfg.min_atr_expansion} — no expansion"], **feats)
        h1 = feats["h1_dir"]
        if h1 == -s:
            return self.flat(ctx, i, "VALID_BREAKOUT", ["1h trend opposes breakout direction"], **feats)
        cl = float(c.close[i])
        height = hi - lo
        mid = (hi + lo) / 2
        base = mid if cfg.stop_mode == "box_mid" else (lo if d is Direction.LONG else hi)
        stop = base - s * cfg.stop_buffer_atr * atr
        edge = hi if d is Direction.LONG else lo
        tp1 = edge + s * cfg.target_box_mult * height
        risk = abs(cl - stop)
        if risk <= 0 or (tp1 - cl) * s <= 0:
            return self.flat(ctx, i, "BREAKOUT_CONTINUATION", ["entry already beyond measured-move target"], **feats)
        rr = (tp1 - cl) * s / risk
        feats["rr_tp1"] = rr
        conf, comps = combine({
            "compression": (score_linear(fval(f, "comp_count", i - 1, 0), cfg.min_compression_bars, cfg.box_lookback), 1.0),
            "volume": (score_linear(relvol, cfg.min_rel_volume, 3 * cfg.min_rel_volume), 1.2),
            "expansion": (score_linear(ratio, cfg.min_atr_expansion, 2.5 * cfg.min_atr_expansion), 1.0),
            "close_strength": (score_linear((cl - edge) * s / atr, 0, 1.0), 0.8),
            "htf": (1.0 if h1 == s else 0.6, 0.8),
            "reward": (score_linear(rr, 0.8, 2.5), 0.8),
            "flow": (score_linear(fval(f, "taker", i, 0.5) if s > 0 else 1 - fval(f, "taker", i, 0.5), 0.5, 0.62), 0.8),
        })
        reasons = [f"compression on {fval(f, 'comp_count', i - 1, 0):.0f}/{cfg.box_lookback} bars (BB width ≤ {cfg.compression_pct:.0f}th pct), box {height / atr:.1f} ATR",
                   f"close {'above' if s > 0 else 'below'} box edge with relvol {relvol:.2f}, bar range {ratio:.2f}× ATR(50)",
                   f"stop at box {'mid' if cfg.stop_mode == 'box_mid' else 'opposite edge'}, TP1 measured move {rr:.2f}R"]
        return EngineSignal(self.name, ctx.symbol, d, conf, setup=setup, state="VALID_BREAKOUT" if setup != "BREAKOUT_RETEST" else setup,
                            entry=cl, stop=float(stop),
                            targets=[TargetLevel(float(tp1), cfg.tp1_fraction), TargetLevel(float(cl + s * 10 * risk), 1.0)],
                            trail_atr=cfg.trail_atr, atr=atr, time_stop_bars=cfg.time_stop_bars, components=comps,
                            features=feats, reasons=reasons, ts=int(c.close_ts[i]))
