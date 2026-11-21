# TITAN COPY AI

Autonomous multi-market copy-trading platform that **runs entirely on an Android phone**: the copy engine, hard risk
limits, six AI agents, trader discovery/scoring, the encrypted credential vault and the database all run inside the app
(embedded Python, foreground service, 24/7). It copies **authorised** signals into independent **Forex** and **Binance**
portfolios. A server deployment of the same backend (Docker) remains available as an option.

**On-phone mode (default):** install the APK → *Start engine on this phone* → done. No server, no pairing code.
The engine keeps running with the app closed and the screen off, restarts after reboots, and mirrors alerts as Android
notifications. It cannot trade while the phone is off or offline; protective stop-losses stay on the broker/exchange.

> **Status:** paper/demo only. Live trading is disabled by default and gated behind 30 days of paper evidence, a server
> flag and a typed approval. Nothing in this system promises profit; past performance does not predict future results.
> Since 1 July 2026 Binance does not accept new orders from EEA residents (MiCA) – live Binance from Croatia is very
> likely unavailable. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

| | |
|---|---|
| `backend/` | Python engine · Starlette API · SQLAlchemy. On the phone: Python 3.12 (Chaquopy), SQLite, in-process bus & scheduler. On a server: PostgreSQL, Redis Streams, Celery, Docker |
| `android/` | Kotlin · Jetpack Compose · Material 3 · MVVM · Retrofit · Room · EncryptedSharedPreferences · WorkManager · foreground EngineService · embedded Python engine |
| `backend/bridges/mt5_bridge.py` | Self-hosted MetaTrader 5 bridge (runs next to your terminal) |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) · [Integrations research](docs/INTEGRATIONS.md) · [API](docs/API.md) · [Security](docs/SECURITY.md) · [Installation](docs/INSTALL.md) · [Test report](docs/TEST_REPORT.md) · [AI agents](docs/AGENTS.md) |

## What it does

* **Two independent portfolios** – Forex (€500, cTrader Open API or MT5 bridge; paper by default) and Crypto
  (500 USDT, Binance Spot / USDⓈ-M / COIN-M Demo; paper by default). Separate adapters and contract maths per market.
* **Trader discovery & ranking** – Myfxbook API and Darwinex Info API sources, CSV import for platforms without a public
  API (ZuluTrade, MQL5, cTrader Copy listings). 35 metrics per trader; missing data is shown as **UNKNOWN**, never
  estimated. 0–100 score (drawdown 25, risk-adjusted return 20, profit factor 15, consistency 15, history 10, execution
  10, transparency 5) with an explanation, automatic rejection of martingale, grid, averaging down, hidden drawdown,
  suspicious curves, excessive leverage, accumulation, no verified history. Research watchlist seeded with the seven
  named candidates (unverified, all metrics UNKNOWN until data is retrieved).
* **Six AI agents** – Trader Intelligence, Quantitative Research, Risk Management (veto), Market Intelligence, Execution,
  Audit & Performance. Versioned prompts, run logs and metrics; Claude (`claude-opus-5-5`) reviews deterministic analyses
  when a key is configured; agents can only tighten risk.
* **Signal sources on the phone** – the engine polls a source account you control (Binance futures / sub-account, your
  MT5 terminal via the bridge, a cTrader account via OAuth) and copies its new positions, closes, partial closes and SL/TP
  changes. HMAC webhooks remain for server deployments.
* **Copy engine** – idempotent, write-ahead order state, staleness/late-entry checks, independent sizing (never source lot
  size), mandatory stop-loss, copies entries, exits, SL/TP changes and partial closes, reconciliation of unknown orders,
  venue-side closes and missing source positions. Survives app closure, phone offline, engine/server restart and venue
  outages (Redis Stream consumer groups + reconciler).
* **Risk** – 0.5 % per trade, 2 % daily, 4 % weekly, 10 % max drawdown (manual review), 1.5 % portfolio risk, 0.5 % per
  trader, correlated currency exposure, leverage and margin caps, drawdown and volatility de-risking, kill switch,
  per-trader stop, close-all, forex flat before rollover. Limits can only be tightened.
* **Backtesting & paper trading** – replay of real trader histories with delay, spread, commission, slippage, partial
  fills, venue rejections and disconnections; Monte Carlo; walk-forward; live-readiness gate.
* **Security** – AES-256-GCM credential vault (server only), hashed device tokens, HMAC signed webhooks, tamper-evident
  audit chain, Binance withdrawal-permission refusal, TLS via Caddy.

## Quick start (local development)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
python -m pytest -q                                   # 69 tests (SQLite); set TITAN_TEST_DATABASE_URL for PostgreSQL
export TITAN_DATABASE_URL=postgresql+psycopg://postgres@localhost/titan TITAN_MASTER_KEY=$(python -m titan.cli genkey)
python -m titan.cli init && python -m titan.cli pair  # one-time pairing code for the app
python -m titan.cli api &  python -m titan.cli engine & celery -A titan.worker worker -B
```

Production deployment: [docs/INSTALL.md](docs/INSTALL.md). APKs are built by the `titan-copy-ai` GitHub Actions workflow
(artifact `titan-apk`).
