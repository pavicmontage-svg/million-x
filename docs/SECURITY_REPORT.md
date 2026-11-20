# SECURITY REPORT (1.9.0)

Nadopunjuje docs/SECURITY_AUDIT.md (34 nalaza iz 1.1). Novo u 1.9.0:

| Područje | Kontrola | Provjera |
|---|---|---|
| AI ključevi | Šifrirani (Fernet) u `kv.ai_providers.key_enc`; nikad u odgovoru API-ja; greške se čiste regexom (`sk-…`, `gsk_…`, `AIza…`, `Bearer …`) | `test_keys_encrypted_and_scrubbed` |
| AI ↔ burza | AI moduli nemaju pristup exchange klijentu ni ključevima; jedini ulaz je `engine.submit_external()` → supervisor → risk engine → governance → execution | `test_trader_signal_goes_through_supervisor_and_risk_engine` |
| AI odobrenje | APPROVE bez razloga je neispravan izlaz; odobrenje samo vraća signal na ponovno dimenzioniranje po trenutnoj cijeni; AI nedostupan → NO TRADE | `test_supervisor_cannot_approve_without_reasons`, `test_supervisor_rejects_and_ai_down_means_no_trade` |
| EMERGENCY_STOP od AI-ja | Blokira nove ulaze bez zatvaranja pozicija (stopovi ostaju na burzi); vlasnik ga poništava | pregled koda |
| TradingView | Tajni ključ (constant-time) ili HMAC-SHA256 nad tijelom; ±60 s; dedup ID; tajna se ne sprema u log; 30 zahtjeva/min po IP-u; 8 KB limit | `test_tradingview_webhook_security_and_pipeline` |
| Binance | Ključ s withdrawal dozvolom se odbija; live traži IP restriction i Futures; saldo prije naloga; idempotentni clientOrderId + atomarni claim | postojeći testovi + `test_real_balance_checked_before_every_order` |
| Rizik | Profil MAX i SIM granice samo u PAPER/DEMO; prelazak na LIVE s njima se odbija; leverage ≤ 20x i likvidacijski razmak | `test_max_profile_only_with_virtual_money`, `test_liquidation_buffer_lowers_leverage_for_wide_stops` |

Poznato:
- `/api/tradingview/webhook` je javan po prirodi (TradingView ne šalje tokene); zaštita je tajni ključ + vremenski prozor + dedup + rate limit.
- DEBUG APK nije za produkciju.
