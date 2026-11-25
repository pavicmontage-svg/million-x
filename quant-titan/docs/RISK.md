# Risk management

The Risk Manager (`risk/manager.py`) reviews every proposed trade, runs **all** checks (so every reason is
visible) and approves only if all pass. An approval is an HMAC-signed, single-use token bound to the decision,
symbol, side, max quantity and a price band (`max_entry_drift_r` of the stop distance); the order manager refuses
any exposure-increasing order without a valid token. No other component holds the signing key, so no agent can
override a veto. Reduce-only orders need no approval (they can only lower risk).

## Sizing
```
budget    = equity × risk_per_trade% × regime_mult × engine_mult × drawdown_mult      (each ≤ 1)
loss/unit = |entry − stop| + entry × (2·taker_fee + 2·slippage)
qty       = floor_to_step(budget / loss/unit)      → rejected if < minQty / minNotional
```
`drawdown_mult` falls linearly from 1 (at `drawdown_derisk_start_pct`) to `drawdown_derisk_floor` (at the monthly
limit). Size never increases after losses.

## Checks
emergency stop · circuit breakers · paused · direction mode · data staleness · minimum equity · stop on the
correct side and ≤ `max_stop_distance_pct` · spread · funding paid by this side · engine enabled / suspended /
max positions / daily loss in R · regime multiplier > 0 · exchange size limits · bar-volume participation ·
max concurrent positions · one position per symbol · max open risk · max symbol exposure · gross leverage ·
correlated same-direction risk within a cluster (signed correlation ≥ threshold) · isolated leverage fits the
available margin and liquidation distance ≥ `min_liquidation_distance_mult` × stop distance.

Isolated liquidation estimate (first maintenance bracket): long `entry·(1 − 1/L + mmr)`, short `entry·(1 + 1/L − mmr)`.

## Circuit breakers (`risk/breakers.py`)
| Breaker | Trips when | Reset |
|---|---|---|
| DAILY_LOSS | daily loss ≥ limit | next UTC day (auto) or manual |
| WEEKLY_LOSS | weekly loss ≥ limit | next ISO week (auto) or manual |
| MONTHLY_DRAWDOWN | drawdown from the month's peak ≥ limit | manual, typed confirmation |
| CONSECUTIVE_LOSSES | ≥ N losing trades in a row | after the cooldown |
| EMERGENCY_STOP | user / system | manual, typed confirmation |
| SAFE_STATE | reconciliation failure, stale data, exchange outage, crashed task, equity below minimum | automatically when healthy again, or manual |

On a trip: new trades blocked, event stored and notified, open positions keep their exchange stops and are still
managed. Initial values (0.5% per trade, 2% daily, 5% weekly, 10% monthly, 3 losses → 4h cooldown) are test
parameters, not recommendations; all are editable and validated in the app.
