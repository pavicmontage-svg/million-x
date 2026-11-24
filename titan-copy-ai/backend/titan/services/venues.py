"""Portfolio bootstrap + adapter factory (credentials are decrypted only here, in memory)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.base import ExecutionAdapter
from ..adapters.feeds import BinanceRestFeed, PushFeed
from ..adapters.paper import PaperBroker, SimParams
from ..audit import audit
from ..config import get_settings
from ..db import session_scope
from ..markets import crypto as cm
from ..models import Credential, Market, Portfolio, TradingMode
from ..security import decrypt_secret
from . import state as st

log = logging.getLogger("titan.venues")

CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT", "ADAUSDT",
                  "AVAXUSDT", "LTCUSDT"]
FOREX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
                 "XAUUSD"]

# Shared forex price feed for paper trading: filled by a connected forex demo adapter (cTrader spot
# events / MT5 ticks) or by prices carried in authorised signals. Never synthesised.
FOREX_PAPER_FEED = PushFeed(max_age_s=60)


def ensure_default_portfolios(s: Session) -> list[Portfolio]:
    cfg = get_settings()
    out = []
    for name, market, venue, ccy, cap in [
        ("Forex Paper", Market.FOREX, "paper_forex", "EUR", cfg.forex_initial_capital_eur),
        ("Crypto Paper", Market.CRYPTO, "paper_crypto_usdm", "USDT", cfg.crypto_initial_capital_usdt),
    ]:
        p = s.scalar(select(Portfolio).where(Portfolio.name == name))
        if p is None:
            p = Portfolio(name=name, market=market, venue=venue, mode=TradingMode.PAPER, base_currency=ccy,
                          initial_capital=cap, high_water_mark=cap, risk_limits={})
            s.add(p)
            s.flush()
            audit(s, "system", "portfolio.created", f"portfolio:{p.id}", name=name, capital=cap, mode="paper")
        out.append(p)
    return out


def credential(s: Session, cred_id: int) -> dict:
    c = s.get(Credential, cred_id)
    if c is None:
        raise LookupError("credential not found")
    return decrypt_secret(c.ciphertext, f"{c.venue}|{c.environment}|{c.label}")


def live_allowed(s: Session, p: Portfolio) -> tuple[bool, str]:
    if not get_settings().live_trading_enabled:
        return False, "live trading is disabled on the server (TITAN_LIVE_TRADING_ENABLED=false)"
    appr = st.get(s, st.LIVE_APPROVALS, {}).get(str(p.id))
    if not appr:
        return False, "no owner approval recorded for live trading on this portfolio"
    from datetime import datetime, timezone

    if datetime.fromisoformat(appr["expires_at"]) < datetime.now(timezone.utc):
        return False, "live approval expired"
    return True, "approved"


def build_adapter(s: Session, p: Portfolio) -> ExecutionAdapter:
    if p.mode == TradingMode.LIVE:
        ok, why = live_allowed(s, p)
        if not ok:
            raise PermissionError(why)
    if p.mode == TradingMode.PAPER:
        if p.market == Market.CRYPTO:
            product = {"paper_crypto_spot": cm.SPOT, "paper_crypto_coinm": cm.COINM}.get(p.venue, cm.USDM)
            feed = BinanceRestFeed(get_settings().binance_public_data_url, "spot")
            filters = {}
            try:
                info = feed.exchange_info(CRYPTO_SYMBOLS)
                # paper futures use spot exchange filters/prices as proxy when futures hosts are unreachable
                filters = {x["symbol"]: cm.SymbolFilters.from_exchange_info("spot", x) for x in info["symbols"]}
            except Exception as e:
                log.warning("could not load Binance filters for paper portfolio: %s", e)
            return PaperBroker(p.venue, "crypto", p.base_currency, float(p.initial_capital), feed, SimParams(),
                               product=product, filters=filters, session_factory=session_scope)
        return PaperBroker(p.venue, "forex", p.base_currency, float(p.initial_capital), FOREX_PAPER_FEED, SimParams(),
                           session_factory=session_scope)
    if p.credential_id is None:
        raise LookupError(f"portfolio {p.name} has no credential configured")
    cred = credential(s, p.credential_id)
    env = "live" if p.mode == TradingMode.LIVE else "demo"
    if p.venue.startswith("binance_"):
        from ..adapters.binance.adapter import BinanceAdapter
        from ..adapters.binance.client import BinanceClient

        product = p.venue.split("_", 1)[1]
        ad = BinanceAdapter(BinanceClient(product, env, cred["api_key"], cred["api_secret"]))
        ad.load_filters(CRYPTO_SYMBOLS if product != "coinm" else None)
        return ad
    if p.venue == "ctrader":
        from ..adapters.forex.ctrader import CTraderAdapter, CTraderConnection

        from ..adapters.forex.ctrader_auth import resolve_account_id
        conn = CTraderConnection(env, cred["client_id"], cred["client_secret"], cred["access_token"],
                                 resolve_account_id(cred, env))
        ad = CTraderAdapter(conn, env, p.base_currency)
        ad.connect(FOREX_SYMBOLS)
        ad.listeners.append(lambda ev: None)
        return ad
    if p.venue == "mt5":
        from ..adapters.forex.mt5 import MT5BridgeAdapter

        ad = MT5BridgeAdapter(cred["url"], cred["secret"], env, cred.get("symbol_map") or {}, alt_urls=cred.get("alt_urls"))
        ad.sync_specs(FOREX_SYMBOLS)
        return ad
    raise ValueError(f"unknown venue {p.venue}")
