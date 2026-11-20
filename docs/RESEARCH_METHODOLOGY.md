# RESEARCH METHODOLOGY — MillionX 2.0

Ovaj dokument opisuje kako MillionX 2.0 traži i potvrđuje prednost (edge). Pravila su zapisana **prije**
pokretanja istraživanja (pre-registracija). Kod: `backend/millionx/research2.py`, `backtest/stats2.py`,
`selection.py`, `regime2.py`. CI: `.github/workflows/research2.yml`.

## 1. Podaci i razdoblja

| | razdoblje | uporaba |
|---|---|---|
| Selekcija | 2025-01-01 – 2026-06-30 | sve: forenzika, walk-forward, CV, odabir parametara, statistika |
| Završni holdout | 2026-07-01 – 2026-10-31 | **ne koristi se** u 2.0 fazama 1–11; pokreće se jednom, nakon zamrzavanja kandidata |

- Instrumenti (10): BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, LTC (USDⓈ-M perpetual).
- Izvor: Binance javna arhiva (data.binance.vision), 1m klines s taker-buy volumenom. Mjesečne datoteke,
  a za tekući mjesec dnevne.
- Viši timeframeovi (3m, 5m, 15m, 1h) grade se iz zatvorenih 1m svijeća. Vrijednost višeg TF-a čita se
  samo sa **zadnje zatvorene** svijeće tog TF-a (`last_closed_index`). Test `test_no_lookahead` to provjerava
  za svaku strategiju: signal u trenutku *i* mora biti identičan sa i bez budućih podataka.

## 2. Simulacija trgovine

- Svaka strategija trguje sama u "shadow" knjizi s fiksnim rizikom 0,5 % (R = rizik do stopa uključujući troškove).
  Rezultat strategije ne ovisi o drugim botovima ni o supervizoru.
- Ulaz je na zatvaranju signalne svijeće. U scenarijima STRESSED i EXTREME ulaz se odgađa 1 odnosno
  2 minute (latencija).
- Izlazi se računaju na 1m putanji: stop, TP1 (djelomično), TP2, trailing, break-even, time-stop.
  Kad isti 1m bar dotakne i stop i cilj, pretpostavlja se **stop** (konzervativno).
- Izolirani margin; likvidacija se simulira ako cijena preskoči stop (gap).

## 3. Troškovni scenariji

| scenarij | taker / strana | slippage / strana | spread (pola po fillu) | funding | latencija | odbijeni nalozi |
|---|---|---|---|---|---|---|
| BASE | 0,05 % | 2 bps | 1 bps | ×1 | 0 | 0 % |
| STRESSED | 0,05 % | 5 bps | 3 bps | ×2 | 1 min | 2 % |
| EXTREME | 0,06 % | 10 bps | 6 bps | ×3 | 2 min | 5 % |

**Ograničenja (nisu modelirani, pa su označeni):**
- *Djelomični fillovi.* Pretpostavlja se potpuni fill market naloga. Pri riziku od 2,50 EUR i ovim
  instrumentima nalog je daleko ispod dubine knjige.
- *Povijesni order book.* Ne postoji u javnoj arhivi. bot3 je u backtestu samo **CVD / taker-flow**.
  Uživo, kad podaci knjige nedostaju, v2 ne izvršava ulaz (`md.live` i `book is None` znače odbijanje, ne potvrdu).
- *Tick i step size* primjenjuju se pri dimenzioniranju (SymbolRules). Minimalni notional provjerava Risk Engine.
- *Funding* se procjenjuje iz konfigurirane stope, a ne iz povijesnog fundinga po simbolu.

## 4. Ugniježđeni walk-forward s purged CV

Za svaku (strategiju, timeframe):

1. **Grid** ima najviše 12 konfiguracija i definiran je prije testa (`param_grid` u klasi).
   - v2 strategije **ne mijenjaju parametre ulazne logike v1**. Grid sadrži samo pragove v2 vrata
     (cost multiplier 4/6/8, ADX 20/25), pa je broj pokušaja mali.
2. Svaka konfiguracija se simulira **jednom** kroz cijelo selekcijsko razdoblje.
   - Trgovina ovisi samo o kauzalnim indikatorima i vlastitim signalima, pa prozori biraju trgovine po vremenu.
   - Ovo je ekvivalentno pokretanju po prozorima, uz razliku od nekoliko trgovina na rubovima zbog
     indikatora koji su "zagrijani" od početka podataka.
