# Operations & recovery procedures

## Normal start
The runtime loads the last configuration version, the edge book and portfolio anchors, builds the venue,
backfills ~8 days of 5m bars per instrument, reconciles with the exchange, then trades on bar closes.

## Recovery playbook
| Situation | What the system does | What you do |
|---|---|---|
| App/phone/server restart | Positions keep their **exchange-resident** stops/TPs. On start the reconciler compares state; positions opened before the restart are reported as unexpected and protected (default policy) | Review Alerts; close or keep the position manually; reset SAFE_STATE on the Risk page once resolved |
| Market data stale (all symbols) | SAFE_STATE, no new trades, websocket→REST fallback, gap backfill on reconnect | Check connectivity; it clears automatically when data is fresh |
| Exchange unreachable 3 reconcile cycles | Orders blocked, SAFE_STATE, critical alert | Check Binance status / network; clears when reconciliation is clean |
| Unexpected position | Alert, block, protect / flatten per policy | Investigate (manual trade? restart?), then reset |
| Protective stop missing | Re-placed automatically | Check the order log if it repeats |
| Stop cannot be placed after entry | Position flattened immediately, critical alert | Check symbol filters / account mode |
| Circuit breaker | New trades blocked; positions managed | Understand the cause before resetting (Risk page, typed RESET) |
| Emergency | Press **STOP** (top bar); optionally flatten all | Reset EMERGENCY_STOP on the Risk page, then Resume |
| Wrong configuration | — | Settings → Configuration versions → Restore |
| Runtime stopped at start (e.g. Binance unreachable) | Banner "Runtime is stopped" | Fix connectivity, press Start |

## Data & backups
SQLite database and master key live in the data directory (Android: app-private storage). Losing the master key
makes stored API keys unreadable (re-enter them). Server: back up `data/` or use PostgreSQL backups.

## Validation path before real money
backtest → walk-forward (OOS evidence) → paper trading (weeks) → testnet → small live with SEMI_AUTOMATIC.
The Quant Researcher page shows whether a setup's evidence is positive with a 95% CI excluding zero.
