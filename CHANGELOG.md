# CHANGELOG

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
