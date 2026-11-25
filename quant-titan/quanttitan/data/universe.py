"""Dynamic trading universe: top-N USDT perpetuals by a market-quality score.

Hard filters (an instrument failing any is never traded automatically):
  status TRADING, listed ≥ min_listing_days, 24h quote volume ≥ min, spread ≤ max_spread_bps,
  depth within 0.5% ≥ min_depth_usdt (checked on the shortlist), |funding| ≤ max_abs_funding, not blacklisted.
Score = log10(quote volume) − 0.5 · spread_bps − 200 · |funding| (liquidity first, then cost).
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from ..config import UniverseConfig
from ..core.types import ContractSpec
from .micro import book_metrics


@dataclass
class UniverseEntry:
    symbol: str
    eligible: bool
    score: float
    quote_volume: float
    spread_bps: float
    funding: float
    depth_usdt: float
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


async def select_universe(client, cfg: UniverseConfig, specs: dict[str, ContractSpec]) -> list[UniverseEntry]:
    if cfg.mode == "fixed":
        return [UniverseEntry(s, s in specs or not specs, 0.0, 0.0, float("nan"), 0.0, 0.0, ["fixed universe"])
                for s in cfg.fixed_symbols]
    tickers = await client.tickers_24h()
    books = await client.book_tickers()
    prem = await client.premium_index()
    now = time.time() * 1000
    rows: list[UniverseEntry] = []
    for t in tickers:
        s = t["symbol"]
        spec = specs.get(s)
        if spec is None or not s.endswith("USDT"):
            continue
        reasons = []
        b = books.get(s)
        spread = (b["ask"] - b["bid"]) / ((b["ask"] + b["bid"]) / 2) * 1e4 if b and b["bid"] > 0 else float("inf")
        fund = prem.get(s, {}).get("funding", 0.0)
        if spec.status != "TRADING":
            reasons.append(f"status {spec.status}")
        if s in cfg.blacklist:
            reasons.append("blacklisted")
        if spec.onboard_ms and (now - spec.onboard_ms) / 86_400_000 < cfg.min_listing_days:
            reasons.append("listed too recently")
        if t["quote_volume"] < cfg.min_quote_volume_24h:
            reasons.append(f"24h volume {t['quote_volume'] / 1e6:.0f}M < {cfg.min_quote_volume_24h / 1e6:.0f}M")
        if spread > cfg.max_spread_bps:
            reasons.append(f"spread {spread:.1f}bps")
        if abs(fund) > cfg.max_abs_funding:
            reasons.append(f"funding {fund:.4%}")
        score = math.log10(max(t["quote_volume"], 1)) - 0.5 * min(spread, 100) - 200 * abs(fund)
        rows.append(UniverseEntry(s, not reasons, round(score, 4), t["quote_volume"], round(spread, 3), fund, 0.0, reasons))
    rows.sort(key=lambda r: (not r.eligible, -r.score))
    # depth check on the shortlist only (cost-aware)
    picked = 0
    for r in rows:
        if not r.eligible or picked >= cfg.size:
            continue
        try:
            bm = book_metrics(await client.depth(r.symbol, 100))
            r.depth_usdt = round(bm["depth_usdt"], 2)
            if bm["depth_usdt"] < cfg.min_depth_usdt:
                r.eligible = False
                r.reasons.append(f"depth {bm['depth_usdt'] / 1e3:.0f}k < {cfg.min_depth_usdt / 1e3:.0f}k USDT")
                continue
        except Exception as e:  # depth unavailable: do not trade it
            r.eligible = False
            r.reasons.append(f"depth unavailable ({type(e).__name__})")
            continue
        picked += 1
    selected = [r for r in rows if r.eligible][: cfg.size]
    return selected + [r for r in rows if r not in selected][:40]
