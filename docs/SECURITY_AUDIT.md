# Sigurnosni i inženjerski audit (v1.0 → v1.1)

Audit je obuhvatio postojeći kod (backend, Android, CI). Za svaki nalaz naveden je uzrok, ispravak i test koji ga pokriva. Ozbiljnost: **K** = kritično (može ostaviti otvorenu poziciju bez zaštite ili izgubiti trag o njoj), **V** = visoko, **S** = srednje, **N** = nisko.

## Izvršenje naloga (Faza 1)

| # | Ozb. | Nalaz u v1.0 | Ispravak u v1.1 | Test |
|---|---|---|---|---|
| 1 | K | Djelomično popunjen market nalog vraćao je „not filled“, a popunjeni dio ostajao je otvoren **bez stopa** | Ishod se razrješava upitom na burzu, ostatak naloga se otkazuje, a zaštita se postavlja odmah na stvarnu popunjenu količinu | `test_partial_fill_protects_actual_quantity` |
| 2 | K | Status `UNKNOWN` samo je slao upozorenje; nalog je mogao biti popunjen, a pozicija nezaštićena | Ishod se provjerava prvo upitom po `clientOrderId`, a zatim preko stvarne pozicije na burzi. Nalog se nikad ne šalje ponovno naslijepo; rekoncilijacija nastavlja provjeru | `test_unknown_status_resolved_from_exchange_position`, `test_unknown_status_not_placed_is_never_resent` |
| 3 | V | Zaštita od duplikata radila je SELECT pa INSERT, što nije atomarno | `claim_order`: atomarni `INSERT OR IGNORE` prije slanja | `test_duplicate_entry_prevented`, `test_unknown_status_not_placed_is_never_resent` |
| 4 | V | Stop je bio postavljen, ali nikad provjeren | Stop se verificira (`GET /fapi/v1/algoOrder`, rezervno `openAlgoOrders`), uz jedan ponovni pokušaj. Ako verifikacija ne uspije, slijedi zatvaranje s **provjerom da je pozicija ravna**; ako ni to ne uspije, otvara se kritični incident i blokiraju svi novi ulazi | `test_stop_rejected_triggers_verified_close`, `test_stop_unverifiable_triggers_close`, `test_close_failure_escalates_and_blocks_entries` |
| 5 | S | Neuspjeh TP naloga nije se pratio | Neuspjeh se bilježi kao incident; engine tada sam vodi ciljeve iz podataka svijeća | (kod: `place_targets`) |
| 6 | K | Nije postojala rekoncilijacija s burzom; nakon restarta ili ručne intervencije sustav je mogao izgubiti trag o poziciji | Rekoncilijacija na startu i na svakoj svijeći. „Siroče“ pozicija dobiva zaštitni stop (2×ATR ili 1 %); nestali stop se vraća; vanjsko zatvaranje se evidentira; neslaganje smjera dovodi do zatvaranja | `test_reconcile_adopts_orphan_and_restores_missing_stop`, `test_startup_reconciliation_after_restart` |
| 7 | V | Odobrenje nakon isteka se ispravno odbijalo, ali je nalog koristio staru veličinu i cijenu | Istek se provjerava pri samom odobrenju. Pozicija se ponovno dimenzionira po trenutnoj cijeni, a governance zatim provjerava pomak cijene i R:R. Odobrenja ne preživljavaju restart | `test_manual_approval_expires`, `test_approval_rechecks_price_drift` |
| 8 | S | Rizik se računao po cijeni signala | Nakon popunjavanja rizik se računa po stvarnoj cijeni: višak se odmah smanjuje, popunjenje iza stopa se zatvara, a prekoračenje slippagea otvara incident | `test_size_reduced_when_actual_fill_risk_too_high` |
| 9 | V | Emergency stop zatvarao je samo interne pozicije, bez provjere | Zatvara se **sve što burza prijavi**, uz provjeru da je svaki simbol ravan; nalozi se otkazuju; nepotvrđeni simboli prijavljuju se kao kritično | `test_emergency_stop_closes_everything_on_exchange` |

## Upravljanje rizikom (Faza 2)

| # | Ozb. | Nalaz | Ispravak | Test |
|---|---|---|---|---|
| 10 | V | Limiti su se preko API-ja provjeravali samo dvjema provjerama, a config datoteka nikako | `governance.BOUNDS`: tvrde granice za **svaki** parametar i pravila između polja. Promjena je sve-ili-ništa i provjerava se u API-ju, configu i pri učitavanju spremljenih limita | `test_out_of_bounds_values_are_refused`, `test_config_file_cannot_bypass_bounds` |
| 11 | V | Nisu postojali limiti učestalosti naloga, korelirane izloženosti, slippagea i minimalne likvidnosti | Dodani su, a provjera prije trgovine (`pre_trade`) radi **neposredno prije svakog stvarnog naloga**, sa svježom cijenom i listom pozicija s burze | `test_pre_trade_checks`, `test_governance_blocks_order_frequency` |
| 12 | – | AI mogao samo pooštriti rizik | Zadržano; dodatno se svaka odluka CEO-a provjerava tvrdim pravilom (loosen → veto) | `test_only_tightening_is_auto_applied`, `test_agenda_order_discussion_and_cro_veto` |

## Live gate (Faza 3)

