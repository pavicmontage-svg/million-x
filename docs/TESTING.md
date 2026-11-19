# Testiranje

## Automatski testovi backenda (`backend/tests`, 87 testova; rezultati u `TEST_REPORT.md`)

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q tests
```

| Područje | Što se provjerava |
|---|---|
| Strategije | nema look-aheada (signal je isti s budućim podacima i bez njih); geometrija SL < ulaz < TP; 10 različitih modula; slot 9 označen kao zamjena; Bot 10 miruje bez validiranih podataka |
| Risk | rizik uključuje troškove i iznosi ≤ 0,5 %; odbijanje 4 EUR zbog minimuma s objašnjenjem; leverage cap prema volatilnosti; dnevni gubitak, max drawdown, niz gubitaka i hlađenje; tighten-only; blokada kod emergency stopa, ustajalih podataka i prekida veze |
| Izlazi | stop prije cilja u istoj svijeći, izlaz po otvaranju kod gapa, djelomični TP → BE → trailing, vremenski stop |
| Backtest | računovodstvo (završni kapital = početni + Σ neto), svaka trgovina plaća naknade, shadow knjiga, **nema dobiti na nasumičnim podacima**, walk-forward odbija nevalidirane botove, Monte Carlo traži dovoljno podataka |
| Engine (paper, lažno tržište) | ulazi s exchange-side stopom, logiranje odluka, ispravan audit lanac, blokada kod ustajalih podataka i prekida veze, emergency stop zatvara pozicije i ostaje upamćen, zaštita od duplog naloga, ručni način ne šalje naloge bez odobrenja, live zahtijeva otključavanje |
| Sigurnost / organizacija | audit i trades su nepromjenjivi; Fernet šifriranje; jednokratni kodovi za uparivanje; RBAC; verzioniranje promptova; deterministički način bez LLM-a; nalazi bez dokaza označeni; automatski se primjenjuje samo pooštravanje i samo od agenata s tim pravom; board meeting bilježi neslaganja |
| API | autentikacija, RBAC preko HTTP-a, odbijanje leveragea > 20, live zaključan |

Napomena: sintetički podaci služe **samo** za testiranje softvera i ne govore ništa o profitabilnosti.

## Ručni testovi prije live

1. **Paper (24–72 h):** pratiti WebSocket događaje, odbijene signale (razlozi), shadow knjigu i ponašanje kod restarta (pozicije i emergency stop se vraćaju).
2. **Demo:** spremiti demo ključeve → način rada `demo` → ručno odobravanje. Na Binance Demo sučelju provjeriti da svaka pozicija ima STOP_MARKET (Algo order), da se nakon TP1 stop pomiče na BE i da se trailing pomiče.
3. **Prekid veze:** blokirati izlaz servera prema Binanceu na 2 minute (firewall). Očekivano: upozorenje „Exchange connection lost“, bez novih ulaza, stopovi ostaju na burzi.
4. **Emergency stop iz aplikacije:** sve pozicije zatvorene, nema novih ulaza, stanje upamćeno i nakon `docker compose restart`.
5. **Demo preflight** (sve provjere moraju proći), zatim **live key check** s pravim live ključem bez withdrawala i s IP ograničenjem.
5a. **Rekoncilijacija:** na Binance Demo ručno otvori malu poziciju izvan aplikacije. Unutar jedne minute mora se pojaviti kritični incident „orphan_position“ i stop na burzi.
6. **Validacija botova:** walk-forward na najmanje 2–3 godine 1m podataka za više simbola. Botovi koji ne prođu ostaju `RESEARCH` i ne smiju u live.

## Android (automatski: unit + UI testovi u GitHub Actionsu, vidi `TEST_REPORT.md`)

- Prva izgradnja: GitHub Actions (`android-apk` job). Ako izgradnja ne uspije, pošalji log; kod je samo sintaktički provjeren jer Android SDK nije bio dostupan.
- Ručno na S25 Ultra: uparivanje, biometrijsko otključavanje, zaključavanje nakon 60 s u pozadini, živi događaji (indikator „live“), odobravanje signala, emergency stop (haptika), uređivanje prompta (nova verzija), audit verify.
