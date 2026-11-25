# Security

* **API keys**: entered once in the app, encrypted with Fernet (AES-128-CBC + HMAC) using a master key from
  `QT_MASTER_KEY` or a 0600 key file in private storage; never returned by the API (masked preview only), never
  persisted in plaintext, registered with the log redactor.
* **Permissions**: create Binance keys with *Enable Futures* + reading only. **Never enable withdrawals.** The LIVE
  key check calls `/sapi/v1/account/apiRestrictions` and refuses keys with withdrawals enabled. Use IP restriction
  when running on a server with a fixed IP (a phone usually has a changing IP).
* **Logs**: every record passes a redaction filter (keys, secrets, signatures, passwords, bearer tokens, and any
  literal secret value registered at runtime).
* **Authentication**: server mode — admin account (scrypt), 256-bit session tokens (only SHA-256 stored), 12h
  expiry, 2h idle timeout, login lockout after 5 failures in 10 minutes. Android — the backend binds to 127.0.0.1
  only and uses a random device token from app-private storage. Tokens travel in the Authorization header (no
  cookies → no CSRF).
* **API hardening**: auth on all routes except health/login, rate limiting, 64 KB body limit, JSON validation,
  pydantic validation of every configuration change, CSP (`script-src 'self'`, no third-party scripts),
  X-Frame-Options DENY, no-store caching for API responses.
* **Dangerous actions** need typed confirmations (`CLOSE ALL`, `RESET`, `ENABLE LIVE TRADING`, `RESET PAPER`);
  LIVE additionally needs `QT_ALLOW_LIVE=1` at deployment level and a fresh successful key check.
* **Audit log** for every mutating action (who, what, when, from where); configuration versions are immutable
  and can be restored.
* **Android**: `allowBackup=false`, cleartext only to 127.0.0.1, WebView restricted to the local backend, no file
  access from the WebView.
