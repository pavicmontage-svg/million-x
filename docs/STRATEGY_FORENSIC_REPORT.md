# STRATEGY FORENSIC REPORT — MillionX 2.0, faza 1 (baseline prije bilo kakvih izmjena strategija)

**Izvor:** GitHub Actions run `44824868312` (workflow *research-forensic*, commit `cbe0688`, grana `v2.0`),
rezultat objavljen na grani `research-forensic` (commit `0d11fa0`). Sirovi podaci: `docs/research/forensic_baseline.json`.
Ovaj dokument je zapisan **prije** ijedne promjene koda strategija (bot1–bot9 su identični verziji 1.9.0).
Završni holdout (2026-07-01 – 2026-10-31) nije korišten.

## 1. Interpretacija (ljudski sažetak automatskih brojeva u nastavku)

1. **Nijedna od 27 kombinacija (9 strategija × 3 timeframea) nije pozitivna nakon troškova.** Najbolja je
   bot6_rsidiv 60m (−0,044 R po trgovini, PF 0,91), zatim bot8_supertrend 15m (−0,050 R) i bot5_squeeze 60m (−0,083 R).
2. **Problem nije samo u naknadama.** 25 od 27 kombinacija je negativno i *prije* troškova (bruto exp R < 0).
   Samo bot6 60m (+0,004 R) i bot8 15m (+0,023 R) imaju malu pozitivnu bruto prednost koju troškovi pojedu.
   Zaključak: ulazna logika ne nosi prednost — filtriranjem ili optimizacijom parametara ne može se dobiti
   profitabilan sustav iz signala koji su sami po sebi bez prednosti.
3. **Trošak u R jako pada s timeframeom:** 1m–5m ≈ 0,15–0,17 R po trgovini, 15m ≈ 0,09–0,15 R, 60m ≈ 0,04–0,12 R.
   Scalping na 1–5 min uz taker naknade troši 15–17 % rizika po trgovini prije ikakve prednosti.
4. **Struktura izlaza:** prosječni stop je ≈ −0,97 R, a medijan MFE (najveći povoljni pomak) je 0,57–0,87 R, tj.
   polovina trgovina nikad ne dosegne +1 R. Time-stop izlazi su uglavnom negativni na 5m i 1m.
5. **LONG strana je lošija od SHORT u 23/27 slučajeva** u razdoblju 2025-01 – 2026-06. To je svojstvo razdoblja,
   ne pravilo; v2 strategije NEĆE uvesti jednostrani bias (to bi bila prilagodba povijesti).
6. **LOW_VOL režimi se ponavljaju u listi "isključiti"** kod gotovo svih strategija — u niskoj volatilnosti
   stop je mali u odnosu na fiksne troškove. To je mehaničko (trošak/ATR), pa je opravdano kao opći filtar.
7. **Redundancija je niska** (Jaccard ≤ 0,07, korelacija dnevnog R ≤ 0,37): strategije ne kopiraju jedna drugu,
   ali su sve slabe — diverzifikacija nula-prednosti ne daje prednost.
8. **bot3_orderflow** je u backtestu samo CVD/taker-flow (nema povijesnog order booka) i na 1m ima najgori
   rezultat (−0,243 R, 155 065 trgovina). Na 60m ima samo 10 trgovina — premalo za bilo kakav zaključak.

## 2. Posljedice za fazu 3 (v2) — odluke donesene na temelju ovog izvještaja

- v2 se testiraju na 15m i 60m izvršnom timeframeu (ne 1–5m), jer trošak u R na nižim timeframeovima sam
  po sebi ruši očekivanje.
- Opći **cost gate**: ulaz samo ako je udaljenost stopa ≥ k × round-trip trošak (k je u gridu, ne jedna vrijednost).
- Trend-following strategije (bot2, bot3, bot4, bot5, bot8, bot9): ulaz samo u smjeru zatvorenog 1h trenda.
- Mean-reversion strategije (bot1, bot6, bot7): ulaz samo kad 15m trend nije jak (ADX ispod praga) i ne protiv jakog 1h trenda.
- Nema jednostranog (LONG/SHORT) biasa, nema izbacivanja simbola na temelju ovog razdoblja.
- v1 kod ostaje netaknut; v2 su zasebne, verzionirane klase. Svi rezultati v2 mjere se samo kroz Research Engine 2.0
  (ugniježđeni walk-forward, purged CV, DSR, PBO, troškovni scenariji) — ovaj izvještaj se ne koristi za "odabir" parametara.

