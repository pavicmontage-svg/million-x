# MILLION-X 1.9.0 — AUDIT REPORT

Datum: 20. 11. 2026. Polazište: 1.8.0 (commit `7535ba2`, backup: lokalni git bundle
`million-x-1.8.0-backup.bundle` + povijest grane `main`; push zasebne backup grane/taga
odbio je proxy, pa se backup oslanja na bundle i git povijest).

Metoda: pregled svih Python/Kotlin/YAML/Gradle/Docker datoteka, pretraga za TODO,
placeholderima i lažnim modulima (nema ih; jedini `NotImplementedError` je apstraktna
metoda bazne strategije), pokretanje svih testova prije i poslije izmjena. Za svaku stavku
provjereno je stvarno izvršavanje u testu, ne samo postojanje klase.

Statusi testiranja: **TEST** = automatski test u repou koji prolazi lokalno i u CI-ju;
**CI-REAL** = pokrenuto u GitHub Actions na stvarnim podacima; **RUČNO** = moguće samo na
uređaju ili s vlasnikovim ključem; **NIJE** = nije implementirano (navedeno zašto).

| Funkcionalnost | Postojeća implementacija (1.8.0) | Otkriveni problem | Popravak u 1.9.0 | Status testiranja |
|---|---|---|---|---|
| Kapital | 4 / 40 EUR načini | Spec traži 500 EUR | Novi način `pro` = 500 EUR (zadano), biranje u Adminu | TEST |
| Rizik po trgovini | 0,5 % | U redu; profil MAX (demo) dopuštao 5 % | Standard = 0,5 % (2,50 EUR na 500 EUR); MAX ostaje samo za PAPER/DEMO, LIVE ga odbija | TEST |
| Dnevni gubitak | samo postotak (3 %) | Nema apsolutnog limita | `max_daily_loss_eur` = 10 EUR + 2 %; stroži vrijedi | TEST |
| Broj pozicija | 3 | Spec traži 2 | `max_open_positions` = 2 | TEST |
| Leverage | dinamički po volatilnosti, max 10x | Spec: 5/10/15/20x po kvaliteti setupa, isolated | Razine 5/10/15/20x (score setupa, volatilnost, spread); sam confidence ne daje 20x; snižavanje dok likvidacija nije ≥ 3× udaljenost stopa; ISOLATED | TEST |
| Veličina pozicije | rizik/(stop + naknade + slippage) | Funding nije bio uključen | + očekivani funding; margina, maintenance margin, likvidacijska cijena u odluci | TEST |
| Usporedba 5/10/15/20x | — | — | `research leverage` na 16 mj. stvarnih podataka (1h): identičan rezultat na svim razinama, 0 likvidacija (rizik određuje poziciju, leverage samo maržu) | CI-REAL |
| Stvarni Binance saldo prije naloga | ne | Nalog mogao biti odbijen na burzi | `available` ≥ potrebna margina, inače blokada | TEST |
| Timeframe | fiksan po botu | Vlasnik traži 30m/1h/4h | Globalni izbor (0/5/15/30/60/240), povijest do 22.000 1m svijeća (paging), time-stop skaliran | TEST |
| AI provideri | jedan provider (Groq/Gemini/kompat.) | Nema failovera, kvota, health checka | Groq → Gemini → OpenRouter (zasebne implementacije), modeli iz /models svakog providera, retry, cool-down na 429, kvote iz headera, tokeni po danu, scrub ključeva, health check, sve dolje → NO TRADE | TEST (mock HTTP) |
| Independent Trader | — | — | Samo tržišni paket (1m/5m/15m indikatori, volume profile, book, funding, OI, S/R, breakout), JSON odluke validirane, spremljene prije supervisora, shadow knjiga `ai_trader` | TEST (mock AI) |
| AI Supervisor | — | — | Pravila (duplikat, korelacija, nelogičan SL/TP, zastarjelo, niz gubitaka, podaci) + AI odluka APPROVE/REJECT/WAIT/NO_TRADE/EMERGENCY_STOP + leverage cap; odobrenje nikad ne zaobilazi risk engine | TEST (mock AI) |
| Tok naloga | bot → supervisor (pravila) → risk → execution | AI sloj nije postojao | AI SIGNAL / bot / TradingView → (AI) SUPERVISOR → RISK ENGINE → EXECUTION → BINANCE | TEST |
| Šest agenata | rade po pravilima bez ključa | — | Koriste lanac providera kad je konfiguriran | TEST |
| Moneyline v3.5 | slot 9 = Range Expansion „zamjena“ | Spec 1.9 zabranjuje zamjenu | Range Expansion je sada samostalna strategija; Moneyline **nije implementiran** (nema specifikacije), `GET /api/strategies/moneyline` | NIJE (čeka specifikaciju) |
| TradingView | — | — | Webhook: tajni ključ ili HMAC, ±60 s, dedup ID, validacija simbola/timeframea/strane, log, limit 30/min; ide kroz supervisor + risk | TEST |
| Binance REST | potpisani REST, algo stopovi, rekoncilijacija, idempotentni clientOrderId | — | Balance check; ostalo zadržano | TEST (fake exchange) + RUČNO (demo preflight na mobitelu) |
| Binance WebSocket | nije postojao (REST polling svake sekunde) | Spec traži WS | **NIJE** u 1.9.0: polling + rekoncilijacija na svakoj svijeći pokriva stanje; WS user-data stream je zaseban posao | NIJE |
| Binance demo testovi iz CI-ja | — | GitHub runneri (SAD) dobivaju HTTP 451 | Demo preflight se pokreće iz aplikacije (Admin) | RUČNO |
| Android Command Center | — | — | Provideri, kvote, tokeni, greške, trader/supervisor, 6 agenata, povijest odluka, kontrole, emergency stop | CI (build + UI test) |
| Performance Auditor | metrike u backtestu i po botu | Nije odvajao stvarno/simulirano u jednom pregledu | Zaseban modul, snapshot svakih 15 min, real vs simulated, po strategiji/instrumentu/režimu, AI vs botovi na istom prozoru | TEST |
| VPS | Docker + Caddy + cloud-init, SQLite WAL, backup CLI | Nema healthchecka, rotacije logova, automatskog backupa | Compose healthcheck, rotacija logova, memory limit, dnevni backup servis, CPU/RAM u /api/health | CI (compose config + docker smoke) |
| PostgreSQL / Redis | — | Spec traži | **NIJE**: jedan proces, SQLite WAL s append-only audit triggerima; prelazak na Postgres bez stvarne potrebe nosi rizik regresije | NIJE (odluka; opisano u VPS vodiču) |
| Android kao 24/7 server | engine u aplikaciji | Android može ugasiti proces | Dokumentirano: VPS je preporuka za 24/7; aplikacija prati VPS | — |
| Potpisivanje APK-a | debug ključ za testne buildove | Produkcijski ključ nije dostavljen | Nepromijenjeno: release ostaje nepotpisan bez vlasnikovog keystorea | — |
| Profitabilnost | backtest/walk-forward | Svi botovi OOS negativni | Nema promjene strategija bez dokaza; istraživanje timeframeova: 0/36 prolazi | CI-REAL |

## Glavni rizici koji ostaju
1. Nijedna strategija nema dokazanu prednost nakon troškova (walk-forward 2025-01 – 2026-06).
2. Leverage ne povećava očekivanu dobit kod pozicije računate iz rizika; mijenja samo maržu i blizinu likvidacije.
3. Besplatni AI provideri imaju male i promjenjive kvote; kada ih nestane, AI dio prelazi u NO TRADE.
4. TradingView webhook zahtijeva javni HTTPS (VPS).