| # | Ozb. | Nalaz | Ispravak | Test |
|---|---|---|---|---|
| 13 | V | Demo preflight je trajno otključavao live trgovanje | Potrebni su: demo preflight mlađi od 24 h, **live provjera ključa i računa** mlađa od 1 h (Futures uključen, withdrawal isključen, IP ograničenje, kapital, slobodna margina, sat) i potvrda vlasnika | `test_unlock_requires_demo_live_check_and_phrase`, `test_live_check_rejects_unsafe_keys`, `test_live_check_requires_equity` |
| 14 | V | Odobrenje za live nije isticalo | Ističe nakon `max_unlock_hours` (zadano 12 h) | `test_unlock_expires` |
| 15 | V | Rotacija ključa i promjena konfiguracije nisu poništavale odobrenje | Otisak konfiguracije (limiti, način rada, otisak ključa) poništava odobrenje | `test_unlock_invalidated_by_config_change`, `test_unlock_invalidated_by_key_rotation` |
| 16 | V | Nakon restarta se live mogao nastaviti bez provjere | Bez valjanog odobrenja prelazi se na PAPER, uz kritično upozorenje | `test_live_mode_not_resumed_after_restart_without_valid_unlock` |
| 17 | S | Live ključ bez IP ograničenja samo je generirao upozorenje | Takav se ključ odbija | API + `test_live_check_rejects_unsafe_keys` |

## AI organizacija i botovi (Faze 4–5)

| # | Ozb. | Nalaz | Ispravak | Test |
|---|---|---|---|---|
| 18 | N | Board meeting nije imao dnevni red ni veto nad odlukama | Dnevni red 1–8; kasniji govornici dobivaju ranija izvješća; runda rasprave samo ako je LLM stvarno dostupan; CEO odluka; CRO odobrava ili stavlja veto (tvrda pravila + CRO agent) | `test_agenda_order_discussion_and_cro_veto`, `test_offline_meeting_is_explicitly_deterministic` |
| 19 | S | `mark_live_eligible` je tražio status koji nijedan tok nije mogao proizvesti, pa je live praktički bio nedostižan (ili bi se zaobilazio) | Prihvatljivost se temelji isključivo na dokazima: WF verdikt mlađi od 90 dana + stvarne paper/demo trgovine (shadow se ne računaju). U live načinu nevaljani statusi se automatski snižavaju | `tests/test_validation.py` |
| 20 | S | Degradacija se mjerila samo apsolutno | Dodana usporedba s validiranim OOS očekivanjem (2 SE) | `test_degradation_detected_against_validation` |

## Server (Faza 7)

| # | Ozb. | Nalaz | Ispravak | Test |
|---|---|---|---|---|
| 21 | S | Bez migracija baze | `schema_migrations` + numerirane migracije; v1 baze nadograđuju se bez gubitka podataka | `test_migration_from_pre_migration_database` |
| 22 | S | Bez backupa, provjere integriteta i watchdoga | Dnevni backup (SQLite backup API, rotacija), `integrity_check`, dnevna provjera audit lanca; watchdog restartira mrtav ili zaglavljen engine | `test_backup_is_consistent_and_rotated`, `test_watchdog_restarts_dead_engine` |
| 23 | S | Uparivanje se moglo pogađati bez ograničenja | Najviše 5 promašaja po IP-u u 15 min i 30 ukupno na sat, uz audit | `test_pairing_throttled_after_failures` |
| 24 | S | Nije bilo ograničenja broja zahtjeva na API | Middleware: 240 zahtjeva/min po tokenu ili IP-u, 10/min za `/api/pair` | `test_rate_limit_returns_429` |
| 25 | S | Tokeni uređaja nisu isticali | Istječu nakon 30 dana neaktivnosti (klizno) | `test_device_token_expires` |
| 26 | N | Master ključ samo kroz env | Podržan je i `MX_MASTER_KEY_FILE` (Docker secret); datoteka koju mogu čitati drugi korisnici se odbija | `test_master_key_file_permissions` |
| 27 | N | Greške enginea nisu slale obavijesti | Kritična obavijest, ograničena na jednu po tipu greške u 10 min | (kod) |

## Android (Faza 6)

| # | Ozb. | Nalaz | Ispravak |
|---|---|---|---|
| 28 | S | Na 401 aplikacija bi ostala „zaglavljena“ | `Session.expired` → povratak na uparivanje, uz poruku |
| 29 | S | WebSocket se nije ponovno pokretao nakon novog uparivanja i radio je i u pozadini | Start/stop prati životni ciklus activityja; 4401 se tretira kao istekla sesija |
| 30 | S | BiometricPrompt se stvarao kasno; osjetljive radnje bile su dopuštene bez zaključavanja zaslona | Prompt se stvara u `onCreate`; osjetljive radnje se blokiraju ako uređaj nema zaključavanje zaslona; emergency stop namjerno ne traži biometriju |
| 31 | S | Način rada bio je vidljiv samo na dashboardu | Globalna traka na svim ekranima (PAPER/DEMO/LIVE · REAL MONEY, stanje enginea) s emergency stopom |
| 32 | N | Zastarjeli podaci nisu se prikazivali | `StaleBanner` (podaci stariji od 3 osvježavanja) |
| 33 | N | Notifikacija bez provjere dozvole i data-only poruke | Provjera `POST_NOTIFICATIONS`; rezervni naslov i tekst iz `data` |
| 34 | S | **Release APK potpisan debug ključem** | Release se potpisuje samo vlasnikovim ključem; bez ključa ostaje nepotpisan i ne objavljuje se. Debug APK je jasno označen `DEBUG-not-for-production` |
