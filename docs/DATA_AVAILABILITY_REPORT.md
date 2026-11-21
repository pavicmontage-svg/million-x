# DATA AVAILABILITY REPORT — MillionX 2.1

**Izvor dokaza:**
- Ova tablica nije pretpostavka, nego rezultat stvarnih zahtjeva poslanih 20. 11. 2026. (CI run `44838758141`,
  `python -m millionx.micro.availability`). Sirovi rezultat: `docs/research/availability_probe_2026-11-20.{md,json}`.
- Provjereno je svih 10 instrumenata (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, LTC) na 9 uzoračkih
  dana između 2024-01 i 2026-11. Za BTC je preuzet po jedan dan svakog skupa da se vidi stvarni sadržaj.
- REST ograničenja su iz službene Binance dokumentacije, jer su REST pozivi s CI-ja (SAD) odbijeni s **HTTP 451**
  (ograničena lokacija). Iz Hrvatske REST radi, što je potvrđeno ranije iz aplikacije.

## 1. Što postoji (javna arhiva data.binance.vision, USDⓈ-M)

| skup | dostupnost 2024-01 → 2026-11 | sadržaj (stvarno pročitano) | granulacija | koristimo za |
|---|---|---|---|---|
| `klines` 1m | 10/10 svih dana | OHLCV + **taker buy volume** + broj trgovina | 1 min | cijena, volatilnost, bar-level CVD |
| `aggTrades` dnevno/mjesečno | 10/10 svih dana | id, cijena, količina, vrijeme (ms), `is_buyer_maker` | svaka agregirana trgovina (BTC ≈ 2,8 M/dan, ~20–50 MB zip/dan) | **pravi trade-level CVD**, veliki nalozi |
| `trades` | 10/10 | pojedinačne trgovine | svaka trgovina (BTC ≈ 25–85 MB zip/dan) | ne koristimo; aggTrades je dovoljan |
| `metrics` | 10/10 svih dana | `sum_open_interest`, `sum_open_interest_value`, top-trader L/S omjeri, `sum_taker_long_short_vol_ratio` | **5 min** (288 redova/dan) | **povijesni Open Interest** |
| `bookDepth` | 10/10 svih dana | `timestamp, percentage, depth, notional` — kumulativna dubina knjige unutar ±x % od cijene | **30 s** (2880 snimki/dan × 12 razina) | **povijesni (agregirani) order book** |
| `bookTicker` | do **2024-03**, nakon toga 0/10 | best bid/ask + količine (svaka promjena) | ~100 ms | ne pokriva 2025–2026 |
| `fundingRate` mjesečno | 10/10 | `calc_time, funding_interval_hours, last_funding_rate` | po namirenju (8 h ili kraće) | **funding history + interval** |
| `premiumIndexKlines` 1m | 10/10 | premium index (osnova budućeg fundinga) | 1 min | funding u realnom vremenu (procjena) |
| `markPriceKlines` 1m | 10/10 | mark price | 1 min | divergencija mark/last |
| `liquidationSnapshot` | **0/10 na svim uzoračkim danima** (USDT-M arhiva stala nakon 2024-03-31, binance-public-data #337) | — | — | **nema povijesnih likvidacija za 2025–2026** |

## 2. REST API (službena dokumentacija; s CI-ja HTTP 451)

| endpoint | povijest | napomena |
|---|---|---|
| `/fapi/v1/fundingRate` | bez navedenog ograničenja dubine; max 1000/zahtjev | dijeli 500/5 min/IP s funding info |
| `/futures/data/openInterestHist` | **samo zadnjih 30 dana** | 5m–1d periodi; 1000 zahtjeva/5 min |
| `/fapi/v1/openInterest` | samo trenutna vrijednost | collector je anketira svakih 60 s |
| `/fapi/v1/aggTrades` | **samo zadnjih 48 h**, prozor < 1 h | dovoljno za oporavak kratkih praznina u snimanju |
| `/fapi/v1/depth` | samo trenutno stanje | — |
| WebSocket `@forceOrder` / `!forceOrder@arr` | samo uživo | **najviše 1 likvidacija po simbolu u sekundi** (zadnja), ostale se ne šalju |
| WebSocket `@depth20@500ms` | samo uživo | top 20 razina, 500 ms |
| WebSocket rute (od 2026-03) | — | depth je na `/public`, a aggTrade/markPrice/forceOrder na `/market`. Nerutirana veza tiho prima samo `/public`; to je potvrđeno na CI-ju i collector je ispravljen (run 44844375356) |

## 3. Posljedice za istraživanje (pravila koja se poštuju)

1. **Funding, Open Interest, order book (agregirani ±%), trade-level CVD** imaju potpunu povijest za
   selekcijsko razdoblje 2025-01 – 2026-06. Istraživanje A, B, D, F i funding/OI dio E izvodi se na stvarnim podacima.
2. **Likvidacije: za 2025–2026 nema povijesnih podataka na Binanceu.**
   - Istraživanje C (Liquidations + Order Book) i likvidacijski dio E označeni su kao **INCOMPLETE**.
   - Likvidacije se ne procjenjuju iz cijene ni volumena, jer bi to bili izmišljeni podaci.
   - Od 2.1 MillionX ih sam snima uživo. I to snimanje je **donja granica** stvarnog volumena, jer stream šalje
     najviše jednu likvidaciju po simbolu u sekundi.
3. **Order book:** `bookDepth` je kumulativna dubina po postotnim pojasevima od cijene (30 s), a ne razine 5/10/20.
   - Razine 5/10/20, zidovi, uklanjanje likvidnosti, apsorpcija i price impact postoje samo iz **vlastitog snimanja**
     (collector, od 2.1 nadalje), pa za njih povijest počinje danom pokretanja.
   - Iz OHLCV-a se ne izmišljaju.
4. **Open Interest u 5-min koracima:** vrijednost s oznakom vremena *t* smije se koristiti tek od minute *t*
   (bez interpolacije unatrag). Horizont od 1 minute s OI-jem je zato ograničen na 5-minutni ritam podatka (dokumentirano).
5. **Završni holdout (2026-07-01 – 2026-10-31) se ne preuzima** za istraživanje. Ingestija staje na 2026-06-30.
6. **Plaćeni izvori** imaju potpunu povijest likvidacija, incremental L2 book i OI od 2019/2020 (npr. Tardis.dev, CoinGlass, Amberdata).
   - Nisu uvedeni. Trošak se mora dokumentirati i odobriti prije bilo kakve uporabe (§11 specifikacije).
   - Cijene nisu provjerene u ovoj fazi.
