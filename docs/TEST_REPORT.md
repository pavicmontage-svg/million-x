# TEST REPORT (1.9.0)

Ovdje su samo testovi koji su **stvarno pokrenuti**, s mjestom izvođenja.

## Backend (pytest): 121 / 121 prošlo
- Lokalno (Python 3.13): `121 passed`.
- GitHub Actions, commit `9d053da`: job `backend-tests` (Python 3.12) `121 passed`; job `backend-tests-android-versions` (iste verzije paketa kao u APK-u: numpy 1.26.2, cryptography 42.0.8) `121 passed`.

| Datoteka | Testova | Što pokriva (zahtjev iz 1.9.0 §12) |
|---|---|---|
| `test_ai_desk.py` | 8 | AI provider failover, iscrpljena kvota (429 → cool-down, preskakanje), neispravan JSON → sljedeći provider, svi provideri dolje → NO TRADE, šifrirani i očišćeni ključevi, validacija odluka tradera, trader → supervisor → risk engine → izvršenje s leverage capom, odbijanje, APPROVE bez razloga |
| `test_activity_api.py` | 13 | Live aktivnost, svijeće, natjecanje, AI model, Binance račun (mock), dijagnoza, profili rizika (MAX samo virtualni novac), timeframe za sve botove, time-stop, TradingView sigurnost i tok |
| `test_risk_and_exits.py` | 15 | Risk engine: 500 EUR / 0,5 % / 2 pozicije, razine leveragea (sam confidence ≠ 20x), likvidacijski razmak, dnevni limit 10 EUR, izlazi |
| `test_execution_safety.py` | 16 | Duplikati naloga, nepoznat status, djelomična popunjenja, stopovi, rekoncilijacija, emergency stop, saldo s Binancea prije naloga |
| `test_governance_gate.py` | 13 | Tvrde granice (i leverage_tiers), pre-trade provjere, live gate |
| `test_engine_paper.py` | 8 | Paper trading end-to-end, prekid veze / zastarjeli podaci blokiraju ulaze, rangiranje po stvarnom volumenu, paging povijesti |
| `test_server_recovery.py` | 10 | Oporavak baze (migracije v1→v3, backup), watchdog, uparivanje, port |
| `test_security_org.py` | 7 | Audit lanac, Fernet, RBAC, AI agenti tighten-only |
| `test_strategies.py` | 6 | Bez look-aheada, minimalni stop zbog naknada, bot 9 nije Moneyline |
| `test_validation.py` / `test_backtest.py` | 5 / 4 | Walk-forward, Monte Carlo, backtest računovodstvo |
| `test_data_download.py` | 2 | Preuzimanje povijesnih podataka (mjesečni + dnevni) |
| `test_org_board.py` / `test_api.py` | 2 / 2 | Board meeting, HTTP autentikacija |

## Android (GitHub Actions, commit `9d053da`)
| Što | Rezultat |
|---|---|
| Debug APK (arm64) i release (nepotpisan bez vlasnikovog ključa) | `BUILD SUCCESSFUL` |
| JVM unit testovi | prošli (`testDebugUnitTest`) |
| Instrumentirani testovi na emulatoru API 34 (UI sigurnost + ugrađeni Python backend: start, health, uparivanje, 10 botova) | `Finished 5 tests on emulator-5554`, `BUILD SUCCESSFUL` |
| Docker: `docker compose config` + build + pokretanje + health | `docker-smoke` uspješan |

## Na stvarnim podacima (GitHub Actions)
| Test | Rezultat |
|---|---|
| Walk-forward 9 botova × 5/15/30/60 min, 2025-01 – 2026-06, BTC/ETH/SOL/XRP | 0 / 36 prolazi aktivacijske kriterije; svi OOS negativni (grana `research`) |
| Leverage 5/10/15/20x, isti signali, 1 h, 2025-07 – 2026-10, s naknadama, slippageom, fundingom i isolated likvidacijom | 324 trgovine na svakoj razini, neto −85,93 USDT, PF 0,66, max DD 17 %, 0 likvidacija — identično na svim razinama (grana `research-leverage`) |

## Nije pokrenuto (i zašto)
| Test | Razlog |
|---|---|
| Binance Demo end-to-end iz CI-ja | GitHub runneri su u SAD-u (Binance vraća 451). Pokreće se iz aplikacije: Admin → „Run demo preflight“ (stvarni nalozi na demo računu). |
| Stvarni pozivi Groq/Gemini/OpenRouter | U testovima je HTTP mockiran; stvarni ključ testira se u aplikaciji (AI Command Center → Test). |
| WebSocket | Nije implementiran u 1.9.0 (vidi AUDIT_REPORT). |
| Instalacija APK-a 1.9.0 na S25 Ultra | Ovdje nemam fizički uređaj; instalacija i pokretanje provjereni su na emulatoru. |