---

# Automatski izlaz (nepromijenjen)

Podaci: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT, ADAUSDT, LINKUSDT, AVAXUSDT, LTCUSDT; razdoblje 2025-01-01 – 2026-06-30 (završni holdout 2026-07-01 – 2026-10-31 NIJE korišten). Svaka strategija trguje sama (shadow knjiga, fiksno 0,5 R, stvarne naknade 0,05 % taker po strani, slippage 2 bps, funding). R = iznos rizika do stopa uključujući troškove.

## Sažetak po strategiji i timeframeu

| strategija | tf | n | exp R | PF | WR | bruto exp R | trošak R | max niz gubitaka | lažni signali | MFE med. |
|---|---|---|---|---|---|---|---|---|---|---|
| bot1_vwap | 3m | 13613 | -0.2175 | 0.581 | 0.4277 | -0.0585 | 0.159 | 36 | 0.305 | 0.598 |
| bot1_vwap | 15m | 4596 | -0.2286 | 0.552 | 0.4708 | -0.0844 | 0.1442 | 21 | 0.297 | 0.606 |
| bot1_vwap | 60m | 540 | -0.2133 | 0.567 | 0.4944 | -0.09 | 0.1232 | 14 | 0.3 | 0.637 |
| bot2_ema | 5m | 32406 | -0.2038 | 0.567 | 0.4799 | -0.0585 | 0.1453 | 23 | 0.296 | 0.631 |
| bot2_ema | 15m | 17153 | -0.1561 | 0.674 | 0.4929 | -0.044 | 0.1121 | 29 | 0.288 | 0.713 |
| bot2_ema | 60m | 2978 | -0.112 | 0.773 | 0.4822 | -0.0472 | 0.0647 | 20 | 0.281 | 0.768 |
| bot3_orderflow | 1m | 155065 | -0.2428 | 0.496 | 0.4878 | -0.0708 | 0.1719 | 27 | 0.322 | 0.609 |
| bot3_orderflow | 15m | 2475 | -0.1971 | 0.584 | 0.5034 | -0.0459 | 0.1512 | 12 | 0.297 | 0.664 |
| bot3_orderflow | 60m | 10 | -0.1416 | 0.709 | 0.5 | -0.0285 | 0.1131 | 2 | 0.4 | 0.743 |
| bot4_breakout | 5m | 18866 | -0.2261 | 0.528 | 0.48 | -0.0677 | 0.1584 | 23 | 0.306 | 0.631 |
| bot4_breakout | 15m | 7802 | -0.1848 | 0.617 | 0.4981 | -0.0502 | 0.1346 | 19 | 0.288 | 0.707 |
| bot4_breakout | 60m | 2287 | -0.1315 | 0.732 | 0.4963 | -0.0471 | 0.0843 | 15 | 0.26 | 0.808 |
| bot5_squeeze | 5m | 5388 | -0.2316 | 0.522 | 0.4759 | -0.0746 | 0.157 | 15 | 0.314 | 0.614 |
| bot5_squeeze | 15m | 3066 | -0.1706 | 0.648 | 0.498 | -0.0487 | 0.1219 | 15 | 0.287 | 0.726 |
| bot5_squeeze | 60m | 713 | -0.0832 | 0.828 | 0.5049 | -0.013 | 0.0701 | 14 | 0.258 | 0.872 |
| bot6_rsidiv | 3m | 18154 | -0.2035 | 0.593 | 0.4401 | -0.0567 | 0.1468 | 29 | 0.291 | 0.638 |
| bot6_rsidiv | 15m | 3658 | -0.1675 | 0.676 | 0.4333 | -0.0751 | 0.0924 | 23 | 0.273 | 0.677 |
| bot6_rsidiv | 60m | 973 | -0.0444 | 0.907 | 0.4872 | 0.0042 | 0.0487 | 19 | 0.23 | 0.82 |
| bot7_liquidity | 3m | 33878 | -0.2251 | 0.528 | 0.4845 | -0.0661 | 0.159 | 25 | 0.307 | 0.631 |
| bot7_liquidity | 15m | 9372 | -0.1638 | 0.657 | 0.5007 | -0.0439 | 0.1199 | 26 | 0.278 | 0.726 |
| bot7_liquidity | 60m | 3432 | -0.1169 | 0.76 | 0.4962 | -0.0379 | 0.079 | 22 | 0.264 | 0.79 |
| bot8_supertrend | 5m | 681 | -0.1471 | 0.659 | 0.464 | -0.0266 | 0.1204 | 8 | 0.261 | 0.604 |
| bot8_supertrend | 15m | 679 | -0.0503 | 0.876 | 0.5228 | 0.0227 | 0.073 | 8 | 0.246 | 0.704 |
| bot8_supertrend | 60m | 171 | -0.0771 | 0.825 | 0.462 | -0.0391 | 0.0379 | 5 | 0.228 | 0.704 |
| bot9_rangeexp | 5m | 7735 | -0.2351 | 0.5 | 0.46 | -0.0779 | 0.1572 | 20 | 0.293 | 0.57 |
| bot9_rangeexp | 15m | 3422 | -0.1553 | 0.664 | 0.5108 | -0.0246 | 0.1307 | 11 | 0.281 | 0.725 |
| bot9_rangeexp | 60m | 987 | -0.1104 | 0.771 | 0.5035 | -0.0257 | 0.0847 | 14 | 0.243 | 0.814 |

