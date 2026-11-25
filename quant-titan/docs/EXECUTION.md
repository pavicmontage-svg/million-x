# Execution

* **Venues**: `PaperExchange` (default; live data, simulated fills) and `BinanceFutures` (TESTNET or LIVE),
  behind one interface, so the managers and the reconciler are identical for all venues.
* **Conditional orders** (STOP_MARKET / TAKE_PROFIT_MARKET) use Binance's Algo service
  (`POST /fapi/v1/algoOrder`, `algoType=CONDITIONAL`, `triggerPrice`), required since Binance's December 2025
  change; `execution.conditional_endpoint=legacy` switches back to `/fapi/v1/order`.
* **One-way mode** is required (hedge mode is refused). Margin type ISOLATED by default; leverage is the lowest
  integer that fits the margin.

## Order lifecycle (`execution/order_manager.py`)
1. Exposure-increasing order → verify the Risk Manager's signed approval (symbol, side, qty, price band, expiry, single use).
2. Deterministic client id per decision and leg (`qt<decision><leg>`), so retries cannot duplicate.
3. Timeout / 5xx → the result is *unknown*: the order is **queried** first; re-sent only if it provably does not exist.
4. Market / IOC orders are polled until a terminal status — a request being sent is never treated as a fill.

## Position lifecycle (`execution/position_manager.py`)
Entry → **protective stop placed and verified** (if it cannot be placed, the position is flattened immediately and
a critical alert is raised) → partial take-profits → break-even after TP1 → chandelier trailing for the runner
(new stop placed *before* the old one is cancelled, never unprotected) → time stop / deterioration exit.

## Reconciliation (`execution/reconciler.py`)
At start-up and every `reconcile_interval_s`: internal vs exchange quantities, missing protective stops
(re-placed), unexpected exchange positions (policy `alert_only | protect | flatten`), orphan platform orders
(cancelled). Unresolvable mismatch or 3 consecutive unreachable cycles → new orders blocked, SAFE_STATE, alert.

## Paper simulation (`execution/paper.py`)
Book-walk fills, partial IOC fills, resting limits (maker fee), trigger-based stops with gap-through fills, taker
and maker fees, funding at funding timestamps, isolated liquidation, optional latency, failure injection
(timeouts with and without the request being applied, rejections, outages). Simulated fills are not guaranteed
real fills.
