# Deployment

## 0. Sve s mobitela (preporučeno): Hetzner + cloud-init

1. **GitHub token:** github.com → profilna slika → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*. Za *Repository access* odaberi *Only select repositories → million-x*, za *Permissions → Contents* odaberi *Read-only*, rok 7 dana. Kopiraj token.
2. **Hetzner Cloud** (console.hetzner.cloud) → *Add Server*: lokacija Njemačka ili Finska, image **Ubuntu 24.04**, najmanji tip (x86), javni IPv4 uključen. SSH ključ ne treba; root lozinka stiže e-mailom.
3. U polje **Cloud config** zalijepi sadržaj datoteke `deploy/cloud-init.yaml` i zamijeni `PASTE_GITHUB_TOKEN_HERE` i `PASTE_PAIRING_CODE_HERE` (kod koji sam smisliš, najmanje 12 znakova). Klikni *Create & Buy*.
4. Pričekaj 5–10 minuta, zatim u pregledniku otvori `https://A-B-C-D.sslip.io/api/health`, gdje je A.B.C.D IP servera s točkama zamijenjenima crticama.
5. U aplikaciji upiši tu adresu (bez `/api/health`) i svoj kod → *Pair device*. Kod vrijedi 72 h, samo jednom, i samo dok nijedan vlasnik nije uparen.

Server se pokreće u **PAPER** načinu. Ako traka u aplikaciji pokazuje „NO MARKET DATA“, Binance odbija IP servera; tada izbriši server i napravi novi na drugoj lokaciji.

## 1. Server (ručno, s računala)

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

## 5. Live (tek nakon dokaza; nikad automatski)

Zadani i rezervni način rada je **PAPER**. Live je dopušten samo dok postoji valjano, vremenski ograničeno odobrenje:

1. Bot mora biti `LIVE_ELIGIBLE`. To je moguće samo uz walk-forward verdikt mlađi od 90 dana i dovoljno stvarnih paper/demo trgovina; shadow trgovine se ne računaju.
2. *Owner Administration → Run demo preflight*: provjerava mehaniku izvršenja na Binance DEMO. Samo po sebi to **ne** otključava live.
3. Spremi LIVE ključeve (Futures dozvola uključena, withdrawal isključen, **obavezno** ograničenje na IP servera). Ključ koji ne ispunjava uvjete se odbija.
4. *Run live key check*: provjerava dozvole, kapital, slobodnu marginu i sat.
5. Upiši potvrdnu frazu → *Authorise live trading*. Odobrenje vrijedi `max_unlock_hours` (zadano 12 h). Poništava ga svaka rotacija ključa i svaka promjena limita, načina izvršenja ili kapitala.
6. Prebaci način rada na `LIVE`. Ako se server restartira bez valjanog odobrenja, sam se vraća na PAPER.
7. Preporuka: prvih tjedana ostavi `MANUAL APPROVAL`.

## 6. Produkcijski potpis APK-a

Debug APK (`...-DEBUG-not-for-production.apk`) služi samo za testiranje. Za produkciju napravi vlastiti ključ (jednom, na računalu):

```bash
keytool -genkeypair -v -keystore mx-release.jks -alias millionx -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 mx-release.jks > mx-release.b64
```

U GitHub repozitoriju otvori *Settings → Secrets and variables → Actions* i dodaj: `MX_KEYSTORE_B64` (sadržaj datoteke `mx-release.b64`), `MX_KEYSTORE_PASSWORD`, `MX_KEY_ALIAS` (`millionx`) i `MX_KEY_PASSWORD`. Sljedeći build na grani `apk` objavljuje `...-release-signed.apk`. **Datoteku `.jks` i lozinke čuvaj sigurno.** Ako ih izgubiš, nadogradnja aplikacije više nije moguća.

## 7. Rad i oporavak

- **Tajne:** umjesto `MX_MASTER_KEY` može se koristiti `MX_MASTER_KEY_FILE=/app/secrets/master.key` (datoteka mora imati `chmod 600`).
- **Health:** `GET /api/health` vraća 200 ako je sve u redu, a 503 uz detalje (engine, zadnji tick, rekoncilijacija, otvoreni kritični incidenti, integritet baze, audit lanac, zadnji backup, live gate).
- **Watchdog:** restartira engine koji je pao ili 3 minute nema ticka; jednom dnevno provjerava audit lanac i radi backup u `data/backups/` (čuva se 14 kopija). Ručno: `python -m millionx.cli backup`.
- **Migracije:** pokreću se automatski pri startu. Ručno: `python -m millionx.cli migrate`.
- **Restart:** otvorene pozicije učitavaju se iz baze, a pri startu se rekonciliraju s burzom. Nepoznate pozicije dobivaju zaštitni stop i otvara se kritični incident. Emergency stop ostaje aktivan i nakon restarta.
- **Incidenti:** prikazuju se u *Risk Center*; rješava ih vlasnik.
