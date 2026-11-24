# Installation

## 0. On the phone only (recommended)

1. Install the APK (Android 9+; arm64 phone build from the GitHub Actions artifact `titan-apk`).
2. Open it → **Start engine on this phone**. The first start unpacks the embedded Python engine (up to ~1 minute).
3. Settings → *Engine on this phone* → **Allow running in background (battery)** (essential on Samsung/Xiaomi), and allow
   notifications.
4. Connect venues in *Binance API Connections* / *Broker Connections* (keys are encrypted on the phone).
5. To copy: open a trader → *Copy from: <your account>* (source account) or import history and approve; enable it in
   *Copy Portfolio*. Paper mode (€500 / 500 USDT, SIMULATED) is active from the start.

### Connecting cTrader from the phone
1. At **openapi.ctrader.com** (cTrader ID login) → *Applications* → your app → add the Redirect URI shown in the app
   (default `http://127.0.0.1:8765/api/ctrader/callback`). Spotware must have approved the app.
2. TITAN → *Broker Connections* → **Log in with cTrader**: enter Client ID + Client Secret → the official cTrader login
   opens → Allow → back in TITAN pick your **demo** account (shown with the account number you know).
3. TITAN stores the token encrypted on the phone and refreshes it before it expires (~30 days). If the portal does not
   accept a `127.0.0.1` redirect, register any https address you like, enter the same address in the app's
   *Redirect URI* field, and after Allow paste the browser's address into the app (within one minute).

### Connecting MetaTrader 5 from the phone
MetaQuotes offers no API for the MT5 mobile app, so a **Windows PC with MetaTrader 5 open and logged in** must be on
while copying. Unzip `TITAN-MT5-Bridge` (from `backend/bridges/`) on the PC, double-click
`windows/START_TITAN_BRIDGE.bat` (installs the packages, creates the secret, opens a local page with a QR code), then in
the app *Broker Connections* → **Scan QR from PC**. The QR carries the PC's Tailscale and home-network addresses and the
secret; the app tries them and keeps the reachable one first. The pairing page is served only to the PC itself.

Everything below (sections 1-5) is only needed for the optional server deployment.

## 1. Server (the permanent execution engine)

Requirements: an EU VPS (2 vCPU, 4 GB RAM, 40 GB disk) with Docker + Docker Compose and a DNS name pointing at it.
Choose a location from which Binance Demo is reachable (Binance returns HTTP 451 to restricted regions); the engine
reports this on startup and `python -m titan.cli binance-check` tests it.

```bash
git clone https://github.com/pavicmontage-svg/million-x.git && cd million-x/titan-copy-ai/backend
cp .env.example .env                    # edit: TITAN_DOMAIN, POSTGRES_PASSWORD (long random)
mkdir -p secrets backups && sudo chown 10001:10001 backups
python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))" > secrets/titan_master_key
chmod 600 secrets/titan_master_key      # keep an offline copy: without it stored credentials are unrecoverable
docker compose up -d --build
docker compose ps                        # postgres, redis, api, engine, worker, caddy -> healthy
curl https://$TITAN_DOMAIN/api/health
```

The stack starts in **paper mode** with two portfolios: *Forex Paper* (€500) and *Crypto Paper* (500 USDT), and the
research watchlist with all metrics UNKNOWN.

Optional settings in `.env`: `ANTHROPIC_API_KEY` (Claude review for the six agents), `TITAN_FCM_*` (push), `TITAN_CALENDAR_URL`
(economic calendar feed for news blackouts). `TITAN_LIVE_TRADING_ENABLED` must stay `false` until the live gate passes.

## 2. Android app

1. Download the APK from the GitHub Actions run (`titan-copy-ai` workflow → artifact `titan-apk`) or build it:
   `cd titan-copy-ai/android && ./gradlew assembleDebug` (JDK 17, Android SDK 35).
2. Install it (allow installation from unknown sources). Android 9+.
3. On the server: `docker compose exec api python -m titan.cli pair` → 15-minute one-time code.
4. In the app enter `https://<your domain>` and the code. Allow notifications (Settings screen).

## 3. Connect venues (all optional; paper trading works without them)

* **Binance Demo** – create API keys at demo.binance.com (Demo Trading), futures/spot trading only, no withdrawals.
  App → *Binance API Connections* → add (DEMO) → *Verify* (time sync, One-way mode, balance) → *Create portfolio*.
  New portfolios start with copying OFF.
* **cTrader** – register an Open API application at openapi.ctrader.com, obtain an access token with scope `trading`
  for your demo account (OAuth via id.ctrader.com), note the `ctidTraderAccountId`. App → *Broker Connections* → cTrader.
* **MetaTrader 5** – on the Windows machine running your logged-in MT5 terminal:
  `pip install MetaTrader5 fastapi uvicorn`, set `TITAN_BRIDGE_SECRET`, run `python bridges/mt5_bridge.py --host 0.0.0.0`,
  expose it via TLS or WireGuard. App → *Broker Connections* → MT5 (URL + secret).
* **Myfxbook / Darwinex** – monitoring sources for trader discovery (*Broker Connections*). The worker refreshes them every 6 h.

## 4. Traders and signals

* Evaluate traders: import a history CSV (Myfxbook export or generic columns) or connect Myfxbook/Darwinex. Each trader
  is scored with an explanation; rejected traders cannot be approved.
* Copying requires an **authorised signal source**: in the trader profile create a signal source (HMAC secret shown once)
  and give it to the provider / your own strategy, or use an account you own (cTrader / MT5 / Binance). Then approve the
  trader and enable it in *Copy Portfolio*.

## 5. Going live (not before 30 days of paper trading)

Settings → *Live trading gate* shows the checks (≥ 30 days, ≥ 20 trades, PF ≥ 1.2, DD < 10 %, execution accuracy ≥ 95 %,
no UNKNOWN orders, intact audit chain). Only after all pass: set `TITAN_LIVE_TRADING_ENABLED=true`, add live credentials,
and approve with the typed phrase (`POST /api/live/approve`, valid ≤ 30 days). Nothing here guarantees profit.

## Operations

* Logs: `docker compose logs -f engine` · Backups: `./backups/*.dump` (nightly, 14 days) · Restore:
  `docker compose exec -T postgres pg_restore -U titan -d titan --clean /path/in/container.dump`
* Audit check: `docker compose exec api python -m titan.cli verify-audit`
* Update: `git pull && docker compose up -d --build`
