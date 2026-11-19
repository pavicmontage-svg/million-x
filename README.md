# MILLION-X AI

Autonomni sustav za scalping na Binance USDⓈ-M Futuresu: 10 strategija, centralni supervisor, neovisni risk manager, 6 AI mozgova i 5 izvršnih AI uloga. Upravlja se iz nativne Android aplikacije (Kotlin, Jetpack Compose, Material 3).

> **Važno.** Cilj od 1.048.576 EUR iz 4 EUR je eksperiment, ne prognoza. U kodu nema izmišljenih rezultata. Sve metrike računaju se isključivo iz stvarnih trgovina (backtest na stvarnim povijesnim podacima, paper, demo ili live). Leverage futures može izgubiti cijeli uloženi kapital.

## Što je u repozitoriju

| Mapa | Sadržaj |
|---|---|
| `backend/` | Python servis koji radi 24/7: trading engine, 10 botova, supervisor, risk, backtest/walk-forward/Monte Carlo, Binance adapter, SQLite, REST + WebSocket API, AI organizacija, FCM |
| `android/` | Android Studio projekt (Kotlin/Compose, MVVM), cilj Samsung Galaxy S25 Ultra |
| `.github/workflows/build.yml` | GitHub Actions: pokreće backend testove i gradi APK |
| `docs/` | Arhitektura, deployment, testiranje, **izvještaj o testovima**, **sigurnosni audit**, **preostala ograničenja**, analiza 4 EUR |

## Brzi početak

1. **Backend na serveru** (VPS s Dockerom): `docs/DEPLOYMENT.md`
2. **APK**: svaki push na `main` izgradi APK i objavi ga na grani `apk` repozitorija (datoteka `...-DEBUG-not-for-production.apk`; potpisani release tek uz tvoj ključ).
3. **Uparivanje**: na serveru pokreni `docker compose exec backend python -m millionx.cli pair`, a u aplikaciju upiši URL servera i dobiveni kod.
4. **Redoslijed rada**: `paper` → `demo` (Binance Demo Trading) → walk-forward validacija → paper dokazi → demo preflight → live key check → vremenski ograničeno odobrenje vlasnika → `live`. Ništa od toga se ne događa automatski.

## Stanje i ograničenja (iskreno)

- **Binance i EU:** od 1. 7. 2026. Binance je za korisnike u EEA-u zaustavio nove naloge (MiCA). Iz Hrvatske live na Binance Futuresu najvjerojatnije neće raditi. Demo Trading se može koristiti za testiranje. Burza je zaseban adapter (`backend/millionx/exchange/`), pa se kasnije može dodati licencirana EU burza.
- **Bot 9:** javna definicija za „Moneyline Breakout Entry v3.5“ nije pronađena. Slot 9 zato vrti dokumentiranu *Range Expansion Breakout* strategiju koja je u aplikaciji jasno označena kao zamjena. Kad pošalješ Pine Script izvornik, implementirat će se originalna strategija.
- **4 EUR:** uz pravilo od 0,5 % rizika trgovanje nije izvedivo (vidi `docs/FEASIBILITY_4EUR.md`). Sustav takve naloge odbija i objašnjava zašto.
- **AI mozgovi:** savjetodavni su. Ne otvaraju trgovine, a automatski smiju samo pooštriti rizik. Bez `ANTHROPIC_API_KEY` rade samo deterministički, uz oznaku „LLM offline“.
- **Android:** kompilira se u GitHub Actionsu (debug i release); unit testovi i 4 UI testa na emulatoru (Android 14) prolaze. Produkcijski potpisan APK zahtijeva tvoj ključ (`docs/DEPLOYMENT.md` §6).
- **Backtest rezultati:** nisu priloženi jer ih ovdje nije bilo moguće izračunati (nema pristupa podacima). Pokreću se na tvom serveru.