## Nalazi po strategiji (automatski iz brojeva; bucket mora imati ≥ 30 trgovina)

### bot1_vwap
- **3m**: najveći izvor gubitka = by_side:LONG (-1645.09 R u 6982 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2367 R), BEAR|NORMAL_VOL (-0.3114 R), BULL|HIGH_VOL (-0.2872 R), BULL|LOW_VOL (-0.262 R), BULL|NORMAL_VOL (-0.2219 R), SIDEWAYS|HIGH_VOL (-0.2072 R), SIDEWAYS|LOW_VOL (-0.2157 R), SIDEWAYS|NORMAL_VOL (-0.2124 R)
  - by_side: LONG n=6982 -0.2356R, SHORT n=6631 -0.1984R
  - by_exit: stop_loss n=7066 -0.9682R, take_profit_2 n=636 0.8936R, time_stop n=1515 0.1119R, trailing_stop n=4396 0.7149R
- **15m**: najveći izvor gubitka = by_side:LONG (-615.93 R u 2416 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|NORMAL_VOL (-0.2564 R), BULL|HIGH_VOL (-0.3474 R), BULL|LOW_VOL (-0.2422 R), SIDEWAYS|HIGH_VOL (-0.2323 R), SIDEWAYS|LOW_VOL (-0.231 R), SIDEWAYS|NORMAL_VOL (-0.2471 R)
  - by_side: LONG n=2416 -0.2549R, SHORT n=2180 -0.1993R
  - by_exit: stop_loss n=2404 -0.9707R, take_profit_2 n=618 0.9183R, time_stop n=69 0.2323R, trailing_stop n=1505 0.4648R
- **60m**: najveći izvor gubitka = by_side:SHORT (-59.92 R u 273 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|NORMAL_VOL (-0.3176 R), BULL|NORMAL_VOL (-0.2622 R), SIDEWAYS|HIGH_VOL (-0.2296 R)
  - by_side: LONG n=267 -0.2069R, SHORT n=273 -0.2195R
  - by_exit: stop_loss n=273 -0.9748R, take_profit_2 n=111 0.9091R, time_stop n=1 0.9046R, trailing_stop n=155 0.317R

### bot2_ema
- **5m**: najveći izvor gubitka = by_side:LONG (-3671.62 R u 15653 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2286 R), BULL|LOW_VOL (-0.2573 R), BULL|NORMAL_VOL (-0.2399 R), SIDEWAYS|LOW_VOL (-0.2257 R), SIDEWAYS|NORMAL_VOL (-0.2061 R)
  - by_side: LONG n=15653 -0.2346R, SHORT n=16753 -0.175R
  - by_exit: stop_loss n=14970 -0.9709R, take_profit_2 n=2592 0.9498R, time_stop n=2509 -0.2229R, trailing_stop n=12335 0.4887R
- **15m**: najveći izvor gubitka = by_side:LONG (-1522.73 R u 8326 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2106 R)
  - by_side: LONG n=8326 -0.1829R, SHORT n=8827 -0.1308R
  - by_exit: stop_loss n=8224 -0.9772R, take_profit_2 n=1770 1.054R, time_stop n=750 -0.1202R, trailing_stop n=6409 0.5591R
- **60m**: najveći izvor gubitka = by_side:LONG (-208.75 R u 1387 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_symbol:DOGEUSDT (0.0218 R, n=304)
  - by_side: LONG n=1387 -0.1505R, SHORT n=1591 -0.0784R
  - by_exit: stop_loss n=1455 -0.9867R, take_profit_2 n=316 1.2181R, time_stop n=133 -0.139R, trailing_stop n=1074 0.6851R

### bot3_orderflow
- **1m**: najveći izvor gubitka = by_side:SHORT (-19517.38 R u 83134 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|HIGH_VOL (-0.2494 R), BEAR|LOW_VOL (-0.2365 R), BEAR|NORMAL_VOL (-0.2645 R), BULL|HIGH_VOL (-0.2367 R), BULL|LOW_VOL (-0.2631 R), BULL|NORMAL_VOL (-0.2332 R), SIDEWAYS|HIGH_VOL (-0.2392 R), SIDEWAYS|LOW_VOL (-0.2385 R), SIDEWAYS|NORMAL_VOL (-0.2406 R)
  - by_side: LONG n=71931 -0.252R, SHORT n=83134 -0.2348R
  - by_exit: stop_loss n=76168 -0.9656R, take_profit_2 n=11784 0.7802R, time_stop n=4120 -0.2397R, trailing_stop n=62993 0.4397R
- **15m**: najveći izvor gubitka = by_side:SHORT (-290.29 R u 1619 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|NORMAL_VOL (-0.214 R), BULL|LOW_VOL (-0.2599 R), SIDEWAYS|LOW_VOL (-0.2292 R), SIDEWAYS|NORMAL_VOL (-0.2112 R)
  - by_side: LONG n=856 -0.2308R, SHORT n=1619 -0.1793R
  - by_exit: stop_loss n=1196 -0.9695R, take_profit_2 n=422 0.8576R, time_stop n=41 -0.2588R, trailing_stop n=816 0.3926R
- **60m**: najveći izvor gubitka = None:None (None R u None trgovina); negativna i PRIJE troškova (nema sirove prednosti); previše lažnih signala (>35 % stopova bez 0,3 R povoljnog pomaka)
  - by_side: LONG n=3 -0.0693R, SHORT n=7 -0.1726R
  - by_exit: stop_loss n=5 -0.973R, take_profit_2 n=3 0.8933R, trailing_stop n=2 0.3845R

### bot4_breakout
- **5m**: najveći izvor gubitka = by_side:SHORT (-2233.65 R u 10307 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2793 R), BEAR|NORMAL_VOL (-0.2094 R), BULL|LOW_VOL (-0.3134 R), BULL|NORMAL_VOL (-0.2358 R), SIDEWAYS|LOW_VOL (-0.2352 R), SIDEWAYS|NORMAL_VOL (-0.2349 R)
  - by_side: LONG n=8559 -0.2374R, SHORT n=10307 -0.2167R
  - by_exit: stop_loss n=8997 -0.9683R, take_profit_2 n=1061 1.0812R, time_stop n=1046 -0.2565R, trailing_stop n=7762 0.4595R
- **15m**: najveći izvor gubitka = by_side:LONG (-728.46 R u 3514 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2306 R), BULL|LOW_VOL (-0.275 R), BULL|NORMAL_VOL (-0.241 R), SIDEWAYS|LOW_VOL (-0.2081 R)
  - by_side: LONG n=3514 -0.2073R, SHORT n=4288 -0.1663R
  - by_exit: stop_loss n=3838 -0.9728R, take_profit_2 n=642 1.1576R, time_stop n=130 -0.0748R, trailing_stop n=3192 0.4883R
- **60m**: najveći izvor gubitka = by_side:SHORT (-158.55 R u 1224 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_regime:SIDEWAYS|HIGH_VOL (0.0343 R, n=112); isključiti u: BEAR|LOW_VOL (-0.2768 R), BULL|LOW_VOL (-0.2502 R), SIDEWAYS|NORMAL_VOL (-0.2286 R)
  - by_side: LONG n=1063 -0.1337R, SHORT n=1224 -0.1295R
  - by_exit: stop_loss n=1131 -0.9827R, take_profit_2 n=186 1.3564R, time_stop n=29 -0.1607R, trailing_stop n=941 0.5985R

### bot5_squeeze
- **5m**: najveći izvor gubitka = by_side:LONG (-633.24 R u 2509 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2649 R), BULL|HIGH_VOL (-0.2481 R), BULL|LOW_VOL (-0.3167 R), BULL|NORMAL_VOL (-0.2027 R), SIDEWAYS|HIGH_VOL (-0.2199 R), SIDEWAYS|LOW_VOL (-0.2482 R), SIDEWAYS|NORMAL_VOL (-0.2419 R)
  - by_side: LONG n=2509 -0.2524R, SHORT n=2879 -0.2134R
  - by_exit: stop_loss n=2616 -0.9689R, take_profit_2 n=481 0.9263R, time_stop n=262 -0.256R, trailing_stop n=2029 0.4477R
- **15m**: najveći izvor gubitka = by_side:LONG (-272.83 R u 1428 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2056 R), SIDEWAYS|HIGH_VOL (-0.2013 R), SIDEWAYS|LOW_VOL (-0.2353 R)
  - by_side: LONG n=1428 -0.1911R, SHORT n=1638 -0.1528R
  - by_exit: stop_loss n=1513 -0.9754R, take_profit_2 n=336 1.0387R, time_stop n=39 -0.1734R, trailing_stop n=1178 0.5182R
- **60m**: najveći izvor gubitka = by_side:LONG (-65.4 R u 336 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_regime:BEAR|HIGH_VOL (0.1942 R, n=54), by_regime:SIDEWAYS|NORMAL_VOL (0.1015 R, n=137), by_regime:SIDEWAYS|HIGH_VOL (0.0658 R, n=52), by_symbol:XRPUSDT (0.0442 R, n=71), by_side:SHORT (0.0162 R, n=377); isključiti u: BULL|HIGH_VOL (-0.4753 R), BULL|NORMAL_VOL (-0.2427 R)
  - by_side: LONG n=336 -0.1947R, SHORT n=377 0.0162R
  - by_exit: stop_loss n=349 -0.9868R, take_profit_2 n=88 1.2151R, time_stop n=7 0.0931R, trailing_stop n=269 0.6598R

### bot6_rsidiv
- **3m**: najveći izvor gubitka = by_side:LONG (-1993.56 R u 9064 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2897 R), BEAR|NORMAL_VOL (-0.2228 R), SIDEWAYS|LOW_VOL (-0.2188 R), SIDEWAYS|NORMAL_VOL (-0.2018 R)
  - by_side: LONG n=9064 -0.2199R, SHORT n=9090 -0.1871R
  - by_exit: stop_loss n=8939 -0.9706R, take_profit_2 n=1315 1.0218R, time_stop n=2008 -0.0885R, trailing_stop n=5892 0.6477R
- **15m**: najveći izvor gubitka = by_side:LONG (-390.08 R u 1922 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2611 R), BEAR|NORMAL_VOL (-0.2538 R), BULL|LOW_VOL (-0.3479 R), SIDEWAYS|LOW_VOL (-0.2721 R)
  - by_side: LONG n=1922 -0.203R, SHORT n=1736 -0.1283R
  - by_exit: stop_loss n=1858 -0.9809R, take_profit_2 n=293 1.2109R, time_stop n=471 0.0938R, trailing_stop n=1036 0.7826R
- **60m**: najveći izvor gubitka = by_side:LONG (-57.56 R u 508 trgovina); pozitivna prije troškova, troškovi je pretvaraju u gubitak; pozitivno u: by_symbol:LINKUSDT (0.1217 R, n=91), by_regime:BULL|NORMAL_VOL (0.0715 R, n=52), by_side:SHORT (0.0309 R, n=465); isključiti u: BEAR|NORMAL_VOL (-0.2311 R)
  - by_side: LONG n=508 -0.1133R, SHORT n=465 0.0309R
  - by_exit: stop_loss n=454 -0.9904R, take_profit_2 n=69 1.3601R, time_stop n=131 0.1501R, trailing_stop n=319 0.9182R

### bot7_liquidity
- **3m**: najveći izvor gubitka = by_side:LONG (-4181.51 R u 17631 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2698 R), BEAR|NORMAL_VOL (-0.2347 R), BULL|HIGH_VOL (-0.2085 R), BULL|LOW_VOL (-0.2341 R), BULL|NORMAL_VOL (-0.2253 R), SIDEWAYS|HIGH_VOL (-0.2049 R), SIDEWAYS|LOW_VOL (-0.2314 R), SIDEWAYS|NORMAL_VOL (-0.225 R)
  - by_side: LONG n=17631 -0.2372R, SHORT n=16247 -0.212R
  - by_exit: stop_loss n=16245 -0.9681R, take_profit_2 n=1490 0.9935R, time_stop n=1797 -0.1574R, trailing_stop n=14346 0.4812R
- **15m**: najveći izvor gubitka = by_side:LONG (-867.98 R u 4878 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2143 R), BEAR|NORMAL_VOL (-0.2089 R), BULL|LOW_VOL (-0.2291 R), SIDEWAYS|LOW_VOL (-0.2117 R)
  - by_side: LONG n=4878 -0.1779R, SHORT n=4494 -0.1484R
  - by_exit: stop_loss n=4539 -0.9756R, take_profit_2 n=735 1.1234R, time_stop n=307 0.1041R, trailing_stop n=3791 0.537R
- **60m**: najveći izvor gubitka = by_side:LONG (-244.08 R u 1805 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_regime:BEAR|LOW_VOL (0.1045 R, n=44); isključiti u: BEAR|HIGH_VOL (-0.2152 R), BULL|LOW_VOL (-0.2433 R), SIDEWAYS|LOW_VOL (-0.2357 R)
  - by_side: LONG n=1805 -0.1352R, SHORT n=1627 -0.0966R
  - by_exit: stop_loss n=1684 -0.9838R, take_profit_2 n=370 1.2153R, time_stop n=96 0.0834R, trailing_stop n=1282 0.6223R

### bot8_supertrend
- **5m**: najveći izvor gubitka = by_side:LONG (-52.13 R u 361 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BULL|NORMAL_VOL (-0.2592 R), SIDEWAYS|LOW_VOL (-0.2359 R)
  - by_side: LONG n=361 -0.1444R, SHORT n=320 -0.1501R
  - by_exit: stop_loss n=259 -0.9764R, take_profit_2 n=24 1.0007R, time_stop n=142 -0.2122R, trailing_stop n=256 0.6204R
- **15m**: najveći izvor gubitka = by_side:LONG (-39.38 R u 364 trgovina); pozitivna prije troškova, troškovi je pretvaraju u gubitak; pozitivno u: by_symbol:LINKUSDT (0.1185 R, n=54), by_symbol:XRPUSDT (0.0698 R, n=65), by_regime:SIDEWAYS|HIGH_VOL (0.0624 R, n=105), by_symbol:BNBUSDT (0.0358 R, n=54), by_side:SHORT (0.0167 R, n=315)
  - by_side: LONG n=364 -0.1082R, SHORT n=315 0.0167R
  - by_exit: stop_loss n=258 -0.9852R, take_profit_2 n=12 1.1677R, time_stop n=144 0.0478R, trailing_stop n=265 0.7515R
- **60m**: najveći izvor gubitka = by_side:SHORT (-10.44 R u 69 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_regime:BULL|NORMAL_VOL (0.005 R, n=35)
  - by_side: LONG n=102 -0.0268R, SHORT n=69 -0.1513R
  - by_exit: stop_loss n=66 -0.9927R, take_profit_2 n=5 1.3003R, time_stop n=43 -0.0795R, trailing_stop n=57 0.8641R

### bot9_rangeexp
- **5m**: najveći izvor gubitka = by_side:LONG (-948.75 R u 3815 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.2295 R), BEAR|NORMAL_VOL (-0.2615 R), BULL|LOW_VOL (-0.2261 R), BULL|NORMAL_VOL (-0.2187 R), SIDEWAYS|LOW_VOL (-0.2523 R), SIDEWAYS|NORMAL_VOL (-0.2458 R)
  - by_side: LONG n=3815 -0.2487R, SHORT n=3920 -0.2218R
  - by_exit: stop_loss n=3493 -0.9683R, take_profit_2 n=439 0.9035R, time_stop n=874 -0.2444R, trailing_stop n=2929 0.4715R
- **15m**: najveći izvor gubitka = by_side:LONG (-321.26 R u 1597 trgovina); negativna i PRIJE troškova (nema sirove prednosti); isključiti u: BEAR|LOW_VOL (-0.286 R), BULL|LOW_VOL (-0.2568 R)
  - by_side: LONG n=1597 -0.2012R, SHORT n=1825 -0.1152R
  - by_exit: stop_loss n=1595 -0.9737R, take_profit_2 n=319 0.9657R, time_stop n=113 -0.1188R, trailing_stop n=1395 0.521R
- **60m**: najveći izvor gubitka = by_regime:SIDEWAYS|NORMAL_VOL (-69.3 R u 402 trgovina); negativna i PRIJE troškova (nema sirove prednosti); pozitivno u: by_regime:BEAR|NORMAL_VOL (0.0556 R, n=86), by_symbol:LTCUSDT (0.0491 R, n=73)
  - by_side: LONG n=447 -0.1366R, SHORT n=540 -0.0887R
  - by_exit: stop_loss n=482 -0.9832R, take_profit_2 n=126 1.1148R, time_stop n=13 -0.1026R, trailing_stop n=366 0.617R

## Redundancija (zajednički ulazi u istom 15-min intervalu, korelacija dnevnog R)

| A | B | zajednički ulazi | Jaccard | korelacija dnevnog R |
|---|---|---|---|---|
| bot6_rsidiv | bot7_liquidity | 2025 | 0.0409 | 0.365 |
| bot1_vwap | bot7_liquidity | 3024 | 0.0691 | 0.296 |
| bot1_vwap | bot6_rsidiv | 685 | 0.0222 | 0.278 |
| bot4_breakout | bot5_squeeze | 822 | 0.0352 | 0.266 |
| bot5_squeeze | bot9_rangeexp | 484 | 0.0383 | 0.257 |
| bot2_ema | bot4_breakout | 319 | 0.0063 | 0.241 |
| bot4_breakout | bot9_rangeexp | 549 | 0.0211 | 0.235 |
| bot2_ema | bot9_rangeexp | 319 | 0.008 | 0.222 |
| bot2_ema | bot8_supertrend | 22 | 0.0007 | 0.214 |
| bot4_breakout | bot8_supertrend | 22 | 0.0011 | 0.142 |
| bot2_ema | bot5_squeeze | 104 | 0.0028 | 0.13 |
| bot8_supertrend | bot9_rangeexp | 20 | 0.0024 | 0.117 |
| bot3_orderflow | bot5_squeeze | 2167 | 0.0142 | 0.101 |
| bot5_squeeze | bot8_supertrend | 13 | 0.0021 | 0.075 |
| bot1_vwap | bot3_orderflow | 1096 | 0.0068 | 0.04 |

## Ograničenja
- Povijesni order book ne postoji u Binance arhivi: bot3 je u backtestu CVD/taker-flow-only.
- Bot 10 (adaptive) nema vlastite signale; prosljeđuje druge pa se ne testira samostalno.
- MFE/MAE iz 1-minutnih svijeća (unutar svijeće nije poznat redoslijed high/low).