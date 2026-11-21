# OPPORTUNITY ENGINE REPORT — MillionX 2.2

Kod: `scalp/opportunity.py` (ranking i odluka), `scalp/live.py` (kontinuirani skener), CI run `44881204071` (kalibracija i validacija).

## Kako se prilika procjenjuje

Za svakog kandidata (engine × konfiguracija × instrument × sekunda) računa se:

| veličina | izvor |
|---|---|
| očekivani pomak, trajanje, bruto dobit | kalibracijska tablica: povijesni ishodi sličnih setupova (engine × kvintil jačine signala × režim; ako ćelija ima < 50 slučajeva, koristi se engine × kvintil). Izvor je samo discovery razdoblje |
| vjerojatnost | udio dobitnih slučajeva u istoj ćeliji (kalibrirano). **AI confidence se ne koristi** |
| naknada ulaz / izlaz | taker 5 bps; maker 2 bps samo kad je fill potvrđen |
| spread, slippage | po troškovnom scenariju (BASE: spread 1 bps, slippage 1 bps po taker fillu) |
| funding | 1 bps / 8 h × očekivano trajanje |
| očekivana neto dobit | bruto − svi troškovi, izraženo u bps, R i EUR (rizik 2,50 EUR) |
| R/R, troškovi/rizik | iz stopa i cilja |
| kvaliteta podataka | OK / INCOMPLETE (npr. C bez svježe knjige) |

**Ranking (odluka vlasnika 2026-11-21): po očekivanom neto iznosu (R / EUR), a ne po win rateu.**
Win rate se prikazuje, ali nije kriterij.

**ACCEPT** zahtijeva sve od navedenog:
- kvaliteta podataka OK;
- ≥ 50 sličnih povijesnih setupova;
- očekivani neto > 2 bps rezerve i > 0 R;
- strategija validirana (Research presuda);
- Risk Engine dopušta: najviše 2 pozicije, dnevni gubitak 10 EUR, margin pri izolirani leverage ≤ 20× s likvidacijom ≥ 3× stop.

Sve ostalo je **REJECT**, uz razloge koji se prikazuju u aplikaciji.

## Rezultat na povijesnim podacima (validacija 2025-10 – 2026-06)

- Generirano je **1 148 645 kandidata** (10 instrumenata, engines A/B, 3 konfiguracije, odluke na 1 s / 1m / 3m / 5m).
- **Prihvaćeno: 0** za A, B i A+B. Kalibrirana očekivana neto dobit je negativna u svim ćelijama,
  jer je bruto ≈ 0, a troškovi su 0,4–3,3 R.
- Mjerenja po specifikaciji: detected = kandidati, rejected = svi, executed = 0.
  Missed (odbijeni, a kasnije dobitni) se uživo mjeri kroz shadow ishode.
  U povijesti je udio dobitnih neto trgovina bio 2–14 %, a njihov zbroj je manji od gubitaka.

## Ako bot dugo ne trguje

Prema §5 specifikacije ovo nije problem podataka ni koda, nego **stvarni nedostatak isplative prilike pri taker troškovima**:
- podaci: aggTrades je potpun, s konzistentnošću ≥ 99,2 % prema klines (2.1);
- kod: testovi, kauzalnost i rekonstrukcija odluke su provjereni;
- strategija: bruto ≈ 0 na svim varijantama.

Kriteriji se ne snižavaju automatski.
