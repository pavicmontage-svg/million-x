"""Binance execution adapters (Spot, USDⓈ-M, COIN-M). Demo by default; live is gated elsewhere.

Position model
* USDⓈ-M / COIN-M: one-way mode required (checked). Because one-way mode nets positions per
  symbol, the adapter allows one copied position per symbol per portfolio
  (`multiple_positions_per_symbol = False`); the risk engine enforces it.
  Protective orders are reduce-only STOP_MARKET / TAKE_PROFIT_MARKET with explicit quantity,
  triggered on MARK_PRICE. USDⓈ-M sends them to POST /fapi/v1/algoOrder (algoType=CONDITIONAL);
  COIN-M uses POST /dapi/v1/order with stopPrice.
* Spot: long only. Protection is an OCO SELL (LIMIT_MAKER take-profit + STOP_LOSS) or a single
  STOP_LOSS order when there is no take-profit.
* If the stop-loss cannot be placed, the entry is closed immediately with a market order.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from ...markets import crypto as cm
from ...models import OrderStatus
from ..base import (AccountSnapshot, AdapterError, AdapterUnavailable, ExecutionAdapter, OpenRequest, OrderResult,
                    VenuePosition)
from .client import BinanceAPIError, BinanceClient

log = logging.getLogger("titan.binance")

STATUS = {"NEW": OrderStatus.ACKED, "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED, "FILLED": OrderStatus.FILLED,
          "CANCELED": OrderStatus.CANCELED, "EXPIRED": OrderStatus.CANCELED, "REJECTED": OrderStatus.REJECTED,
          "EXPIRED_IN_MATCH": OrderStatus.CANCELED}


class BinanceAdapter(ExecutionAdapter):
    market = "crypto"

    def __init__(self, client: BinanceClient, max_leverage: int = 3):
        self.c = client
        self.product = client.product
        self.mode = client.env
        self.venue = f"binance_{self.product}"
        self.account_currency = "USDT" if self.product != "coinm" else "USD"
        self.multiple_positions_per_symbol = self.product == "spot"
        self.max_leverage = max_leverage
        self.filters: dict[str, cm.SymbolFilters] = {}
        self._protect: dict[str, dict] = {}   # position ref -> protective order client ids

    # ------------------------------------------------------------------ metadata
    def load_filters(self, symbols: list[str] | None = None) -> dict[str, cm.SymbolFilters]:
        info = self.c.exchange_info()
        for s in info.get("symbols", []):
            if symbols and s["symbol"] not in symbols:
                continue
            self.filters[s["symbol"]] = cm.SymbolFilters.from_exchange_info(self.product, s)
        return self.filters

    def f(self, symbol: str) -> cm.SymbolFilters:
        if symbol not in self.filters:
            self.load_filters()
        if symbol not in self.filters:
            raise AdapterError(f"{symbol} is not listed on {self.venue}")
        if not self.filters[symbol].trading():
            raise AdapterError(f"{symbol} status is {self.filters[symbol].status}")
        return self.filters[symbol]

    def preflight(self) -> dict:
        """Connectivity + account configuration checks (run before enabling a portfolio)."""
        out = {"venue": self.venue, "env": self.mode}
        out["time_offset_ms"] = self.c.sync_time()
        acct = self.account()
        out["equity"] = acct.equity
        if self.product in ("usdm", "coinm"):
            dual = self.c.position_mode_dual()
            out["hedge_mode"] = dual
            if dual:
                raise AdapterError("account is in Hedge Mode; switch to One-way Mode for copy trading")
        if self.mode == "live":
            r = self.c.api_restrictions()
            out["api_restrictions"] = {k: r.get(k) for k in ("enableWithdrawals", "ipRestrict", "enableFutures",
                                                             "enableSpotAndMarginTrading")}
            if r.get("enableWithdrawals"):
                raise AdapterError("API key has WITHDRAWAL permission enabled - refused. Create a key without it.")
            if not r.get("ipRestrict"):
                out["warning"] = "API key is not IP-restricted; restrict it to the server IP"
        return out

    # ------------------------------------------------------------------ account / positions
    def account(self) -> AccountSnapshot:
        a = self.c.account()
        if self.product == "spot":
            usdt = next((b for b in a.get("balances", []) if b["asset"] == "USDT"), {"free": "0", "locked": "0"})
            bal = float(usdt["free"]) + float(usdt["locked"])
            pos = self.positions(a)
            eq = bal + sum(p.quantity * p.entry_price + p.unrealized for p in pos)
            return AccountSnapshot("USDT", bal, eq, 0.0, float(usdt["free"]), pos, raw={"canTrade": a.get("canTrade")})
        if self.product == "usdm":
            pos = self.positions()
            return AccountSnapshot("USDT", float(a.get("totalWalletBalance", 0)), float(a.get("totalMarginBalance", 0)),
                                   float(a.get("totalInitialMargin", 0)), float(a.get("availableBalance", 0)), pos)
        # COIN-M: balances are per coin; value them in USD at the perpetual's mid price.
        pos = self.positions()
        bal = eq = margin = 0.0
        for asset in a.get("assets", []):
            wb = float(asset.get("walletBalance", 0) or 0)
            if wb == 0:
                continue
            bid, ask = self.quote(f"{asset['asset']}USD_PERP")
            px = (bid + ask) / 2
            bal += wb * px
            eq += float(asset.get("marginBalance", wb) or wb) * px
            margin += float(asset.get("initialMargin", 0) or 0) * px
        return AccountSnapshot("USD", bal, eq, margin, eq - margin, pos, raw={"assets": a.get("assets", [])})

    def positions(self, spot_account: dict | None = None) -> list[VenuePosition]:
        if self.product == "spot":
            a = spot_account or self.c.account()
            out = []
            for b in a.get("balances", []):
                qty = float(b["free"]) + float(b["locked"])
                sym = b["asset"] + "USDT"
                if b["asset"] in ("USDT",) or qty <= 0 or sym not in self.filters:
                    continue
                f = self.filters[sym]
                if Decimal(str(qty)) < f.min_qty:
                    continue
                bid, _ = self.quote(sym)
                out.append(VenuePosition(sym, sym, "buy", qty, bid, unrealized=0.0))
            return out
        out = []
        for p in self.c.position_risk():
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            out.append(VenuePosition(p["symbol"], p["symbol"], "buy" if amt > 0 else "sell", abs(amt),
                                     float(p["entryPrice"]), unrealized=float(p.get("unRealizedProfit", 0)),
                                     liquidation_price=float(p.get("liquidationPrice") or 0) or None,
                                     leverage=float(p.get("leverage") or 0) or None))
        return out

    def quote(self, symbol: str) -> tuple[float, float]:
        d = self.c.book_ticker(symbol)
        if isinstance(d, list):
            d = d[0]
        return float(d["bidPrice"]), float(d["askPrice"])

    def size(self, symbol, side, entry, sl, risk_amount):
        r = cm.size_by_risk(self.f(symbol), side, entry, sl, risk_amount)
        if r.reason:
            return 0, 0, 0, 0, {}, r.reason
        q = float(r.quantity)
        lev = self.max_leverage if self.product != "spot" else 1
        return q, r.risk_usd, r.notional_usd, r.notional_usd / lev, {}, None

    # ------------------------------------------------------------------ orders
    def _qty(self, symbol: str, qty: float) -> str:
        f = self.f(symbol)
        return cm.fmt(cm.q_down(qty, f.market_step_size if f.market_step_size > 0 else f.step_size))

    def _px(self, symbol: str, px: float) -> str:
        return cm.fmt(cm.q_price(px, self.f(symbol).tick_size))

    def open_position(self, req: OpenRequest) -> OrderResult:
        f = self.f(req.symbol)
        if self.product == "spot" and req.side != "buy":
            raise AdapterError("spot cannot open short positions")
        qty = self._qty(req.symbol, req.quantity)
        if Decimal(qty) <= 0:
            raise AdapterError("quantity rounds to zero")
        if self.product != "spot":
            lev = max(1, min(self.max_leverage, int(req.leverage or self.max_leverage)))
            self.c.set_isolated(req.symbol)
            self.c.set_leverage(req.symbol, lev)
        try:
            d = self.c.new_order(symbol=req.symbol, side=req.side.upper(), type="MARKET", quantity=qty,
                                 newClientOrderId=req.client_id)
        except AdapterUnavailable:
            raise
        except BinanceAPIError as e:
            return OrderResult(req.client_id, OrderStatus.REJECTED, error=str(e))
        res = self._parse_order(req.client_id, d)
        if res.filled_qty <= 0:
            return res
        res.position_ref = req.symbol
        # ---- protective orders (stop-loss mandatory)
        try:
            res.protection = self._place_protection(req.symbol, req.side, res.filled_qty, req.sl, req.tp, req.client_id)
        except Exception as e:
            log.error("SL placement failed for %s: %s - closing position", req.client_id, e)
            close = self.close_position(VenuePosition(req.symbol, req.symbol, req.side, res.filled_qty,
                                                      res.avg_price or 0), res.filled_qty, f"{req.client_id}-x")
            res.status = OrderStatus.REJECTED
            res.error = f"stop-loss could not be placed ({e}); position closed: {close.status.value}"
            res.protection = {"error": str(e), "emergency_close": close.status.value}
        _ = f
        return res

    def _place_protection(self, symbol, side, qty, sl, tp, cid) -> dict:
        qs = self._qty(symbol, qty)
        exit_side = "SELL" if side == "buy" else "BUY"
        out = {}
        if self.product == "spot":
            if tp:
                self.c.new_oco_sell(symbol, qs, self._px(symbol, tp), self._px(symbol, sl), f"{cid}-p")
                out = {"oco": f"{cid}-p"}
            else:
                self.c.new_order(symbol=symbol, side="SELL", type="STOP_LOSS", quantity=qs,
                                 stopPrice=self._px(symbol, sl), newClientOrderId=f"{cid}-sl")
                out = {"sl": f"{cid}-sl"}
        elif self.product == "usdm":
            self.c.new_algo_order(symbol=symbol, side=exit_side, type="STOP_MARKET", quantity=qs,
                                  triggerPrice=self._px(symbol, sl), reduceOnly="true", workingType="MARK_PRICE",
                                  clientAlgoId=f"{cid}-sl")
            out["sl"] = f"{cid}-sl"
            if tp:
                self.c.new_algo_order(symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET", quantity=qs,
                                      triggerPrice=self._px(symbol, tp), reduceOnly="true", workingType="MARK_PRICE",
                                      clientAlgoId=f"{cid}-tp")
                out["tp"] = f"{cid}-tp"
        else:
            self.c.new_order(symbol=symbol, side=exit_side, type="STOP_MARKET", quantity=qs,
                             stopPrice=self._px(symbol, sl), reduceOnly="true", workingType="MARK_PRICE",
                             newClientOrderId=f"{cid}-sl")
            out["sl"] = f"{cid}-sl"
            if tp:
                self.c.new_order(symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET", quantity=qs,
                                 stopPrice=self._px(symbol, tp), reduceOnly="true", workingType="MARK_PRICE",
                                 newClientOrderId=f"{cid}-tp")
                out["tp"] = f"{cid}-tp"
        self._protect[symbol] = {**out, "base": cid, "seq": 0}
        return out

    def _cancel_protection(self, symbol: str, persisted: dict | None = None):
        p = self._protect.pop(symbol, None) or (persisted or {}).get("new", persisted)
        if not p:
            return
        for k, cid in p.items():
            if k in ("base", "seq"):
                continue
            try:
                if self.product == "spot" and k == "oco":
                    self.c.cancel_order_list(symbol, cid)
                elif self.product == "usdm":
                    self.c.cancel_algo_order(cid)
                else:
                    self.c.cancel_order(symbol, cid)
            except BinanceAPIError as e:
                log.warning("cancel protection %s failed: %s", cid, e)

    def modify_protection(self, pos, sl, tp, client_id):
        old = self._protect.get(pos.symbol) or pos.protection
        self._cancel_protection(pos.symbol, pos.protection)
        new_sl = sl if sl is not None else pos.sl
        new_tp = tp if tp is not None else pos.tp
        if new_sl is None:
            raise AdapterError("cannot remove the stop-loss")
        prot = self._place_protection(pos.symbol, pos.side, pos.quantity, new_sl, new_tp, client_id)
        return OrderResult(client_id, OrderStatus.ACKED, position_ref=pos.ref, protection={"old": old, "new": prot})

    def close_position(self, pos, quantity, client_id):
        qty = self._qty(pos.symbol, min(quantity, pos.quantity))
        full = Decimal(qty) >= Decimal(self._qty(pos.symbol, pos.quantity))
        self._cancel_protection(pos.symbol, pos.protection)
        params = dict(symbol=pos.symbol, side="SELL" if pos.side == "buy" else "BUY", type="MARKET",
                      quantity=qty, newClientOrderId=client_id)
        if self.product != "spot":
            params["reduceOnly"] = "true"
        try:
            d = self.c.new_order(**params)
        except BinanceAPIError as e:
            return OrderResult(client_id, OrderStatus.REJECTED, error=str(e))
        res = self._parse_order(client_id, d)
        res.position_ref = pos.ref
        if not full and pos.sl:
            remaining = pos.quantity - res.filled_qty
            if remaining > 0:
                res.protection = self._place_protection(pos.symbol, pos.side, remaining, pos.sl, pos.tp, f"{client_id}-r")
        return res

    def get_order(self, client_id, symbol):
        try:
            return self._parse_order(client_id, self.c.query_order(symbol, client_id))
        except BinanceAPIError as e:
            if e.code == -2013:   # Order does not exist
                return None
            raise

    def _parse_order(self, cid: str, d: dict) -> OrderResult:
        filled = float(d.get("executedQty", 0) or 0)
        avg = d.get("avgPrice")
        avg = float(avg) if avg not in (None, "", "0", "0.00000") else None
        fee, fee_asset = 0.0, None
        if self.product == "spot" and d.get("fills"):
            notional = sum(float(x["price"]) * float(x["qty"]) for x in d["fills"])
            avg = notional / filled if filled else None
            fee = sum(float(x.get("commission", 0)) for x in d["fills"])
            fee_asset = d["fills"][0].get("commissionAsset")
        elif self.product == "spot" and filled and float(d.get("cummulativeQuoteQty", 0) or 0):
            avg = float(d["cummulativeQuoteQty"]) / filled
        return OrderResult(cid, STATUS.get(d.get("status", ""), OrderStatus.UNKNOWN), str(d.get("orderId")),
                           None, filled, avg, fee, fee_asset, raw=d)
