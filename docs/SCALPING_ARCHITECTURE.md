# SCALPING ARCHITECTURE — MillionX 2.2 (Continuous Directional Scalping Engine)

Grana `v2.2` (iz `v2.1`). `v2.0`, `v2.1` i `release-1.9.0` ostaju netaknuti.

## 1. Analiza postojeće arhitekture (2.1)

| dio | kako radi danas | ograničenje za scalping |
|---|---|---|
| `TradingEngine.tick()` | REST anketiranje 1m svijeća; odluka **samo na zatvaranju 1m bara** (`on_bar`) | najmanje 60 s kašnjenja; bez tick/1 s podataka |
| strategije bot1–9 (+v2) | OHLCV indikatori; v2 traže 1h trend | 60m filtar u v2 (2.2 ga ne koristi kao obvezan) |
| `micro/collector.py` (2.1) | WebSocket /public depth20@500ms + /market aggTrade, forceOrder, markPrice@1s; 1 s redovi u `micro.db` | snima, ali ne odlučuje |
| Risk Engine (`risk.py`) | rizik 0,5 %, sizing iz stopa, 10 EUR/dan, 2 pozicije, ISOLATED, razine leveragea, likvidacijski buffer 3× | **ostaje nepromijenjen** i koristi se i za scalping |
| `ExecutionManager` | idempotentni clientOrderId, verificirani stop na burzi, rekoncilijacija | ostaje jedini put do burze; u 2.2 se ne zove (samo paper) |
| AI desk (2.0) | samo savjetodavan | ostaje savjetodavan, izvan kritičnog puta |
| Research 2.0 / 2.1 | 1m backtest, A–F mikrostruktura | nema 1 s event-driven simulacije |

## 2. Nova arhitektura (2.2)

```
Binance WS /public (depth20@500ms) ─┐
Binance WS /market (aggTrade, markPrice@1s, forceOrder) ─┤→ Aggregator (2.1) → 1 s redovi (micro.db)
REST openInterest (60 s) ───────────┘            │
                                                 ▼  svake sekunde, neovisno o otvorenim pozicijama
                                     ScalpScanner (scalp/live.py)
                                     · rolling 1 s barovi po instrumentu (30 min)
                                     · scalp/features.py  (ISTE funkcije kao u backtestu)
                                     · Engine A Momentum / B Pullback / C Order Flow (scalp/engines.py)
                                     · Opportunity Ranking (scalp/opportunity.py):
                                       očekivani pomak i trajanje (kalibrirano), naknade ulaz/izlaz,
                                       spread, slippage, funding, neto EV, R/R, kvaliteta podataka
                                     · odluka: ACCEPT / REJECT (+ razlozi) → zapis prilike
                                                 ▼ samo ACCEPT i samo ako strategija prošla validaciju
                                     Risk Engine (postojeći) → sizing iz rizika i stopa
                                                 ▼
                                     PaperBook (virtualni fillovi na živim podacima; nikad Binance nalozi)
                                     · SL / TP / break-even / trailing / exhaustion / order-flow reversal / max hold
```

Ključna načela:
- **Skener nikad ne staje.** Prilike se analiziraju i bilježe svake sekunde, i kad ima otvorenih pozicija.
  Izvršenje je ograničeno samo Risk Engineom (2 pozicije, dnevni limit) i rezultatom validacije.
- **Hijerarhija timeframeova:**
  - primarno 1 s (ulaz i izlaz), 1m (entry kontekst), 3m (potvrda momenta), 5m (kratki trend);
  - sekundarno 15m (kontekst, ne blokira);
  - **60m se ne koristi**.
- **Režimi:** trending, ranging, visoka/niska volatilnost, momentum expansion, liquidity sweep i dislocation
  računaju se iz 1 s/1m/5m podataka (`scalp/features.py::regime`).
  Režim je ulaz u kalibraciju, a ne automatska blokada.
- **Isti kod uživo i u backtestu.** Značajke se računaju istim vektoriziranim funkcijama nad 1 s nizovima.
  Svaka prilika sprema snimku značajki u trenutku odluke (rekonstrukcija "što je bot vidio").
- **Troškovi:**
  - taker za ulaz i izlaz je zadan;
  - maker se računa **samo** kad je fill limit naloga potvrđen (u backtestu cijena mora proći kroz limit,
    a ispunjava se ograničeni dio prometa);
  - prilika se odbija ako očekivani pomak ne pokriva ukupne troškove + rezervu.
- **Confidence nije AI broj.** Vjerojatnost i očekivani pomak dolaze iz kalibracijske tablice: povijesni ishodi
  sličnih setupova (engine × kvantil jačine signala × režim) na discovery razdoblju.
- **Engine C (order flow):**
  - povijesni order book u 1 s rezoluciji ne postoji, pa se C ne može vjerodostojno backtestirati;
  - radi samo nad snimljenim podacima (collector);
  - bez svježe knjige setup je **INCOMPLETE** i ne izvršava se;
  - OHLCV se ne tretira kao order flow.
- **AI:** Independent Trader i Supervisor ostaju savjetodavni i asinkroni. Skener, ranking i Risk Engine rade bez AI API-ja.
- **Live ostaje zaključan.** U 2.2 izvršenje je isključivo PaperBook (virtualno).

## 3. Istraživanje (event-driven, 1 s)

- **Podaci:** aggTrades (svaka agregirana trgovina, 2025-01 – 2026-06, 10 instrumenata) se pretvaraju u 1 s barove:
  OHLC, agresivni buy/sell volumen, broj trgovina, VWAP.
  Ulaz/izlaz se simulira na tim sekundama, a 1m/3m/5m se grade iz istih sekundi.
- **Što nije moguće povijesno:**
  - spread i knjiga u 1 s rezoluciji → za A/B se koriste pretpostavljeni spread i slippage po troškovnom scenariju, uz stres test;
  - C → samo uživo (paper).
- **Latencija:** ulaz 1 s (BASE) / 3 s (STRESSED) nakon odluke, po prvoj sljedećoj cijeni.
- **Ispadi burze:** nasumično (deterministički sjeme) izostavljene minute u kojima se ne može ući ni izaći.
  Izlaz se odgađa do povratka, a gubitak se bilježi.
- **Protokol:**
  - discovery 2025-01 – 2025-09: kalibracija i odabir pragova iz unaprijed zadanog grida;
  - validacija 2025-10 – 2026-06: jedino mjesto za ocjenu;
  - walk-forward po kvartalima unutar validacije;
  - holdout 2026-07 – 2026-10 se ne preuzima.
- **Portfelj:**
  - 500 EUR, 0,5 % rizika, 2 pozicije, 10 EUR/dan, ISOLATED;
  - leverage 5/10/15/20× određuje može li se pozicija s uskim stopom uopće otvoriti (margin).

## 4. Aktivacija

Kriteriji iz v2.0 (Activation 2.0) se **ne mijenjaju**. Scalping strategija smije u paper izvršenje (PaperBook)
tek kad na validaciji ima pozitivan neto rezultat s donjom granicom CI > 0 nakon troškova. U live ne ide nikad bez svih
kriterija, ≥ 30 dana papera i odluke vlasnika. Broj signala nije kriterij.