3. **Vanjski WF:** trening 90 dana, test 30 dana, korak 30 dana.
4. **Unutarnji purged 3-fold CV** na treningu bira konfiguraciju:
   - purge 1 dan i embargo 1 dan oko svakog test folda;
   - broje se samo trgovine otvorene **i** zatvorene unutar folda;
   - svaki fold treba ≥ 8 trgovina;
   - cilj je `mean(R)·√n − 3·DD`, izglađen prosjekom sa susjednim konfiguracijama (plateau, ne vrh).
5. U izvan-uzoračke (OOS) rezultate ulaze samo trgovine iz vanjskih test prozora.

## 5. Statistika i zaštita od višestrukog testiranja

- **Block bootstrap** (blok 10 trgovina, 2000 ponavljanja): 95 % CI očekivanja u R i p95 drawdowna.
- **Monte Carlo** permutacija redoslijeda: p95 max drawdowna.
- **Deflated Sharpe Ratio** (Bailey & López de Prado), računat s **ukupnim** brojem konfiguracija u cijelom
  2.0 istraživanju (`run_plan()`, 354 konfiguracije). DSR ≥ 0,95 znači da je Sharpe vrlo vjerojatno stvaran.
- **PBO (CSCV)** na matrici dnevnog R svih konfiguracija (8 blokova): vjerojatnost da je najbolja konfiguracija
  u uzorku ispod medijana izvan uzorka.
- **Stabilnost parametara:** udio susjednih konfiguracija najčešće odabrane konfiguracije koje su također pozitivne.
- **Cross-asset:** odabir samo na BTC/ETH/SOL/XRP/BNB, mjerenje na ostalih 5 instrumenata.
- **Režimi (Regime 2.0):** 10 kauzalnih režima iz 1h/15m/5m/1m. Rezultat se raščlanjuje po režimu,
  LONG/SHORT i instrumentu.
- **Osjetljivost na troškove:** promjena exp R po dodatnom bps-u, iz BASE → STRESSED → EXTREME.

## 6. Activation 2.0 (svi uvjeti moraju vrijediti)

Postojeći kriteriji ostaju kao **minimum**:

| | prag |
|---|---|
| OOS trgovina | ≥ 100 |
| PF | ≥ 1,15 |
| exp R | ≥ 0,05 |
| pozitivni WF prozori | ≥ 60 % |
| max DD | ≤ 20 % |
| MC DD p95 | ≤ 30 % |
| paper trgovina | ≥ 50 |
| paper PF | ≥ 1,05 |

Dodano u 2.0:
- 95 % CI očekivanja > 0 (block bootstrap).
- DSR ≥ 0,95.
- PBO ≤ 0,5.
- Stabilnost parametara ≥ 0,5.
- Cross-asset exp R > 0.
- ≥ 2 režima s n ≥ 30 i pozitivnim exp R.
- exp R > 0 i u STRESSED scenariju.
- Research 2.0 presuda `CANDIDATE_FOR_PAPER`.
- **≥ 30 dana** stvarnog paper/demo trgovanja.

Ništa od navedenog ne smije mijenjati LLM. Pragovi se nisu snižavali nakon što su rezultati viđeni.

## 7. Strategy Selection Engine

Po ćeliji (instrument × režim) bira se strategija s najboljim *final score*:

- prosjek ponderiran raspadom (poluživot 60 dana);
- Bayesov shrinkage prema 0 R (snaga priora 50 trgovina);
- penali za drawdown, turnover i korelaciju dnevnog R s već odabranim strategijama.

Ćelija dobiva **NO TRADE** ako nijedna validirana strategija nema n ≥ 30, donju granicu 95 % CI > 0 i
pozitivan konačni rezultat. Selekcija je deterministička. AI u njoj ne sudjeluje.

## 8. Uloga AI-ja u 2.0

Samo savjetodavna:
- Ideje Independent Tradera se bilježe (`advisory_recorded`) i uspoređuju sa strategijama, ali se ne šalju.
- `advisory_only` nije postavka vlasnika.
- AI Supervisor može samo dodatno ograničiti: odbiti, čekati, sniziti leverage. Risk Engine ima zadnju riječ.

## 9. Holdout protokol

Kandidati se nakon Research 2.0 zamrzavaju zajedno s parametrima i verzijom koda. Zatim se jednom
pokreću na holdoutu. Holdout se ne koristi za izmjene; neuspjeh na holdoutu znači povratak u RESEARCH.
