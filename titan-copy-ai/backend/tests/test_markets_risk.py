from decimal import Decimal

import pytest

from titan.markets import crypto, forex
from titan.risk.engine import (
    OpenExposure, PortfolioState, RiskLimits, RiskOverlay, SizedTrade, TradeIntent,
    evaluate_portfolio, post_size, pre_trade,
)

MIDS = {"EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 150.0, "EURGBP": 0.85, "XAUUSD": 2400.0,
        "USDCHF": 0.88, "USDCAD": 1.36, "AUDUSD": 0.66, "NZDUSD": 0.60}


# ----------------------------------------------------------------------------- forex
def test_esma_leverage_caps():
    assert forex.spec("EURUSD").max_leverage == 30
    assert forex.spec("AUDUSD").max_leverage == 20      # AUD is not an ESMA major currency
    assert forex.spec("XAU/USD").max_leverage == 20
    assert forex.normalize_symbol("eurusd.pro") == "EURUSD"


def test_pip_value_and_conversion():
    eurusd = forex.spec("EURUSD")
    # 1 lot EURUSD: 10 USD per pip -> 10/1.08 EUR
    assert forex.pip_value(eurusd, 1, "EUR", MIDS) == pytest.approx(10 / 1.08)
    usdjpy = forex.spec("USDJPY")
    assert forex.pip_value(usdjpy, 1, "USD", MIDS) == pytest.approx(1000 / 150)
    # EUR via USD cross for JPY quote
    assert forex.convert(15000, "JPY", "EUR", MIDS) == pytest.approx(100 / 1.08)


def test_forex_sizing_500_eur():
    eurusd = forex.spec("EURUSD")
    # 0.5% of 500 EUR = 2.50 EUR, 20 pip stop -> ~0.0135 lots -> 0.01 lots
    r = forex.size_by_risk(eurusd, 1.0800, 1.0780, 2.5, "EUR", MIDS)
    assert r.lots == 0.01 and r.risk_amount <= 2.5
    gold = forex.spec("XAUUSD")
    r = forex.size_by_risk(gold, 2400, 2395, 2.5, "EUR", MIDS)   # 0.01 lot risks ~4.63 EUR
    assert r.lots == 0 and "minimum volume" in r.reason


def test_currency_legs():
    assert forex.currency_legs("EURUSD", "buy", 2) == {"EUR": 2, "USD": -2}
    assert forex.currency_legs("USDJPY", "sell", 1) == {"USD": -1, "JPY": 1}


# ----------------------------------------------------------------------------- crypto
FUT_INFO = {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT",
            "status": "TRADING", "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
                {"filterType": "MIN_NOTIONAL", "notional": "100"}]}


def test_usdm_sizing_and_min_notional():
    f = crypto.SymbolFilters.from_exchange_info("usdm", FUT_INFO)
    r = crypto.size_by_risk(f, "buy", 90000, 89100, 2.5)  # 1% stop: 2.5/900 = 0.00277 -> 0.002
    assert r.quantity == Decimal("0.002") and r.risk_usd == pytest.approx(1.8)
    assert r.notional_usd < 200
    # tighter stop gives notional above 100 -> ok; very wide stop -> notional below min
    r2 = crypto.size_by_risk(f, "buy", 90000, 81000, 2.5)
    assert r2.quantity == 0 and "minimum" in r2.reason


