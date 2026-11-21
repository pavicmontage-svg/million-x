# Security

## Credentials
* Exchange/broker/data credentials are entered once in the app, sent over TLS, and stored **only on the server**, encrypted with **AES-256-GCM** (random 96-bit nonce, record identity `venue|environment|label` bound as associated data so ciphertexts cannot be swapped). They are never returned by any API.
* The master key comes from the secrets manager: Docker secret file `secrets/titan_master_key` → `/run/secrets/titan_master_key` (`TITAN_MASTER_KEY_FILE`). It is not in the image, the repository or the database. Losing it makes stored credentials unrecoverable (re-enter them).
* The Android app stores only the server URL and its device token in EncryptedSharedPreferences (Android Keystore); app backups are disabled.
* Separate demo and live credentials (`environment` is part of the record). Live credentials are refused while `TITAN_LIVE_TRADING_ENABLED=false`.

## Binance keys
* Create keys in Demo Trading for testing. For live keys: **never enable withdrawals**, enable only the trading product you use, restrict to the server's IP.
* Live preflight calls `GET /sapi/v1/account/apiRestrictions` and **refuses keys with withdrawals enabled**; warns when the key is not IP-restricted. Account must be in One-way mode.

## Forex brokers
* Official integrations only: cTrader Open API (OAuth; scope `trading` on accounts you own) and the MT5 terminal on your own machine through a self-hosted bridge. No passwords are shared with third-party copy services. Respect broker API terms; verify the account is yours and the broker is EU/EEA-authorised.

## Device authentication
* Pairing: one-time code (15 min, single use, stored hashed) from `python -m titan.cli pair`; optional bootstrap code valid only until the first device is paired. Brute-force limited per client (5 attempts, then 1/min).
* Device bearer tokens: 256-bit random, only SHA-256 hashes stored, revocable.

## Signals
* Webhooks require `X-Titan-Signature = hex(HMAC_SHA256(secret, "{timestamp}.{body}"))` with a ±30 s timestamp window; secrets are generated per trader, shown once, stored encrypted.
* Idempotency keys + DB uniqueness prevent replayed signals from executing twice.

## Trading safety
* Live trading is off by default and requires: server flag, ≥ 30 days paper/demo evidence (live gate checks), typed approval phrase, time-limited approval.
* Hard risk limits are code constants (ceilings); the API can only tighten them. AI output can only add tightening actions, validated in code.
* Dangerous app actions require typed confirmation phrases (close all, release emergency stop, review after max drawdown).

## Audit
* Every material action (signals, orders, positions, limit changes, credentials, approvals, emergency actions, agent runs) is written to a hash-chained `audit_log`; `GET /api/audit/verify` / `python -m titan.cli verify-audit` recompute the chain and report the first tampered row.

## Transport & hosting
* Caddy terminates TLS (automatic certificates), HSTS, `X-Frame-Options: DENY`; API responses carry `no-store`, `nosniff`. The Android network-security config forbids cleartext except emulator/localhost.
* Containers run as an unprivileged user (uid 10001); Postgres and Redis are not exposed publicly.
* Nightly `pg_dump` (14 days retention) to `./backups`; restore verified in CI (`pg_restore` into a scratch DB). Copy backups off-site.

## Known limitations
* FCM push is optional; without it, notifications arrive via the live socket and a 15-minute background sync.
* Credential rotation of the master key requires re-entering credentials (no re-encryption tool yet).
