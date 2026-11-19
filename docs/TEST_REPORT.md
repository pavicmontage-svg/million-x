# Izvještaj o testiranju (v1.1)

Ovdje su samo testovi koji su **stvarno pokrenuti**. Za svaki je naveden broj i mjesto izvođenja.

## Backend (pytest), 87/87 prošlo

- Lokalno (Python 3.13): `87 passed`.
- GitHub Actions (Python 3.12), commit `845cb99`: `87 passed in 55.58s`.

| Datoteka | Testova | Područje |
|---|---|---|
| `test_execution_safety.py` | 15 | djelomično popunjenje, nepoznat status (popunjen / nije postavljen), stop odbijen ili neprovjerljiv → zatvaranje s provjerom, neuspjelo zatvaranje → eskalacija i blokada, smanjenje veličine po stvarnoj cijeni, siroče pozicije, vraćanje nestalog stopa, rekoncilijacija nakon restarta, emergency stop nad svim pozicijama na burzi, istek odobrenja, ponovna provjera cijene pri odobrenju, učestalost naloga, live bez odobrenja |
| `test_governance_gate.py` | 22 | granice svih parametara (9 parametriziranih slučajeva), pravila između polja, config ne može zaobići granice, 12 provjera prije trgovine, live gate (uvjeti, nesigurni ključevi ×3, kapital, istek, promjena konfiguracije, rotacija ključa, zastario demo preflight) |
| `test_risk_and_exits.py` | 12 | dimenzioniranje, odbijanje 4 EUR, leverage cap, dnevni gubitak i DD, niz gubitaka, tighten-only, blokade, logika izlaza |
| `test_security_org.py` | 7 | nepromjenjivi audit i trades, Fernet, uparivanje, RBAC, verzije promptova, offline način, nalazi bez dokaza, tighten-only |
| `test_server_recovery.py` | 7 | migracija v1 → v2 bez gubitka podataka, backup i rotacija, throttling uparivanja, istek tokena, dozvole datoteke s master ključem, watchdog, live se ne nastavlja nakon restarta |
| `test_engine_paper.py` | 6 | paper engine na lažnom tržištu end-to-end |
| `test_strategies.py` | 5 | bez look-aheada, geometrija signala, 10 različitih modula, slot 9 označen, Bot 10 miruje bez dokaza |
| `test_validation.py` | 5 | prihvatljivost bez WF-a, shadow trgovine se ne računaju, istek validacije, degradacija, snižavanje statusa u live načinu |
| `test_backtest.py` | 4 | računovodstvo, nema dobiti na nasumičnim podacima, WF odbija, MC traži podatke |
| `test_org_board.py` | 2 | dnevni red 1–8, rasprava, CRO veto (tvrdo pravilo + agent), međuagentska komunikacija, svaki zapis odgovara stvarnom pozivu, offline sastanak bez tokena |
| `test_api.py` | 2 | autentikacija i RBAC preko HTTP-a, odbijanje limita izvan granica, live zaključan, health iskreno javlja stanje, rate limit 429 |

## Android

| Što | Gdje | Rezultat |
|---|---|---|
| Kompilacija debug APK-a | GitHub Actions, `845cb99` | `BUILD SUCCESSFUL` |
| Kompilacija release APK-a (bez ključa → nepotpisan) | GitHub Actions, `845cb99` | `BUILD SUCCESSFUL` |
| JVM unit testovi (`JsonHelpersTest`, 3 testa) | GitHub Actions, `testDebugUnitTest` | task izvršen, build uspješan (od sljedećeg builda log ispisuje svaki test pojedinačno) |
| Instrumentirani UI testovi (`UiSafetyTest`, 4 testa) | GitHub Actions, emulator Android 14 (API 34, x86_64) | `Finished 4 tests`, 0 failed |

UI testovi provjeravaju sljedeće:
- emergency stop traži potvrdu i tek je onda izvršava;
- globalna traka prikazuje „SERVER UNREACHABLE“ i emergency stop ostaje dostupan;
- uparivanje nije moguće bez koda;
- stanje greške nudi gumb Retry.

## Nije testirano (vidi `LIMITATIONS.md`)
- Pravi Binance Demo/Live (radno okruženje nema mrežni pristup; to potvrđuje *demo preflight* na tvom serveru).
- Pravi Claude API (testirano s lažnim LLM-om).
- Fizički Samsung Galaxy S25 Ultra.
- Backtest i walk-forward na stvarnim povijesnim podacima.
