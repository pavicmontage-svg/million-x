# CHANGELOG

## 2.2.0 — Continuous Directional Scalping Engine (grana `v2.2`; v2.0 i v2.1 netaknuti)
### Engine
- **Continuous Opportunity Scanner** (`scalp/live.py`):
  - analizira 10 instrumenata svake sekunde, neovisno o otvorenim pozicijama;
  - radi kao listener 2.1 collectora (WebSocket /public + /market);
  - egzaktni predfiltar daje ≈ 0,1 ms medijan po sekundi.
- **Hijerarhija:** 1 s ulaz/izlaz, 1m, 3m momentum, 5m trend, 15m kontekst (ne blokira). **60m se ne koristi.**
  Režimi: trending, ranging, high/low vol, momentum expansion, liquidity sweep, dislocation.
- **Engines:**
  - A Momentum (brzina, akceleracija, rel. volumen, CVD akceleracija, taker omjer, proboj, iscrpljenost);
  - B Pullback (EMA9/21, VWAP, dubina, kontrakcija volumena, povratak momenta + CVD, ulaz na proboj);
  - C Order Flow (samo stvarna knjiga; bez nje INCOMPLETE).
- **Opportunity Engine:**
  - kalibrirani očekivani pomak, trajanje, bruto, naknade ulaz/izlaz, spread, slippage, funding, neto, R/R, kvaliteta podataka;
  - **ranking po neto novcu (R/EUR), ne po win rateu** (odluka vlasnika);
  - razlozi ACCEPT/REJECT.
- **Upravljanje pozicijom:** početni stop, ciljevi 2R/3R/5R, break-even (nikad iznad tržišta), trailing,
  exhaustion i order-flow reversal izlazi, max 15 min (45 min samo za poziciju u trailingu).
- **Troškovi:** taker zadano; maker samo uz potvrđen fill (trade-through, ≤ 10 % prometa); djelomični fillovi; latencija; ispadi.
- **Virtualni PaperBook:**
  - Risk Engine limiti nepromijenjeni (0,5 %, 2 pozicije, 10 EUR/dan, ISOLATED, ≤ 20×, likvidacija ≥ 3× stop);
  - shadow ishodi za missed opportunities;
  - **nikakvi Binance nalozi**.
### Istraživanje (CI run 44881204071, 10/10 instrumenata)
- Event-driven 1 s simulacija na aggTrades, 2025-01 – 2026-06; 1 148 645 kandidata.
- **Presuda NO_TRADE** (A, B, A+B):
  - bruto ≈ 0 R, troškovi 0,4–3,3 R po trgovini;
  - 0 prihvaćenih na validaciji;
  - aktivacijski kriteriji nepromijenjeni.
### API / Android
- `/api/scalp/status|opportunities|paper|research`.
- Ekran **Scalping**: skener, LONG/SHORT prilike rangirane po očekivanom neto EUR uz razloge (✓/✗), paper rezultati
  (bruto/neto, naknade, trajanje), latencija, vrijeme odluke, research presuda.

## 2.1.0 — Market Microstructure Data Intelligence (grana `v2.1`; `v2.0` netaknut)
### Podaci
- Audit dostupnosti stvarnim zahtjevima prema Binanceu (`DATA_AVAILABILITY_REPORT.md`):
  - postoje funding, OI (5 min), bookDepth (±%, 30 s), aggTrades i premium/mark 1m;
  - **nema likvidacija za 2025–2026**; bookTicker završava 2024-03;
  - REST OI samo 30 dana, aggTrades samo 48 h.
- Ingestija arhive (`micro/archive.py`): 10 instrumenata, 2024-12 – 2026-06, kauzalno poravnano na 1 min, s izvještajem kvalitete. Holdout se odbija.
- Live collector (`micro/collector.py`, stdlib WebSocket):
  - depth20@500ms, aggTrade, forceOrder (donja granica), markPrice@1s i OI poll;
  - 1-sekundni redovi u zasebnoj `micro.db`;
  - quality eventi, reconnect, retencija, backup;
  - Docker servis `collector`.
- Unified snapshot (`micro/snapshot.py`): timestamp, izvor, kvaliteta, svježina i oznaka nedostajućeg za svako polje. Zastarjeli podaci se nikad ne koriste kao aktualni.
- Data Quality Engine (`micro/quality.py`): praznine, duplikati, redoslijed, drift, nevaljale/abnormalne vrijednosti, mapiranje simbola, INCOMPLETE.
### Istraživanje
- A–F (`micro/research.py`):
  - discovery 2025-01 – 2025-09 samo za smjer, validacija 2025-10 – 2026-06;
  - 96 testova, BH-FDR, BASE/STRESSED troškovi.
- **0 kandidata, NO TRADE.** Najveći bruto učinak je +5,1 bps (CVD apsorpcija, fade), a trošak 14 bps.
- C (likvidacije) je INCOMPLETE.
### Strategije
- Nijedna nova strategija nije razvijena ni aktivirana (`NEW_STRATEGY_REPORT.md`).
- Activation kriteriji, Risk Engine i holdout nepromijenjeni; live ostaje zaključan.
### API / Android
- `/api/micro/status|collector|snapshot/{sym}|series/{sym}|research`.
- Ekran **Research Data**:
  - live snapshot s kvalitetom po polju, grafovi knjige/spreada/CVD-a/fundinga/OI-ja, likvidacije;
  - prekidač snimanja;
  - kvaliteta podataka, povijesna pokrivenost, rezultati A–F.
