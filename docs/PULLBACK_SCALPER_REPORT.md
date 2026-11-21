# PULLBACK SCALPER REPORT — MillionX 2.2

*Automatski generirano iz `scalp-research run 44881204071 (9/10 instrumenata; BTC se ponovno računa)` (event-driven 1 s simulacija na aggTrades, 9 instrumenata). Discovery 2025-01 – 2025-09 (kalibracija i izbor konfiguracije), validacija 2025-10 – 2026-06. Holdout 2026-07 – 2026-10 nije korišten. Brojevi nisu ručno upisani.*

## Zaključak (interpretacija)

- **Nema prednosti ni prije troškova.**
  - Prosječni bruto pomak po signalu na validaciji je **−2 do +3 bps**, tj. ≈ 0 R (−0,07 do +0,21 R).
  - Smjer koji engine predviđa na 1–300 s horizontu nije bolji od slučajnog.
- **Troškovi su presudni.**
  - Round-trip taker trošak je ≈ 13 bps (BASE).
  - Uski scalping stopovi (4–36 bps) znače da troškovi iznose **0,4–3,4 R po trgovini**, prije bilo kakvog rezultata.
  - Odnos naknada i bruto dobiti je **1,7–7,3**: naknade su veće od cjelokupne bruto dobiti dobitnih trgovina.
- **Veliki R/R ne pomaže kad nema smjera.**
  - S ciljevima 3R/5R win rate je 2–14 %.
  - Većina dobitnika izlazi na break-even/trailingu prije cilja (prosječni dobitnik ≈ 0,3–1,3 R neto).
  - Očekivanje ostaje negativno.
- **Maker ulaz smanjuje trošak**, ali uz potvrđeni fill i dalje ostaje negativan (najbolje −0,29 R).
- **Opportunity Engine je ispravno odbio sve:** 0 prihvaćenih na validaciji, presuda **NO_TRADE**.
  Kriteriji nisu snižavani.

## Logika (unaprijed registrirana)

Engine B (Pullback): 1m EMA9/21 trend u smjeru 5m trenda; povlačenje u zonu EMA21/VWAP (dubina u ATR-ovima); volumen pada tijekom povlačenja; ulaz tek kad se momentum vrati (1 s brzina i CVD u smjeru trenda) i cijena probije 60-sekundni ekstrem — nikad na sam dodir EMA-e. Stop = sl_atr × 1m ATR; ciljevi 3R/5R; isti izlazi kao A.

**Presuda: NO_TRADE** · konfiguracija odabrana na discoveryju: `B2@300s` · prihvaćeno Opportunity Engineom na validaciji: 0

## Svi kandidati bez filtra (validacija, BASE ako nije drukčije navedeno)

| konfiguracija @ odluka | val n | bruto R | neto R BASE | neto R STRESSED | neto R EXTREME | neto R MAKER | WR | avg win R | avg loss R | PF | hold s | naknade/bruto dobit | neto EUR/dan |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0@1s | 103882 | 0.122 | -3.366 | -4.989 | -7.952 | -2.093 | 0.04 | 0.78 | -3.53 | 0.01 | 36 | 5.44 | -3202.2 |
| B0@60s | 1461 | 0.044 | -3.430 | -5.023 | -7.956 | -2.176 | 0.03 | 0.80 | -3.55 | 0.01 | 33 | 5.97 | -45.9 |
| B0@180s | 556 | -0.048 | -3.543 | -5.101 | -8.069 | -2.263 | 0.02 | 0.70 | -3.62 | 0.00 | 31 | 7.27 | -18.0 |
| B0@300s | 344 | -0.055 | -3.490 | -5.008 | -7.896 | -2.183 | 0.03 | 0.87 | -3.63 | 0.01 | 30 | 6.15 | -11.0 |
| B1@1s | 114415 | 0.104 | -2.677 | -3.971 | -6.335 | -1.664 | 0.04 | 1.10 | -2.84 | 0.02 | 41 | 5.19 | -2804.4 |
| B1@60s | 1633 | 0.016 | -2.781 | -4.057 | -6.396 | -1.724 | 0.03 | 1.29 | -2.90 | 0.01 | 39 | 6.09 | -41.6 |
| B1@180s | 648 | -0.018 | -2.828 | -4.083 | -6.434 | -1.764 | 0.02 | 1.19 | -2.92 | 0.01 | 37 | 6.88 | -16.8 |
| B1@300s | 409 | -0.042 | -2.849 | -4.112 | -6.425 | -1.738 | 0.02 | 1.24 | -2.95 | 0.01 | 36 | 6.31 | -10.7 |
| B2@1s | 146090 | 0.032 | -1.361 | -2.010 | -3.193 | -0.853 | 0.04 | 0.66 | -1.44 | 0.02 | 44 | 5.09 | -1820.9 |
| B2@60s | 2066 | 0.009 | -1.377 | -2.011 | -3.176 | -0.858 | 0.03 | 0.74 | -1.45 | 0.02 | 44 | 5.32 | -26.0 |
| B2@180s | 807 | 0.002 | -1.396 | -2.030 | -3.210 | -0.873 | 0.03 | 1.05 | -1.47 | 0.02 | 42 | 5.33 | -10.3 |
| B2@300s | 513 | -0.016 | -1.408 | -2.034 | -3.189 | -0.875 | 0.04 | 0.68 | -1.50 | 0.02 | 43 | 5.29 | -6.6 |

## Walk-forward (kalibracija se širi kvartal po kvartal)

- 2025-10-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-01-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-04-01: prihvaćeno 0, neto R —, neto EUR 0.0
