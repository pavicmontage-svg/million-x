# Architecture

```
            ┌──────────────── market data ─────────────────┐
            │ LiveFeed (WebSocket → REST fallback, gap      │   ReplayFeed (history through the
            │ backfill) · depth · aggTrades · mark/funding  │   identical live pipeline)
            └───────────────────────┬──────────────────────┘
                                    │ closed 5m bar (all symbols batched)
                                    ▼
   ┌──────────── 6 independent engines (own config, stats, cooldown, logger) ────────────┐
   │ regime (filter) · vwap · trend · breakout · order_flow (confirmation/standalone)    │
   │ stat_arb (pairs, own risk bucket)                                                   │
   └───────────────────────────────┬─────────────────────────────────────────────────────┘
                                   ▼
   Orchestrator.propose  — regime gate → eligibility → conflicts → order-flow confirmation
                           → cost model (R) → evidence/edge (EdgeBook) → direction mode
                                   ▼
   PortfolioManager.rank — net edge, confidence, de-duplicate correlated exposure
                                   ▼
   RiskManager.review    — ALL checks; sizing; leverage & liquidation; breakers ── VETO
                                   │ signed single-use approval (HMAC)
                                   ▼
   ExecutionSpecialist   — book walk / slippage / order type
                                   ▼
   SEMI_AUTOMATIC: user approval (risk re-checked) │ AUTOMATIC / PAPER: execute
                                   ▼
   OrderManager (verifies approval, idempotent client ids, never assumes fills)
   PositionManager (protective stop first, partial TPs, BE, trailing, time stop)
   Reconciler (internal vs exchange, unexpected positions, orphan orders → SAFE_STATE)
                                   ▼
   Exchange: PaperExchange (simulated fills on live data) │ BinanceFutures TESTNET / LIVE
```

The backtester (`backtest/engine.py`) calls the same `DecisionPipeline`; only the data source and the fill
simulation differ. Both paths are covered by tests (`test_engines.py::test_live_path_equals_backtest_path`,
`test_backtest.py::test_no_lookahead_future_perturbation`).

## Packages

| Package | Responsibility |
|---|---|
| `config.py` | the full typed configuration tree (pydantic v1 API so it also runs on Android); versioned in the DB |
| `core/` | domain types, structured logging with secret redaction |
| `data/` | candles, resampling, HTF alignment, integrity checks, Binance public REST, archive downloader, feeds, universe, microstructure |
| `indicators.py` | causal vectorised indicators (verified by truncation tests) |
| `engines/` | the six engines |
| `orchestrator/` | decision framework, cost model, edge book, shared pipeline |
| `agents/` | quant researcher, market analyst, execution specialist, portfolio manager, performance auditor |
| `risk/` | risk manager, portfolio accounting, circuit breakers, signed approvals |
| `execution/` | exchange interface, Binance client, paper exchange, order/position managers, reconciler, exit rules |
| `backtest/`, `research/` | backtester, metrics, research lab, statistics |
| `storage/` | SQLite / PostgreSQL persistence |
| `security/` | encrypted secrets, auth, sessions, rate limiting |
| `notifications/` | in-app, Android (via the service), Telegram, webhook |
| `runtime/` | the trader loop |
| `api/`, `web/` | REST + WebSocket API, the dashboard |

## Technology choices

* **Python** for all quantitative logic; **numpy/pandas/scipy/statsmodels** pinned to versions that exist as
  Android builds (Chaquopy), so the identical code runs on the phone and on a server.
* **Starlette + uvicorn** (no FastAPI: pydantic v2's Rust core has no Android build).
* **SQLite** by default (also on the phone); **PostgreSQL** via `QT_DATABASE_URL` on servers.
* **No Redis**: a single process with asyncio does not need it.
* **Vanilla JS PWA** (no build step, no third-party scripts) inside an Android **WebView**.
