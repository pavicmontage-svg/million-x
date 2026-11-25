# Android app

The APK contains the **entire platform**: Python 3.10 (Chaquopy), numpy 1.23.3, scipy 1.8.1, pandas 2.1.3,
statsmodels 0.14.5, cryptography 42.0.8, pydantic 1.10, Starlette/uvicorn — exactly the versions the CI test job
`tests (Python 3.10, exact APK library versions)` runs the full suite against. These are the newest mutually
compatible Android builds in the Chaquopy repository (scipy 1.8.1 requires numpy < 1.25; pydantic v2 has no
Android build, hence the v1 API).

* `EngineService` — foreground service (special use), partial wake lock, START_STICKY, restart after reboot and
  app update, status notification, and Android notifications for trades, stops, breakers, emergency stops,
  pending approvals and failures.
* `MainActivity` — WebView showing the dashboard from `http://127.0.0.1:<port>` with the device token; links to
  other hosts open in the browser; Back keeps the engine running.
* Python entry: `quanttitan/android_entry.py`.

## Install / update
GitHub → **Releases → Quant Titan (latest build)** → download the APK on the phone → install. Builds are signed
with a CI key kept in the Actions cache so updates install over the previous version. For a permanent signing key,
add repository secrets `QT_KEYSTORE_B64` (base64 of a .jks), `QT_KEYSTORE_PASSWORD`, `QT_KEY_ALIAS`,
`QT_KEY_PASSWORD`. If the signature changes (e.g. cache expired), uninstall the old app first (this deletes its data).

## Phone settings for 24/7 operation
Settings → Apps → Quant Titan → Battery → **Unrestricted**; allow notifications. Keep the phone charging and on a
reliable network. A phone is not a data centre: network changes, OS updates and battery management can interrupt
the engine — exchange-resident stops protect open positions meanwhile.

## Connectivity
Market data comes from `fapi.binance.com` (or the testnet). If Binance is not reachable from your location/network,
the runtime shows "Runtime is stopped"; use paper replay or the research lab (data.binance.vision) instead. See
LIMITATIONS.md regarding EEA availability.
