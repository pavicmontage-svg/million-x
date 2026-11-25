"""Measurable microstructure features from an order-book snapshot and recent aggregated trades.

Nothing here claims to identify "institutional" or hidden orders. ``large_trade_share`` is simply
the share of traded notional coming from prints above a size quantile of the recent sample.
"""
from __future__ import annotations

import numpy as np

from ..engines.base import MicroStats


def book_metrics(book: dict, levels: int = 10, band_pct: float = 0.005) -> dict:
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return {"spread_bps": float("nan"), "mid": float("nan"), "imbalance": 0.0, "depth_usdt": 0.0}
    bb, ba = float(bids[0][0]), float(asks[0][0])
    mid = (bb + ba) / 2
    spread_bps = (ba - bb) / mid * 1e4
    bq = sum(float(q) for _, q in bids[:levels])
    aq = sum(float(q) for _, q in asks[:levels])
    imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
    lo, hi = mid * (1 - band_pct), mid * (1 + band_pct)
    depth = sum(float(p) * float(q) for p, q in bids if float(p) >= lo) + sum(float(p) * float(q) for p, q in asks if float(p) <= hi)
    return {"spread_bps": spread_bps, "mid": mid, "imbalance": imb, "depth_usdt": depth}


def trade_metrics(trades: list[dict], quantile: float = 0.95) -> dict:
    """``trades``: [{price, qty, ts, buyer_maker}] — buyer_maker=True means the aggressor sold."""
    if not trades:
        return {"buy": 0.0, "sell": 0.0, "large_share": 0.0, "large_bias": 0.0, "count": 0, "impact": 0.0}
    p = np.array([t["price"] for t in trades], dtype=float)
    q = np.array([t["qty"] for t in trades], dtype=float)
    side = np.where(np.array([t["buyer_maker"] for t in trades]), -1.0, 1.0)
    notional = p * q
    buy = float(notional[side > 0].sum())
    sell = float(notional[side < 0].sum())
    thr = np.quantile(notional, quantile) if len(notional) >= 20 else np.inf
    large = notional >= thr
    lt = notional[large].sum()
    large_share = float(lt / notional.sum()) if notional.sum() > 0 else 0.0
    large_bias = float((notional[large] * side[large]).sum() / lt) if lt > 0 else 0.0
    impact = 0.0
    if len(p) >= 30:
        # regress per-trade log return on the signed notional of that trade (per 1M USDT)
        r = np.diff(np.log(p))
        x = (side * notional)[1:] / 1e6
        if np.var(x) > 0:
            impact = float(np.cov(x, r, ddof=0)[0, 1] / np.var(x))
    return {"buy": buy, "sell": sell, "large_share": large_share, "large_bias": large_bias, "count": len(trades),
            "impact": impact}


def micro_stats(book: dict | None, trades: list[dict] | None, prev: MicroStats | None = None, *,
                levels: int = 10, quantile: float = 0.95, ts: int = 0) -> MicroStats:
    bm = book_metrics(book or {}, levels)
    tm = trade_metrics(trades or [], quantile)
    depth_change = 0.0
    if prev is not None and prev.depth_usdt > 0:
        depth_change = bm["depth_usdt"] / prev.depth_usdt - 1.0
    return MicroStats(spread_bps=bm["spread_bps"], mid=bm["mid"], book_imbalance=bm["imbalance"],
                      depth_usdt=bm["depth_usdt"], depth_change=depth_change, aggr_buy_usdt=tm["buy"],
                      aggr_sell_usdt=tm["sell"], large_trade_share=tm["large_share"],
                      large_trade_bias=tm["large_bias"], trade_count=tm["count"], price_impact=tm["impact"], ts=ts)
