# Integration research (verified 21 Nov 2026)

Rule followed throughout: **no invented APIs**. Every endpoint used in code was taken from the official
documentation or from the platform's own published client code. Public monitoring, authorised signal
ingestion and broker execution are kept strictly separate. A publicly visible account is never treated as
copyable.

## Summary

| Platform | Official programmatic access | TITAN implementation | Copy execution |
|---|---|---|---|
| **Binance Spot / USDⓈ-M / COIN-M** | REST + WebSocket, Demo Trading hosts | `adapters/binance/` (demo default, live gated) | Yes – on the owner's own account |
| **cTrader** | Open API (OAuth, JSON over WebSocket :5036) | `adapters/forex/ctrader.py` | Yes – on accounts the owner authorised |
| **MetaTrader 5** | Official `MetaTrader5` Python package (needs a running terminal) | `bridges/mt5_bridge.py` + `adapters/forex/mt5.py` | Yes – on the owner's terminal |
| **Myfxbook** | Myfxbook API (session-based JSON) | `discovery/sources.py: MyfxbookSource` | No (monitoring) |
| **Darwinex** | DARWIN Info API (Bearer token) | `discovery/sources.py: DarwinexInfoSource` | No (monitoring) |
| **ZuluTrade** | No documented public API found | Manual history import | No – copy inside ZuluTrade |
| **MQL5 Signals** | No public API; copying only inside MT4/MT5 terminals | Manual history import | No – subscribe inside MT5 |
| **cTrader Copy listings** | No strategy-listing endpoint in Open API | Manual history import | Only via an authorised source account |
| **Myfxbook AutoTrade** | No public API found | — | No |

## Binance

Source: developers.binance.com (Spot Demo Mode general info; USDⓈ-M and COIN-M general info; New Algo Order).

| Product | Demo REST | Live REST | Demo market streams |
|---|---|---|---|
| Spot | `https://demo-api.binance.com` (`/api/v3/...`) | `https://api.binance.com` | `wss://demo-stream.binance.com/ws` |
| USDⓈ-M | `https://demo-fapi.binance.com` (`/fapi/...`) | `https://fapi.binance.com` | `wss://demo-fstream.binance.com` |
| COIN-M | `https://demo-dapi.binance.com` (`/dapi/...`) | `https://dapi.binance.com` | `wss://demo-dstream.binance.com` |