def test_coinm_inverse_sizing():
    info = {"symbol": "BTCUSD_PERP", "baseAsset": "BTC", "quoteAsset": "USD", "marginAsset": "BTC",
            "contractSize": 100, "contractStatus": "TRADING", "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "1000000"}]}
    f = crypto.SymbolFilters.from_exchange_info("coinm", info)
    r = crypto.size_by_risk(f, "buy", 90000, 89100, 2.5)  # ~1.01 USD per contract -> 2 contracts
    assert r.quantity == 2
    loss = crypto.pnl_usd("coinm", "buy", 2, 90000, 89100, 100)
    assert loss == pytest.approx(-r.risk_usd, rel=1e-6)


def test_spot_cannot_short():
    info = dict(FUT_INFO, filters=FUT_INFO["filters"][:3] + [{"filterType": "NOTIONAL", "minNotional": "5"}])
    f = crypto.SymbolFilters.from_exchange_info("spot", info)
    assert "short" in crypto.size_by_risk(f, "sell", 90000, 91000, 2.5).reason


# ----------------------------------------------------------------------------- risk
def state(**kw):
    base = dict(market="forex", equity=500, balance=500, day_start_equity=500, week_start_equity=500,
                high_water_mark=500)
    base.update(kw)
    return PortfolioState(**base)


L = RiskLimits.for_market("forex")


def intent(**kw):
    base = dict(symbol="EURUSD", side="buy", entry=1.08, sl=1.078, trader_id=1, currencies=("EUR", "USD"))
    base.update(kw)
    return TradeIntent(**base)


def test_budget_is_half_percent():
    d = pre_trade(intent(), state(), L)
    assert d.approved and d.budget == pytest.approx(2.5)


@pytest.mark.parametrize("sl,msg", [(None, "missing stop-loss"), (1.081, "wrong side"), (1.0, "exceeds")])
def test_stop_loss_validation(sl, msg):
    d = pre_trade(intent(sl=sl), state(), L)
    assert not d.approved and any(msg in r for r in d.reasons)


def test_daily_limit_blocks_and_halts():
    st = state(equity=489.9)  # -2.02%
    d = pre_trade(intent(), st, L)
    assert not d.approved and any("daily loss" in r for r in d.reasons)
    assert evaluate_portfolio(st, L).new_status == "halted_daily"


def test_max_drawdown_requires_review():
    st = state(equity=449, day_start_equity=449, week_start_equity=449, high_water_mark=500)
    a = evaluate_portfolio(st, L)
    assert a.new_status == "review_required" and a.notify_kind == "max_drawdown"


def test_drawdown_reduces_risk_never_increases():
    d0 = pre_trade(intent(), state(), L)
    st = state(equity=480, day_start_equity=480, week_start_equity=480, high_water_mark=500)  # 4% DD
    d1 = pre_trade(intent(), st, L)
    assert d1.multipliers["drawdown"] == 0.75
    assert d1.budget < d0.budget


def test_portfolio_and_trader_headroom():
    exp = [OpenExposure("GBPUSD", "buy", 2.5, 1000, 40, trader_id=1),
           OpenExposure("USDJPY", "buy", 2.5, 1000, 40, trader_id=2),
           OpenExposure("AUDUSD", "buy", 2.0, 1000, 40, trader_id=3)]
    d = pre_trade(intent(trader_id=4, symbol="USDCAD", currencies=("USD", "CAD")), state(exposures=exp), L)
    assert d.approved and d.budget == pytest.approx(0.5)  # 1.5% * 500 = 7.5 - 7.0 open
    d = pre_trade(intent(trader_id=1, symbol="EURGBP", currencies=("EUR", "GBP")), state(exposures=exp), L)
    assert not d.approved and "trader" in d.reasons[0]


def test_currency_correlation_blocks():
    exp = [OpenExposure("EURUSD", "buy", 2.5, 1000, 40, 1, {"EUR": 2.5, "USD": -2.5}),
           OpenExposure("GBPUSD", "buy", 2.4, 1000, 40, 2, {"GBP": 2.4, "USD": -2.4})]
    t = SizedTrade(intent(trader_id=3, symbol="AUDUSD", currencies=("AUD", "USD")), 2.0, 1000, 40,
                   {"AUD": 2.0, "USD": -2.0})
    d = post_size(t, state(exposures=exp), L, budget=2.5)
    assert not d.approved and any("correlated USD" in r for r in d.reasons)
    # a hedging trade that *reduces* USD short exposure is allowed
    t2 = SizedTrade(intent(trader_id=3, symbol="USDCHF", currencies=("USD", "CHF")), 0.1, 1000, 40,
                    {"USD": 0.1, "CHF": -0.1})
    assert post_size(t2, state(exposures=exp), L, budget=2.5).approved


def test_overlay_blocks_and_kill_switch():
    o = RiskOverlay(blocked_currencies={"USD": "FOMC in 10 minutes"})
    assert not pre_trade(intent(), state(), L, o).approved
    assert not pre_trade(intent(), state(global_kill_switch=True), L).approved


def test_limits_can_only_be_tightened():
    t = RiskLimits().tightened({"risk_per_trade_pct": 5.0, "max_drawdown_pct": 8, "require_stop_loss": False})
    assert t.risk_per_trade_pct == 0.5 and t.max_drawdown_pct == 8 and t.require_stop_loss is True
    with pytest.raises(ValueError):
        RiskLimits().tightened({"daily_loss_pct": 0})


def test_no_averaging_into_losing_position():
    exp = [OpenExposure("EURUSD", "buy", 2.0, 1000, 40, trader_id=1)]
    d = pre_trade(intent(), state(exposures=exp), L)
    assert not d.approved and "averaging" in " ".join(d.reasons)
