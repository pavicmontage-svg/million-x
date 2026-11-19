# Deployment

## 1. Server

Bilo koji Linux VPS (2 vCPU / 4 GB RAM dovoljno je za trading; walk-forward na 2–3 godine i 10 simbola traje satima pa je s više CPU-a brže). Potrebni su Docker i Docker Compose te domena koja pokazuje na IP servera (za HTTPS).

> Lokacija servera bitna je za pristup Binanceu. Binance blokira određene zemlje (npr. SAD), a za EEA rezidente od 1. 7. 2026. nema novih naloga. Provjeri uvjete za svoj račun i zemlju.

```bash
git clone <tvoj-repo> million-x && cd million-x/backend
cp .env.example .env
docker compose run --rm backend python -m millionx.cli genkey   # rezultat upiši u MX_MASTER_KEY u .env
nano .env                                                       # MX_DOMAIN, po želji ANTHROPIC_API_KEY
docker compose up -d --build
docker compose exec backend python -m millionx.cli pair          # jednokratni kod za Android (10 min)
```

Provjera: `https://<domena>/api/health` mora vratiti `{"ok": true, ...}`.

**Sigurnosna kopija:** mapa `backend/data/` (baza) i vrijednost `MX_MASTER_KEY`. Bez master ključa spremljeni API ključevi se ne mogu dešifrirati.

## 2. Binance Demo ključevi

1. Prijava na Binance → *Demo Trading* → *API Management* → *Create API*.
2. Ne uključuj withdrawal dozvolu.
3. U aplikaciji: *More → Owner Administration → Binance API keys → DEMO → Verify & store*.
4. Prebaci način rada na `DEMO`.

## 3. Povijesni podaci i validacija

```bash
docker compose exec backend python -m millionx.cli download --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT --start 2023-01 --end 2025-12
docker compose exec backend python -m millionx.cli walkforward --symbols ETHUSDT,SOLUSDT --start 2023-01-01 --end 2025-12-31 --bots bot2_ema,bot7_liquidity --out /app/data/wf.json
```

Isto se može pokrenuti iz aplikacije (*Quant Research Lab*). Rezultat walk-forwarda primjenjuje se gumbom *Apply verdict to bots*.

## 4. APK

**GitHub Actions (preporuka):** push na GitHub → *Actions* → posljednji *build* → artifact `million-x-apk` → `app-debug.apk`. Na telefonu dopusti instalaciju iz nepoznatih izvora za preglednik ili upravitelj datoteka.

- Push obavijesti: u Firebase konzoli dodaj Android aplikaciju `com.millionx.ai`, preuzmi `google-services.json`, spremi ga kao GitHub secret `GOOGLE_SERVICES_JSON_B64` (base64), a na serveru postavi `MX_FCM_SERVICE_ACCOUNT`.
- Potpisani release: secretsi `MX_KEYSTORE_B64`, `MX_KEYSTORE_PASSWORD`, `MX_KEY_ALIAS`, `MX_KEY_PASSWORD`.

**Android Studio:** *Open* → mapa `android/` → *Build → Build APK(s)*.

## 5. Live (tek nakon dokaza)

1. Bot mora imati status `LIVE_ELIGIBLE` (walk-forward + Monte Carlo + paper).
2. *Owner Administration → Run execution & security preflight (demo)*: sve provjere moraju proći.
3. Upiši potvrdnu frazu → *Unlock live trading*.
4. Spremi LIVE ključeve (bez withdrawala, ograničene na IP servera) → način rada `LIVE`.
5. Preporuka: prvih tjedana ostavi `MANUAL APPROVAL`.
