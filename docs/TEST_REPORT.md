# TEST REPORT — MillionX 2.1.0 (grana `v2.1`)

Svi rezultati ispod su iz stvarno pokrenutih testova. Ništa nije pretpostavljeno.

## Backend (pytest)
- **Lokalno** (Python 3.12, numpy 2, pandas): 159/159 prošlo.
- **CI `build` run 44843853448** (commit `00c7006`):
  - `backend-tests`: **159 passed**;
  - `backend-tests-android-versions` (paketi kao u APK-u, bez pandas): **157 passed, 2 skipped**. Preskočena su dva
    istraživačka testa kojima treba pandas, koji se ne pakira u aplikaciju;
  - `docker-smoke`, `android-apk` i `android-ui-tests` (emulator): uspjeh.
- **Novi testovi 2.1:**
  - `test_micro_quality.py` (5): svježe/zastarjelo/nedostaje/budući timestamp, INCOMPLETE bez kritičnih izvora,
    praznine/duplikati/redoslijed/nevaljalo, abnormalni skokovi, mapiranje simbola;
  - `test_micro_collector.py` (7): značajke knjige (zidovi, impact, uklanjanje likvidnosti), agregacija po sekundi,
    duplikati/praznine u aggTrade ID-jevima, out-of-order depth, krivi simbol na streamu, prekrižena knjiga,
    zastarjela knjiga se ne zapisuje, WebSocket okviri (ping/pong, fragmenti, close, maskiranje), rute /public i /market,
    snapshot s kvalitetom po polju i oznakom donje granice za likvidacije;
  - `test_micro_archive.py` (3): kašnjenje dostupnosti i istek vrijednosti, parsiranje vremena, **odbijanje holdouta**;
  - `test_micro_research.py` (3): BH-FDR, trgovine bez preklapanja, kauzalni percentil, end-to-end na nasumičnim
    podacima **bez lažne prednosti**;
  - `test_micro_api.py` (1): `/api/micro/*`, RBAC (viewer ne može uključiti snimanje).

## Na stvarnim podacima (GitHub Actions)
| run | što | rezultat |
|---|---|---|
| 44838758141 | audit dostupnosti (10 instr. × 9 dana × 11 skupova + REST) | `DATA_AVAILABILITY_REPORT.md` |
| 44839902719 | ingestija 10 instrumenata 2024-12 – 2026-06 (matrica) | 10/10 uspjeh, `DATA_QUALITY_REPORT.md` |
| 44841732631 | istraživanje A–F, 96 testova | 0 kandidata, `MICROSTRUCTURE_RESEARCH_REPORT.md` |
| 44843853802 | collector uživo, 3 min (stari, nerutirani URL) | stigao samo depth: **otkrivena greška**, Binance od 2026-03 rutira streamove po putanji |
| 44844375356 | collector uživo, 3 min, nakon ispravka (/public + /market) | depth 711, aggTrade 4453, markPrice 358, forceOrder 4 poruke; 358 redova knjige, 355 trades, 352 funding, 4 likvidacije |

## Nije pokrenuto ili nije moguće (i zašto)
- **OI poll uživo s CI-ja:** HTTP 451, jer Binance REST blokira SAD regiju CI runnera.
  Iz Hrvatske REST radi (aplikacija ga koristi od 1.4.0). Na mobitelu/VPS-u treba potvrditi u ekranu Research Data.
- **Istraživanje likvidacija (C):** nema povijesnih podataka, INCOMPLETE.
- **Dugotrajno snimanje (dani/mjeseci):** pokreće se na VPS-u (`docker compose up -d collector`) ili u aplikaciji (gumb Record).
- **Stvarni nalozi:** nisu slani. Live je zaključan, a risk i aktivacijski kriteriji nisu mijenjani.

---

# TEST REPORT (1.9.0) — sačuvano

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
