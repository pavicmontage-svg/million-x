# Architecture

```
Android control centre (Kotlin/Compose)            Cloud backend (Docker)
┌───────────────────────────────┐   HTTPS/WSS   ┌──────────────────────────────────────────────────────┐
│ 17 screens · MVVM · Retrofit  │──────────────▶│ Caddy (TLS) ─▶ API (FastAPI, never trades)           │
│ Room cache · Encrypted prefs  │◀── events ────│     │  writes signals/commands ─┐                     │
│ WorkManager notifications     │               │     ▼                           ▼                     │
└───────────────────────────────┘               │ PostgreSQL 17  ◀──────▶  Redis Streams                │
                                                │     ▲                 titan:signals / titan:commands  │
 Authorised signal sources                      │     │                 titan:events (→ WebSocket)      │
 (HMAC webhooks, own cTrader/MT5/Binance) ─────▶│ ENGINE process (copy engine, risk monitor,            │
                                                │   reconciler, paper broker) ──▶ Execution adapters    │
                                                │ WORKER (Celery + beat): discovery, scoring, agents,   │
                                                │   weekly report, pg_dump backups                      │
                                                └──────────────────────────────────────────────────────┘
                                                Adapters: Paper (SIMULATED) · Binance Spot/USDⓈ-M/COIN-M
                                                          · cTrader Open API · MT5 bridge
```

The phone is only the control centre: the engine keeps running when the app is closed or the phone is offline.

## Services (backend/titan)

| Service | Module |
|---|---|
| Trader Discovery | `discovery/sources.py`, `discovery/service.py` |
| Trader Analytics | `analytics/metrics.py`, `analytics/behaviour.py` |
| Ranking | `analytics/ranking.py` |
| Quantitative Research | `analytics/quant.py`, `analytics/technical.py` |
| AI Agent Orchestrator | `agents/base.py`, `agents/agents.py`, `agents/prompts/*.md` |
| Signal Ingestion | `copy/signals.py`, `api/routes.py` (`/api/signals/webhook/{id}`) |
| Copy Trading Engine | `copy/engine.py` |
| Portfolio Risk Engine | `risk/engine.py` (pure), `copy/monitor.py`, `services/portfolio.py` |
| Forex Execution Adapters | `adapters/forex/ctrader.py`, `adapters/forex/mt5.py`, `bridges/mt5_bridge.py` |
| Binance Execution Adapter | `adapters/binance/client.py`, `adapters/binance/adapter.py` |
| Paper broker / price feeds | `adapters/paper.py`, `adapters/feeds.py` |
| Notification Service | `notify.py` (DB feed + WebSocket + optional FCM HTTP v1) |
| Audit Service | `audit.py` (hash-chained, tamper-evident) |
| Backtesting / live gate | `backtest/engine.py`, `backtest/readiness.py` |
| Runtime | `runtime.py` (engine), `worker.py` (Celery), `api/app.py` |

## Copy-engine pipeline (per signal, per following portfolio)

1. **Ingestion** – HMAC verified (±30 s), validated, deterministic idempotency key, `UNIQUE` constraint → duplicates are no-ops. Published to the `titan:signals` Redis Stream (consumer group, at-least-once, pending entries re-delivered after a crash).
2. **Staleness** – entries older than 20 s (forex) / 10 s (crypto) are rejected; exits are always processed.
3. **Eligibility** – allocation enabled, trader not rejected/suspended, not already copied, one position per symbol on netting venues, no new forex entries within 15 min of the NY 17:00 rollover.
4. **Late-entry check** – rejected if price moved > 0.3 R against the source entry, or is already beyond the stop.
5. **Risk pre-trade** (hard-coded): kill switch, portfolio status, daily/weekly loss, max drawdown, valid stop-loss (side + distance), position counts, no averaging into an at-risk position, AI/calendar blocks (tighten-only). Budget = 0.5% equity × drawdown step × volatility × AI multiplier × allocation, capped by portfolio (1.5%), trader (0.5%) and daily-loss headroom.
6. **Sizing** – market-specific: forex lots from stop distance × pip value × currency conversion × broker volume step; crypto quantity from exchange filters (LOT_SIZE/MARKET_LOT_SIZE/NOTIONAL), COIN-M inverse contracts. Source lot size is never copied.
7. **Risk post-size** – actual risk, portfolio/trader risk, effective leverage, margin usage, correlated currency exposure (forex legs).
8. **Write-ahead order** – `orders` row with deterministic client id committed **before** the venue call.
9. **Execution** – entry + protective stop (and TP). If the stop cannot be placed the position is closed immediately.
10. **Record** – position with fill price, slippage cost, fees, copy delay, protection order ids.
11. **Reconciliation** (every 20 s) – UNKNOWN orders resolved by client id; positions closed on the venue are closed in the DB (flagged estimated); untracked venue positions raise alerts; source positions that disappeared are closed.

Per-portfolio locks serialise signal handling, reconciliation and stop/target triggers (a race found in the live E2E test).

## Risk monitor (every 30 s)

Equity snapshot; day/week start equity (UTC day, ISO week); automatic halt at the daily (2%) or weekly (4%) loss limit with automatic reset next period; **10% maximum drawdown → `review_required`, copying disabled, critical notification, manual typed review required** (drawdown then re-measured from current equity, limits unchanged). Forex positions are flattened 10 min before the NY rollover.

## Database (PostgreSQL)

`devices`, `pairing_codes`, `credentials` (AES-256-GCM), `traders`, `trader_metric_snapshots` (all metrics nullable = UNKNOWN), `trader_equity_points`, `trader_trades`, `trader_scores` (components + explanation + version), `portfolios`, `allocations`, `signals`, `orders`, `positions`, `equity_snapshots`, `risk_events`, `system_state`, `paper_accounts`, `agent_prompts` (version history), `agent_runs` (logs + metrics), `notifications`, `audit_log` (hash chain), `backtest_runs`. Tables are created by SQLAlchemy metadata (`python -m titan.cli init`).

## Android app (android/)

Kotlin, Jetpack Compose, Material 3, MVVM (`LoadVm`/`AppVm` with StateFlow), Retrofit + kotlinx.serialization, Room (offline cache + notifications), EncryptedSharedPreferences (server URL + device token only), WorkManager (15-min background sync → Android notifications in 3 channels), OkHttp WebSocket for live updates. Screens: Main / Forex / Binance dashboards, Trader Marketplace, Trader Details, Copy Portfolio, Open Positions, Trade History, Risk Management, AI Intelligence Center, Quantitative Analytics, Broker Connections, Binance API Connections, Notifications, Settings, Audit Logs, Emergency Controls (+ pairing).