### CI
- Workflowi `micro` (availability), `micro-data` (ingestija, matrica 10 instrumenata), `micro-research`.
- APK-ovi grana v2.x objavljuju se na `apk-<grana>`.

## 2.0.0 — Quantitative Strategy Research, Optimization & Edge Engine (grana `v2.0`; 1.9.0 ostaje netaknut na `release-1.9.0`)
### Istraživanje
- Faza 1: forenzički izvještaj svih 9 strategija × 3 TF-a na 10 instrumenata. Rezultat: 0/27 pozitivno nakon troškova, 25/27 negativno i prije troškova (`docs/STRATEGY_FORENSIC_REPORT.md`).
- Regime Intelligence 2.0 (10 kauzalnih režima iz 1h/15m/5m/1m).
- Research Engine 2.0:
  - ugniježđeni walk-forward s purged 3-fold CV i embargom;
  - cross-asset, long/short i raščlamba po režimima;
  - block bootstrap CI, Monte Carlo, Deflated Sharpe (svi pokušaji), PBO (CSCV), stabilnost parametara;
  - troškovni scenariji BASE/STRESSED/EXTREME (spread, latencija, odbijeni nalozi, funding).
- Strategy Selection Engine po instrumentu i režimu (shrinkage, raspad, CI, penali korelacije/DD/turnovera, NO TRADE fallback).
- Activation 2.0: postojeći kriteriji kao minimum, plus CI > 0, DSR ≥ 0,95, PBO ≤ 0,5, stabilnost, cross-asset, ≥ 2 režima, STRESSED > 0, Research 2.0 presuda i ≥ 30 dana paper trgovanja.
### Strategije
- v2 verzije bot1–bot9 kao zasebne verzionirane klase (v1 netaknut); bot3 v2 je označen kao CVD-only u backtestu, a kad live order book nedostaje, nema ulaza.
- Istraživački prototipovi A–D (Trend Pullback, Liquidity Sweep Reversal, Volatility Breakout Retest, Cost-Aware Momentum), samo za istraživanje.
### AI
- AI je samo savjetodavan: ideje Independent Tradera se bilježe, ne izvršavaju; to nije postavka.
### Android
- Ekran Research 2.0: najbolja strategija po instrumentu i režimu (ili NO TRADE), metrike i CI po strategiji, research-only i suspendirani botovi, upozorenja o raspadu, povijest parametara.
### CI
- `research2.yml` (matrica 23 strategije), `research-forensic.yml`; APK grane `v2.0` objavljuje se na granu `apk-v2.0` (1.9.0 APK ostaje na `apk`).

## 1.9.0 — 20. 11. 2026.
### Risk Engine
- Kapital 500 EUR (`capital_mode=pro`, zadano), 0,5 % rizika (2,50 EUR), dnevni limit 10 EUR i 2 %, najviše 2 pozicije.
- Leverage 5x/10x/15x/20x po kvaliteti setupa; sam confidence nikad ne daje 20x; zaštita od likvidacije (≥ 3× udaljenost stopa), ISOLATED margina.
- Veličina pozicije uključuje očekivani funding; stvarni Binance saldo provjerava se prije svakog naloga.
- Backtest simulira isolated likvidaciju; `python -m millionx.research leverage` uspoređuje 5/10/15/20x.
- Izbor timeframea za sve botove (5/15/30 min, 1 h, 4 h); time-stop se skalira s timeframeom.

### AI
- Lanac providera Groq → Gemini → OpenRouter (samo besplatni modeli) s failoverom, kvotama, retryjem, health checkom i šifriranim ključevima.
- Independent Trader (samo tržišni podaci, odluke spremljene zasebno, shadow knjiga `ai_trader`).
- AI Supervisor (APPROVE / REJECT / WAIT / NO_TRADE / EMERGENCY_STOP + odobreni leverage) ispred risk enginea.
- Šest agenata i izvršni direktori koriste lanac providera kada je konfiguriran.

### Integracije
- TradingView webhook (tajni ključ ili HMAC, ±60 s, dedup, validacija, log).
- Bot 9 je samostalna Range Expansion strategija; Moneyline v3.5 nije implementiran (čeka specifikaciju).

### Android
- AI Command Center, povijest AI odluka, Performance Auditor, izbor kapitala 500 EUR i timeframea.

### Infrastruktura
- Docker Compose: healthcheck, rotacija logova, memory limit, dnevni backup; CPU/RAM u `/api/health`.
- DB migracija v3 (`ai_decisions`, `ai_errors`, `perf_snapshots`, `tv_signals`).

## 1.8.0 i ranije
- Vidi git povijest (`git log`): lokalni engine na mobitelu, Live ekran, Binance račun, profili rizika, backtest s automatskim preuzimanjem podataka, istraživanje timeframeova.
