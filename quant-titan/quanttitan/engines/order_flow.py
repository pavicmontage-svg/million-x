"""ENGINE 4 — Order Flow & Liquidity.

Two feature tiers, both strictly measurable:

Bar tier (available in backtests — from Binance klines):
  taker-buy ratio        taker_buy_base / volume of the bar
  normalised CVD         Σ(2·taker_buy − volume) / Σ volume over ``cvd_window`` bars  (−1..1)
  volume z-score         abnormal volume vs ``vol_z_window`` bars
  avg trade size z       (quote volume / trade count) z-score — larger-than-usual prints
  price response         correlation of bar returns with signed volume over the window
  absorption             high |CVD| with small net price move (flow not moving price)

Live tier (paper/live only — from depth snapshots and aggTrades, see data/micro.py):
  book imbalance, spread, depth within 0.5%, depth change (liquidity deterioration/replenishment),
  aggressive buy/sell notional, large-print share and bias, per-trade price impact.

Role: by default a *confirmation* engine. The orchestrator uses :meth:`assess` to measure support or
opposition for another engine's direction. It acts as a standalone strategy only when configured
so *and* the Performance Auditor holds ≥ ``min_samples_standalone`` validated trades for it.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..config import OrderFlowConfig
from ..core.types import Direction, EngineSignal, TargetLevel
from .base import Engine, MarketContext, combine, common_features, fval, score_linear


class OrderFlowEngine(Engine):
    name = "order_flow"
    title = "Order Flow & Liquidity"
    description = "Measured aggressive flow, book imbalance and liquidity; confirmation or standalone."

    def __init__(self, cfg: OrderFlowConfig) -> None:
        super().__init__(cfg)

    def min_bars(self) -> int:
        return max(120, self.cfg.vol_z_window + 10)

    def features(self, ctx: MarketContext) -> dict[str, np.ndarray]:
        c, cfg = ctx.candles, self.cfg
        f = common_features(c)
        sv = ind.signed_volume(c.volume, c.taker_buy)
        w = cfg.cvd_window
        with np.errstate(divide="ignore", invalid="ignore"):
            cvd = ind.rolling_sum(sv, w) / ind.rolling_sum(c.volume, w)
            avg_size = c.quote_volume / np.maximum(c.trades, 1)
        ret = ind.log_returns(c.close)
        net_move = np.log(c.close / ind.shift(c.close, w))
        atr_pct = f["atr"] / c.close
        with np.errstate(divide="ignore", invalid="ignore"):
            move_atr = net_move / (atr_pct * np.sqrt(w))
        f.update(
            cvd=cvd, vol_z=ind.zscore(c.volume, cfg.vol_z_window), size_z=ind.zscore(avg_size, cfg.vol_z_window),
            response=ind.rolling_corr(ret, sv / np.maximum(c.volume, 1e-12), w * 2), move_atr=move_atr,
        )
        return f

    def _bar_score(self, f: dict[str, np.ndarray], i: int, d: Direction) -> tuple[float, dict[str, float], dict]:
        s = d.sign
        cvd = fval(f, "cvd", i, 0.0) * s
        taker = fval(f, "taker", i, 0.5)
        flow = taker if s > 0 else 1 - taker
        resp = fval(f, "response", i, 0.0)
        move = fval(f, "move_atr", i, 0.0) * s
        volz = fval(f, "vol_z", i, 0.0)
        comps = {
            "cvd": (score_linear(cvd, 0.0, 0.25), 1.2),
            "taker": (score_linear(flow, 0.5, self.cfg.min_taker_ratio + 0.06), 1.0),
            "price_response": (score_linear(resp, 0.0, 0.5) if move > 0 else 0.0, 0.8),
            "volume": (score_linear(volz, 0.0, 2.0), 0.6),
        }
        feats = {"cvd": cvd * s, "taker": taker, "response": resp, "move_atr": move * s, "vol_z": volz,
                 "size_z": fval(f, "size_z", i, 0.0)}
        return 0.0, comps, feats

    def _live_components(self, ctx: MarketContext, d: Direction, comps: dict, feats: dict) -> list[str]:
        notes: list[str] = []
        m = ctx.micro
        if m is None:
            return notes
        s = d.sign
        comps["book_imbalance"] = (score_linear(m.book_imbalance * s, 0.0, self.cfg.min_book_imbalance * 2), 1.0)
        comps["aggressive_flow"] = (score_linear(m.aggr_imbalance * s, 0.0, 0.3), 1.0)
        comps["large_prints"] = (score_linear(m.large_trade_bias * s, 0.0, 0.5), 0.5)
        comps["liquidity"] = (score_linear(m.depth_change, -0.3, 0.1), 0.4)
        feats.update(book_imbalance=m.book_imbalance, aggr_imbalance=m.aggr_imbalance, spread_bps=m.spread_bps,
                     depth_usdt=m.depth_usdt, depth_change=m.depth_change, large_trade_share=m.large_trade_share,
                     large_trade_bias=m.large_trade_bias, price_impact=m.price_impact)
        if np.isfinite(m.spread_bps) and m.spread_bps > self.cfg.max_spread_bps:
            notes.append(f"spread {m.spread_bps:.1f}bps above {self.cfg.max_spread_bps}")
        if m.depth_change < -0.4:
            notes.append(f"liquidity deteriorated {m.depth_change:.0%}")
        return notes

    def assess(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int, d: Direction) -> tuple[float, float, dict]:
        """Return (support 0..100, opposition 0..100, features) for a proposed direction."""
        _, cs, feats = self._bar_score(f, i, d)
        self._live_components(ctx, d, cs, feats)
        support, _ = combine(cs)
        _, co, _ = self._bar_score(f, i, d.opposite())
        self._live_components(ctx, d.opposite(), co, {})
        opposition, _ = combine(co)
        return support, opposition, feats

    def evaluate(self, ctx: MarketContext, f: dict[str, np.ndarray], i: int) -> EngineSignal:
        c, cfg = ctx.candles, self.cfg
        atr = fval(f, "atr", i)
        best = None
        for d in (Direction.LONG, Direction.SHORT):
            _, comps, feats = self._bar_score(f, i, d)
            notes = self._live_components(ctx, d, comps, feats)
            conf, cmap = combine(comps)
            if best is None or conf > best[1]:
                best = (d, conf, cmap, feats, notes)
        d, conf, cmap, feats, notes = best
        feats["atr"] = atr
        s = d.sign
        cvd_ok = feats["cvd"] * s >= 0.1
        taker = feats["taker"] if s > 0 else 1 - feats["taker"]
        taker_ok = taker >= cfg.min_taker_ratio
        live_ok = True
        if ctx.micro is not None:
            live_ok = feats.get("book_imbalance", 0) * s >= cfg.min_book_imbalance and not notes
        absorption = abs(feats["cvd"]) > 0.25 and abs(feats["move_atr"]) < 0.2
        state = "ABSORPTION" if absorption else ("AGGRESSIVE_BUYING" if feats["cvd"] > 0.1 else
                                                 "AGGRESSIVE_SELLING" if feats["cvd"] < -0.1 else "BALANCED")
        if not (cvd_ok and taker_ok and live_ok and np.isfinite(atr) and atr > 0) or absorption:
            sig = self.flat(ctx, i, state, notes or ["flow not one-sided enough"], **feats)
            sig.components = cmap
            sig.confidence = 0.0
            return sig
        cl = float(c.close[i])
        stop = cl - s * cfg.stop_atr * atr
        tp1 = cl + s * cfg.tp1_r * cfg.stop_atr * atr
        reasons = [f"normalised CVD {feats['cvd']:+.2f} over {cfg.cvd_window} bars, taker ratio {feats['taker']:.2f}",
                   f"volume z {feats['vol_z']:+.1f}, price response corr {feats['response']:+.2f}"]
        if ctx.micro is not None:
            reasons.append(f"book imbalance {feats['book_imbalance']:+.2f}, aggressive imbalance {feats['aggr_imbalance']:+.2f}")
        return EngineSignal(self.name, ctx.symbol, d, conf, setup="ORDER_FLOW_IMPULSE", state=state, entry=cl,
                            stop=float(stop), targets=[TargetLevel(float(tp1), cfg.tp1_fraction),
                                                       TargetLevel(float(cl + s * 2 * cfg.tp1_r * cfg.stop_atr * atr), 1.0)],
                            atr=atr, time_stop_bars=cfg.time_stop_bars, role=cfg.role, components=cmap,
                            features=feats, reasons=reasons, ts=int(c.close_ts[i]))
