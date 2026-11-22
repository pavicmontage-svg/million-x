"""Forex / CFD contract mathematics. Never used for crypto.

Defaults below are the common retail-CFD conventions (1 standard lot = 100,000 base units,
gold = 100 oz). Execution adapters MUST override them with the broker's own symbol
specification (contract size, volume min/step/max, digits) before live use -
`InstrumentSpec.from_broker()` is used by the cTrader and MT5 adapters.

Leverage caps follow ESMA retail product-intervention limits (applied by CySEC/HANFA
for EU retail clients incl. Croatia): 30:1 major FX pairs, 20:1 non-major FX and gold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

ESMA_MAJOR_CCY = {"USD", "EUR", "JPY", "GBP", "CAD", "CHF"}


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str          # canonical, e.g. EURUSD
    base: str
    quote: str
    contract_size: float
    pip_size: float
    tick_size: float
    min_volume: float = 0.01   # lots
    volume_step: float = 0.01
    max_volume: float = 50.0
    max_leverage: float = 20.0

    @property
    def is_fx(self) -> bool:
        return self.base != "XAU"

    def from_broker(self, **kw) -> "InstrumentSpec":
        return replace(self, **{k: v for k, v in kw.items() if v is not None})


def _fx(sym: str, pip: float = 0.0001) -> InstrumentSpec:
    base, quote = sym[:3], sym[3:]
    lev = 30.0 if base in ESMA_MAJOR_CCY and quote in ESMA_MAJOR_CCY else 20.0
    return InstrumentSpec(sym, base, quote, 100_000.0, pip, pip / 10, max_leverage=lev)


INSTRUMENTS: dict[str, InstrumentSpec] = {
    s.symbol: s for s in [
        _fx("EURUSD"), _fx("GBPUSD"), _fx("USDJPY", 0.01), _fx("AUDUSD"), _fx("USDCAD"),
        _fx("USDCHF"), _fx("NZDUSD"), _fx("EURGBP"), _fx("EURJPY", 0.01), _fx("GBPJPY", 0.01),
        InstrumentSpec("XAUUSD", "XAU", "USD", 100.0, 0.1, 0.01, max_leverage=20.0),
    ]
}


def normalize_symbol(sym: str) -> str:
    """'EUR/USD', 'eurusd.m', 'EURUSD.pro', 'GOLD' -> canonical."""
    s = sym.upper().replace("/", "").replace("_", "").replace("-", "")
    for suffix in (".PRO", ".M", ".R", ".ECN", ".RAW", "+", ".", "M"):
        if s.endswith(suffix) and s[: -len(suffix)] in INSTRUMENTS:
            s = s[: -len(suffix)]
    s = {"GOLD": "XAUUSD"}.get(s, s)
    if s not in INSTRUMENTS:   # broker suffixes such as EURUSDECN, EURUSDA, GOLDPRO
        for canon in INSTRUMENTS:
            if s.startswith(canon) and len(s) - len(canon) <= 5:
                return canon
        if s.startswith("GOLD") and len(s) <= 9:
            return "XAUUSD"
    return s


def spec(symbol: str) -> InstrumentSpec:
    sym = normalize_symbol(symbol)
    if sym not in INSTRUMENTS:
        raise KeyError(f"unsupported forex instrument {symbol}")
    return INSTRUMENTS[sym]


class ConversionError(LookupError):
    pass


def convert(amount: float, frm: str, to: str, mids: dict[str, float]) -> float:
    """Convert using mid prices of available pairs (direct, inverse, or via USD)."""
    if frm == to:
        return amount
    if (r := mids.get(frm + to)):
        return amount * r
    if (r := mids.get(to + frm)):
        return amount / r
    if "USD" not in (frm, to):
        return convert(convert(amount, frm, "USD", mids), "USD", to, mids)
    raise ConversionError(f"no rate to convert {frm}->{to}")


def value_per_price_unit(sp: InstrumentSpec, lots: float, account_ccy: str, mids: dict[str, float]) -> float:
    """Account-currency P&L for a 1.0 move in price for `lots` lots."""
    return convert(sp.contract_size * lots, sp.quote, account_ccy, mids)


def pip_value(sp: InstrumentSpec, lots: float, account_ccy: str, mids: dict[str, float]) -> float:
    return value_per_price_unit(sp, lots, account_ccy, mids) * sp.pip_size


def pnl(sp: InstrumentSpec, side: str, lots: float, entry: float, exit_: float,
        account_ccy: str, mids: dict[str, float]) -> float:
    direction = 1 if side == "buy" else -1
    return direction * (exit_ - entry) * value_per_price_unit(sp, lots, account_ccy, mids)


def floor_step(x: float, step: float) -> float:
    n = math.floor(x / step + 1e-9)
    return round(n * step, 10)


@dataclass
class SizeResult:
    lots: float
    risk_amount: float          # account ccy actually risked at SL with the rounded size
    reason: str | None = None   # set when no valid size exists


def size_by_risk(sp: InstrumentSpec, entry: float, sl: float, risk_amount: float,
                 account_ccy: str, mids: dict[str, float]) -> SizeResult:
    dist = abs(entry - sl)
    if dist <= 0:
        return SizeResult(0, 0, "stop-loss distance is zero")
    per_lot = dist * value_per_price_unit(sp, 1.0, account_ccy, mids)
    lots = min(floor_step(risk_amount / per_lot, sp.volume_step), sp.max_volume)
    if lots < sp.min_volume:
        need = sp.min_volume * per_lot
        return SizeResult(0, 0, f"minimum volume {sp.min_volume} lots would risk {need:.2f} {account_ccy} "
                                f"> allowed {risk_amount:.2f} {account_ccy}")
    return SizeResult(lots, lots * per_lot)


def margin_required(sp: InstrumentSpec, lots: float, price: float, account_ccy: str,
                    mids: dict[str, float], leverage: float | None = None) -> float:
    lev = min(leverage or sp.max_leverage, sp.max_leverage)
    notional_quote = sp.contract_size * lots * price
    return convert(notional_quote, sp.quote, account_ccy, mids) / lev


def currency_legs(symbol: str, side: str, risk_amount: float) -> dict[str, float]:
    """Signed risk exposure per currency: long EURUSD = +EUR, -USD."""
    sp = spec(symbol)
    d = 1 if side == "buy" else -1
    return {sp.base: d * risk_amount, sp.quote: -d * risk_amount}
