# Test report (21 Nov 2026)

## Automated suites

| Suite | Result |
|---|---|
| Backend pytest on SQLite | **69 passed** |
| Backend pytest on PostgreSQL 16 (local) | **69 passed** |
| Android unit tests (`testDebugUnitTest`) | **5 passed** |
| Android debug APK build | success (62.9 MB) |
| Android release build with R8 (test-signed) | success (3.75 MB) |
| Android instrumented launch test | written; runs in CI on an Android 14 emulator (no KVM in the build container) |

Backend coverage by area: market maths (pip values, conversion, ESMA leverage caps, Binance filters incl. COIN-M inverse),
risk engine (budget, stop validation, daily/weekly/max-DD, headroom, correlated currency exposure, overlays, tighten-only
limits, no averaging down), analytics (metrics, UNKNOWN handling, martingale/grid/hidden-drawdown detection, scoring,
Monte Carlo, walk-forward, correlation, CSV import), copy engine (open, duplicates, stale, missing SL, late entry, min
volume, SL tighten/widen, partial + full close, paper SL trigger, daily halt, max-DD review, kill switch + flatten,
lost-response reconciliation, restart persistence, venue-side close, correlation block, rejected trader, concurrent
close vs reconcile), Binance adapter contract (signing, hosts, quantisation, algo-order protection, SL-failure
emergency close, no blind POST retry, hedge-mode check, partial close re-protection, HTTP 451 handling), API (pairing,
auth, dashboard, watchlist UNKNOWN, import → score → approve → authorised webhook → dedup, backtest, analytics, limits,
emergency controls, credential encryption, live-credential refusal, agents, news blackout, LLM tighten validation,
readiness gate, audit tamper detection).

## Real-connectivity checks

| Check | Result |
|---|---|
| Binance public market data (`data-api.binance.vision`): exchangeInfo, bookTicker, klines for all 10 symbols | **OK** |
| Binance trading hosts (`api`, `fapi`, `demo-api`, `demo-fapi`, `demo-dapi`, testnet) from this environment | **HTTP 451 (restricted location)** – demo order flow verified only against the documented contract (mock transport) |
| cTrader proto definitions (`spotware/openapi-proto-messages`) | fetched; payload IDs/fields used verbatim |
| Darwinex `api.darwinex.com` | not reachable from this environment (503 / reset) |
| Myfxbook API | page blocks automated fetches (403); methods confirmed from published client |
| Anthropic API | no key configured here → agents ran in deterministic mode; LLM path validated by schema/unit tests only |

## Live end-to-end run (real processes, PostgreSQL, Redis, real Binance prices)

API and engine as separate processes. A test-fixture trader was imported, approved and given a signal secret; a signed
webhook `open ETHUSDT` at the live ask (1689.01) → Redis Stream → engine → paper fill **0.0986 ETH, risk 2.499 USDT
(0.5 % of 500), 895 ms copy delay**, Binance step size respected. The engine was then killed with SIGKILL, a `close`
signal was sent while it was down (queued durably), and after restart the position was closed at the live price with
fees and R-multiple recorded; audit chain verified.

**Bugs found by this run and fixed (with regression tests):**
1. Race between the reconciler and a signal close recorded the close twice and overwrote the real exit with an estimate
   → per-portfolio locks + idempotent close recording.
2. Audit chain verification broke on PostgreSQL because JSONB normalises `-0.0` (and rejects NaN) → canonical JSON form.

Earlier real-data finding: Binance Spot `MARKET_LOT_SIZE` reports `stepSize=0`; quantities were not quantised → fallback
to `LOT_SIZE` (verified against live filters).

## Docker deployment smoke test

Production Compose stack (PostgreSQL 17, Redis, API, engine, Celery worker+beat) built and started: all services healthy,
pairing via CLI code, dashboard with both paper portfolios, worker backup → `pg_restore` into a scratch DB (22 tables).
Findings fixed: missing `packaging` dependency (Celery/kombu Redis transport), worker healthcheck, `pg_dump` 17 vs
server 16 mismatch (stack moved to PostgreSQL 17). (The sandbox needed a proxy-CA build variant; the production
Dockerfile is unchanged and is built as-is in CI.)

## Not verified here (requires your environment)

* Real Binance Demo orders (geo-block), cTrader Open API session (needs your app credentials + token), MT5 bridge (needs a
  Windows terminal), Myfxbook/Darwinex live pulls (need your credentials), FCM push (needs Firebase project), Claude
  reviews (needs `ANTHROPIC_API_KEY`), UI on a physical device (CI emulator test covers launch only).
* 30 days of paper trading: the system records paper results and the live gate enforces the 30-day minimum; that period
  necessarily starts after deployment.
* No backtest or paper result in this repository is evidence of future profitability.
