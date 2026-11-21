"""Binance contract mathematics (spot, USDⓈ-M linear, COIN-M inverse). Never used for forex.

Filters are always loaded at runtime from the venue's exchangeInfo; nothing is hardcoded.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

SPOT, USDM, COINM = "spot", "usdm", "coinm"


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    product: str              # spot | usdm | coinm
    base: str
    quote: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    market_step_size: Decimal
    market_min_qty: Decimal
    market_max_qty: Decimal
    min_notional: Decimal     # quote asset (spot / usdm); 0 when not applicable
    contract_size: Decimal    # COIN-M: USD value of one contract; 1 otherwise
    margin_asset: str
    status: str

    @classmethod
    def from_exchange_info(cls, product: str, s: dict) -> "SymbolFilters":
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        mlot = f.get("MARKET_LOT_SIZE", lot)
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        min_notional = notional.get("minNotional") or notional.get("notional") or "0"
        D = lambda v, d="0": Decimal(str(v if v not in (None, "") else d))  # noqa: E731
        # Spot MARKET_LOT_SIZE often reports 0 for step/min/max: fall back to LOT_SIZE values.
        mlot = {k: (mlot.get(k) if mlot.get(k) not in (None, "") and Decimal(str(mlot[k])) != 0 else lot.get(k))
                for k in ("stepSize", "minQty", "maxQty")}
        return cls(
            symbol=s["symbol"], product=product,
            base=s.get("baseAsset", ""), quote=s.get("quoteAsset", ""),
            tick_size=D(f.get("PRICE_FILTER", {}).get("tickSize"), "0.00000001"),
            step_size=D(lot.get("stepSize"), "0.00000001"),
            min_qty=D(lot.get("minQty")), max_qty=D(lot.get("maxQty"), "9e15"),
            market_step_size=D(mlot.get("stepSize") or lot.get("stepSize"), "0.00000001"),
            market_min_qty=D(mlot.get("minQty") or lot.get("minQty")),
            market_max_qty=D(mlot.get("maxQty") or lot.get("maxQty"), "9e15"),
            min_notional=D(min_notional),
            contract_size=D(s.get("contractSize"), "1"),
            margin_asset=s.get("marginAsset") or s.get("quoteAsset", ""),
            status=s.get("status") or s.get("contractStatus", ""),
        )

    def trading(self) -> bool:
        return self.status in ("TRADING", "")


def q_down(x: float | Decimal, step: Decimal) -> Decimal:
    x = Decimal(str(x))
    if step <= 0:
        return x
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def q_price(x: float | Decimal, tick: Decimal) -> Decimal:
    x = Decimal(str(x))
    return (x / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick if tick > 0 else x


def fmt(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return s


@dataclass
class CryptoSize:
    quantity: Decimal          # base units (spot/usdm) or contracts (coinm)
    risk_usd: float
    notional_usd: float
    reason: str | None = None


def size_by_risk(f: SymbolFilters, side: str, entry: float, sl: float, risk_usd: float,
                 market: bool = True) -> CryptoSize:
    dist = abs(entry - sl)
    if dist <= 0 or entry <= 0:
        return CryptoSize(Decimal(0), 0, 0, "stop-loss distance is zero")
    if f.product == SPOT and side != "buy":
        return CryptoSize(Decimal(0), 0, 0, "spot account cannot open short positions")
    step = f.market_step_size if market else f.step_size
    min_q = f.market_min_qty if market else f.min_qty
    max_q = f.market_max_qty if market else f.max_qty
    if f.product == COINM:
        # Inverse contract: PnL(coin) = n * cs * (1/entry - 1/exit) * dir ; risk in USD at the stop
        cs = float(f.contract_size)
        per_contract_usd = cs * abs(1 / entry - 1 / sl) * sl
        qty = q_down(risk_usd / per_contract_usd, step if step > 0 else Decimal(1))
        risk = float(qty) * per_contract_usd
        notional = float(qty) * cs
    else:
        qty = q_down(risk_usd / dist, step)
        risk = float(qty) * dist
        notional = float(qty) * entry
    qty = min(qty, max_q)
    if qty < min_q or qty <= 0:
        return CryptoSize(Decimal(0), 0, 0, f"minimum quantity {fmt(min_q)} exceeds the risk budget "
                                            f"({risk_usd:.2f} USD)")
    if f.product != COINM and f.min_notional > 0 and Decimal(str(notional)) < f.min_notional:
        need_qty = f.min_notional / Decimal(str(entry))
        need_risk = float(need_qty) * dist
        return CryptoSize(Decimal(0), 0, 0, f"order notional {notional:.2f} < minimum {fmt(f.min_notional)}; "
                                            f"meeting it would risk {need_risk:.2f} USD > {risk_usd:.2f}")
    return CryptoSize(qty, risk, notional)


def pnl_usd(product: str, side: str, qty: float, entry: float, exit_: float, contract_size: float = 1.0) -> float:
    d = 1 if side == "buy" else -1
    if product == COINM:
        coin = d * qty * contract_size * (1 / entry - 1 / exit_)
        return coin * exit_
    return d * qty * (exit_ - entry)


def isolated_liquidation_price(side: str, entry: float, leverage: float, mmr: float = 0.004) -> float:
    """Approximate isolated-margin liquidation price for a linear contract (fees ignored).

    Used only as a protective pre-trade check (stop must be well inside liquidation);
    the venue's reported liquidationPrice always takes precedence once a position exists.
    """
    if side == "buy":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)
