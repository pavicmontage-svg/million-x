# API reference

The same API runs on the phone (bound to 127.0.0.1, used by the app itself) and on an optional server.
Implemented on Starlette (no pydantic, which has no Android build). All endpoints except `/api/health`,
`/api/pair` and the signed webhook require `Authorization: Bearer <device token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agents` | agents |
| POST | `/api/agents/{name}/run` | run agent |
| GET | `/api/agents/{name}/runs` | agent runs |
| GET | `/api/allocations` | allocations |
| POST | `/api/allocations` | upsert allocation |
| GET | `/api/analytics/{tid}` | analytics |
| GET | `/api/audit` | audit log |
| GET | `/api/audit/verify` | audit verify |
| POST | `/api/backtest` | backtest |
| GET | `/api/connections` | connections |
| POST | `/api/connections` | add connection |
| DELETE | `/api/connections/{cid}` | delete connection |
| POST | `/api/connections/{cid}/portfolio` | attach portfolio |
| POST | `/api/connections/{cid}/verify` | verify connection |
| GET | `/api/dashboard` | dashboard |
| POST | `/api/devices/fcm` | fcm |
| GET | `/api/emergency` | emergency |
| POST | `/api/emergency/flatten` | emergency flatten |
| POST | `/api/emergency/release` | emergency release |
| POST | `/api/emergency/stop` | emergency stop |
| POST | `/api/emergency/trader/{tid}/stop` | emergency trader |
| GET | `/api/health` | Service health (database, event bus, engine heartbeat) |
| POST | `/api/live/approve` | approve live |
| GET | `/api/notifications` | notifications |
| POST | `/api/notifications/read` | mark read |
| GET | `/api/orders` | orders |
| POST | `/api/pair` | Exchange a one-time pairing code for a device token |
| GET | `/api/portfolios` | portfolios |
| PATCH | `/api/portfolios/{pid}` | patch portfolio |
| GET | `/api/portfolios/{pid}/equity` | equity |
| POST | `/api/portfolios/{pid}/reactivate` | reactivate |
| GET | `/api/positions` | positions |
| POST | `/api/positions/{pos_id}/close` | close position |
| GET | `/api/readiness` | readiness |
| GET | `/api/risk` | risk |
| GET | `/api/settings` | settings view |
| GET | `/api/signals` | signals |
| POST | `/api/signals/webhook/{tid}` | webhook |
| GET | `/api/traders` | traders |
| POST | `/api/traders/import` | import trader |
| GET | `/api/traders/{tid}` | trader |
| POST | `/api/traders/{tid}/signal-source` | Create (or rotate) the HMAC secret an authorised provider uses to sign webhook signals. |
| DELETE | `/api/traders/{tid}/source-account` | Stop polling the trader's source account |
| POST | `/api/traders/{tid}/source-account` | Use one of the owner's connected accounts as this trader's signal source (polled; no webhook needed) |
| POST | `/api/traders/{tid}/status` | trader status |

## Live events
`GET /api/ws?token=<device token>` (WebSocket) streams JSON events: `notification`, `position`, `signal`, `portfolio`.

## Authorised signal sources

On the phone, signals come from **source accounts** the engine polls (`POST /api/traders/{id}/source-account`
with a Binance futures, MT5-bridge or cTrader credential): new position → open, closed → close, reduced → partial close,
SL/TP changed → modify. Positions already open when a source is connected are not copied.

## Authorised signal webhook (server mode only - a phone has no public address)
```
POST /api/signals/webhook/{trader_id}
X-Titan-Timestamp: <unix ms>
X-Titan-Signature: hex(HMAC_SHA256(secret, "<timestamp>.<raw body>"))

{"kind": "open", "source_position_id": "A-1001", "symbol": "EURUSD", "side": "buy",
 "entry_price": 1.0800, "sl": 1.0780, "tp": 1.0850, "source_ts": "2026-11-21T10:00:00Z",
 "bid": 1.07998, "ask": 1.0800}
```
`kind`: `open` (needs side, entry_price; sl is mandatory for execution), `close`, `partial_close` (`close_fraction` 0–1),
`modify` (`sl` and/or `tp`; widening a stop beyond the initial risk is ignored). Optional `signal_id` (provider event id)
for idempotency. Duplicates return `{"duplicate": true}`.

Python example:
```python
import hashlib, hmac, json, time, httpx
body = json.dumps(payload).encode(); ts = str(int(time.time() * 1000))
sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
httpx.post(f"{server}/api/signals/webhook/{trader_id}", content=body,
           headers={"X-Titan-Timestamp": ts, "X-Titan-Signature": sig})
```

