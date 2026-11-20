# CHANGELOG

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
