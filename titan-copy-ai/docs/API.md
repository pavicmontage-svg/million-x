# API reference

Generated from the FastAPI application (`python -m titan.cli api` also serves interactive OpenAPI docs at `/docs`).
All endpoints except `/api/health`, `/api/pair` and the signed webhook require `Authorization: Bearer <device token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agents` | Agents |
| POST | `/api/agents/{name}/run` | Run Agent |
| GET | `/api/agents/{name}/runs` | Agent Runs |
| GET | `/api/allocations` | Allocations |
| POST | `/api/allocations` | Upsert Allocation |
| GET | `/api/analytics/{tid}` | Analytics |
| GET | `/api/audit` | Audit Log |
| GET | `/api/audit/verify` | Audit Verify |
| POST | `/api/backtest` | Backtest |
| GET | `/api/connections` | Connections |
| POST | `/api/connections` | Add Connection |
| DELETE | `/api/connections/{cid}` | Delete Connection |
| POST | `/api/connections/{cid}/portfolio` | Attach Portfolio |
| POST | `/api/connections/{cid}/verify` | Verify Connection |
| GET | `/api/dashboard` | Dashboard |
| POST | `/api/devices/fcm` | Fcm |
| GET | `/api/emergency` | Emergency |
| POST | `/api/emergency/flatten` | Emergency Flatten |
| POST | `/api/emergency/release` | Emergency Release |
| POST | `/api/emergency/stop` | Emergency Stop |
| POST | `/api/emergency/trader/{tid}/stop` | Emergency Trader |
| GET | `/api/health` | Health |
| POST | `/api/live/approve` | Approve Live |
| GET | `/api/notifications` | Notifications |
| POST | `/api/notifications/read` | Mark Read |
| GET | `/api/orders` | Orders |
| POST | `/api/pair` | Pair |
| GET | `/api/portfolios` | Portfolios |
| PATCH | `/api/portfolios/{pid}` | Patch Portfolio |
| GET | `/api/portfolios/{pid}/equity` | Equity |
| POST | `/api/portfolios/{pid}/reactivate` | Reactivate |
| GET | `/api/positions` | Positions |
| POST | `/api/positions/{pos_id}/close` | Close Position |
| GET | `/api/readiness` | Readiness |
| GET | `/api/risk` | Risk |
| GET | `/api/settings` | Settings View |
| GET | `/api/signals` | Signals |
| POST | `/api/signals/webhook/{tid}` | Webhook |
| GET | `/api/traders` | Traders |
| POST | `/api/traders/import` | Import Trader |
| GET | `/api/traders/{tid}` | Trader |
| POST | `/api/traders/{tid}/signal-source` | Signal Source |
| POST | `/api/traders/{tid}/status` | Trader Status |

## Live events
`GET /api/ws?token=<device token>` (WebSocket) streams JSON events: `notification`, `position`, `signal`, `portfolio`.

## Authorised signal webhook
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