* Signing: `X-MBX-APIKEY` header + HMAC-SHA256 signature; `recvWindow` 5000 ms; timestamp corrected by measured server-time offset. (Binance also supports Ed25519/RSA keys.)
* USDⓈ-M conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET) are placed through **`POST /fapi/v1/algoOrder`** with `algoType=CONDITIONAL`, `triggerPrice`, `clientAlgoId`; queried/cancelled via `GET/DELETE /fapi/v1/algoOrder`.
* Spot protection: OCO order list (`aboveType=LIMIT_MAKER` take-profit, `belowType=STOP_LOSS`) or a single `STOP_LOSS` order.
* Futures user data: `POST/PUT /fapi/v1/listenKey` (60-min keep-alive). Spot user data is delivered through the WebSocket API subscription (no Spot listenKey documented for Demo Mode).
* Demo Mode: "Realistic market data is not equal to real market data"; filters and rate limits are the same as live; balances can be reset in the UI.
* **Open points to verify on first connection** (the docs were inconsistent): some USDⓈ-M endpoint blocks list `testnet.binancefuture.com` as the test host while the general-info page lists `demo-fapi.binance.com`; the demo host for the futures listenKey is not stated. TITAN uses `demo-fapi`/`demo-dapi`; `python -m titan.cli binance-check` reports reachability.
* **Connectivity from this build environment:** every Binance trading host returned **HTTP 451** (geo-restricted location). The public market-data host `data-api.binance.vision` worked and was used to verify exchange filters, prices and klines for all 10 symbols. Real demo-order verification must be done from a server in a permitted region.
* **EEA / Croatia:** multiple 2026 reports state that since **1 July 2026 Binance stopped new orders, deposits and sign-ups for EEA residents** after withdrawing its MiCA application (Binance is not in ESMA's CASP register as of Nov 2026). Live Binance trading from Croatia is therefore very likely unavailable; Demo Trading remains usable for testing. Confirm on Binance's own announcements. The exchange layer is a separate adapter, so a MiCA-licensed EU venue can be added later.

## cTrader Open API

Sources: help.ctrader.com/open-api (getting started, proxies/endpoints, account authentication, messages) and `spotware/openapi-proto-messages` (payload type IDs and message fields were taken directly from the `.proto` files).

* Hosts: `demo.ctraderapi.com` / `live.ctraderapi.com`; **port 5036 = JSON**, 5035 = Protobuf; TCP or WebSocket.
* OAuth: `https://id.ctrader.com/my/settings/openapi/grantingaccess/?client_id=…&redirect_uri=…&scope=trading&product=web` → `GET https://openapi.ctrader.com/apps/token` (access token ~30 days, refresh token).
* Sequence: `ProtoOAApplicationAuthReq (2100)` → `ProtoOAAccountAuthReq (2102)`.
* Orders: `ProtoOANewOrderReq (2106)` – for MARKET orders absolute SL/TP are **not supported**; `relativeStopLoss`/`relativeTakeProfit` (1/100000 price units) are used, then re-anchored with `ProtoOAAmendPositionSLTPReq (2110)`. Close: `ProtoOAClosePositionReq (2111)`. State: `ProtoOAReconcileReq (2124)`, `ProtoOATraderReq (2121)`. Prices: `ProtoOASubscribeSpotsReq (2127)` → `ProtoOASpotEvent (2131)`.
* Units: volume in 0.01 of a unit; `lotSize`, `minVolume`, `stepVolume` from `ProtoOASymbol`; money scaled by `10^moneyDigits`.
* Rate limit: 50 req/s (non-historical) per connection.
* **No API to list public cTrader Copy strategies** (Pepperstone "Alpha Trader", "Steady Eddie Algo", etc.). They can only be evaluated from imported history; copying them happens in cTrader Copy itself, or via a source account the owner controls.

## MetaTrader 5

Source: mql5.com/en/docs/python_metatrader5. `pip install MetaTrader5`; functions `initialize`, `account_info`, `positions_get`, `symbol_info`, `symbol_info_tick`, `order_send`, `history_deals_get` talk to a **running MT5 terminal** (in practice Windows). TITAN therefore ships a small self-hosted HMAC-authenticated bridge that runs next to the owner's terminal; the MT5 password never leaves that machine and is never shared with a third-party copier.

## Myfxbook

Source: myfxbook.com/api (page returned 403 to automated fetches; method names and parameters were confirmed from the published `myfxbook-api-client` package and search results). Base `https://www.myfxbook.com/api/<method>.json`, session from `login`. Methods used: `get-my-accounts`, `get-watched-accounts`, `get-open-trades`, `get-history` (latest trades only), `get-daily-gain` (start/end `yyyy-MM-dd`). Watched accounts expose summary fields only. Myfxbook's published `drawdown` is stored as reported and treated as balance-type drawdown by the ranking engine.

## Darwinex

Sources: `darwinex/darwinexapis` (official package), help.darwinex.com. DARWIN Info API: `https://api.darwinex.com/darwininfo/<version>` with `/products?status=&page=&per_page=`, `/products/{name}/history/quotes`, `/products/{name}/history/badges`, `/products/{name}/candles/{tf}`; Bearer token (data-API tokens ~6 months); Info API 10 requests/minute. api.darwinex.com was not reachable from the build environment (503/connection reset), so live verification is pending. DARWIN quotes are Darwinex's **risk-normalised index**, not the raw account – this is labelled in the UI.

## ZuluTrade and MQL5 Signals

No documented public API for leader statistics or trade copying was found (zulutrade.com navigation/help centre; mql5.com/en/signals says subscription copying requires the MetaTrader terminal). TITAN does **not** scrape these sites. The research watchlist (Carmine Quaranta real1, Money Pile, FIRE GC, Alpha Trader, Steady Eddie Algo, WL Tactical Growth, Stable Strategy) is seeded with **all metrics UNKNOWN** until the owner imports a verified history (CSV) or connects an official source.

## Regulation for a Croatian retail client (summary, not legal advice)

* EU retail CFD rules (ESMA product intervention, applied by national regulators incl. HANFA): leverage caps **30:1 major FX pairs, 20:1 non-major FX and gold**, 50% margin close-out per account, **negative balance protection**, standardised risk warning. TITAN encodes the 30:1 / 20:1 caps in `markets/forex.py` and runs far below them (portfolio leverage cap 10x, margin usage cap 35%).
* Use only brokers authorised in the EU/EEA (e.g. CySEC/HANFA/other NCA register) that offer cTrader or MT5 accounts to Croatian residents; verify the broker's registration in the ESMA/NCA registers and its negative-balance-protection terms before connecting.
* Copy-trading services can be portfolio-management/investment-advice activities; TITAN copies only into the owner's own accounts from sources the owner is authorised to use.
* Crypto: MiCA applies; see the Binance note above.
