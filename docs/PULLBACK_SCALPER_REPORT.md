# PULLBACK SCALPER REPORT — MillionX 2.2

*Automatski generirano iz `scalp-research run 44881204071 (10/10 instrumenata)` (event-driven 1 s simulacija na aggTrades, 10 instrumenata). Discovery 2025-01 – 2025-09 (kalibracija i izbor konfiguracije), validacija 2025-10 – 2026-06. Holdout 2026-07 – 2026-10 nije korišten. Brojevi nisu ručno upisani.*

## Zaključak (interpretacija)

- **Nema prednosti ni prije troškova.**
  - Prosječni bruto pomak po signalu na validaciji je **−2 do +2 bps**, tj. ≈ 0 R (−0,10 do +0,13 R).
  - Smjer koji engine predviđa na 1–300 s horizontu nije bolji od slučajnog.
- **Troškovi su presudni.**
  - Round-trip taker trošak je ≈ 13 bps (BASE).
  - Uski scalping stopovi (4–35 bps) znače da troškovi iznose **0,4–3,3 R po trgovini**, prije bilo kakvog rezultata.
  - Odnos naknada i bruto dobiti je **2,1–7,5**: naknade su veće od cjelokupne bruto dobiti dobitnih trgovina.
- **Veliki R/R ne pomaže kad nema smjera.**
  - S ciljevima 3R/5R win rate je 2–14 %.
  - Većina dobitnika izlazi na break-even/trailingu prije cilja (prosječni dobitnik ≈ 0,3–1,2 R neto).
  - Očekivanje ostaje negativno.
- **Maker ulaz smanjuje trošak**, ali uz potvrđeni fill i dalje ostaje negativan (najbolje −0,34 R).
- **Opportunity Engine je ispravno odbio sve:** 0 prihvaćenih na validaciji, presuda **NO_TRADE**.
  Kriteriji nisu snižavani.

## Logika (unaprijed registrirana)

Engine B (Pullback): 1m EMA9/21 trend u smjeru 5m trenda; povlačenje u zonu EMA21/VWAP (dubina u ATR-ovima); volumen pada tijekom povlačenja; ulaz tek kad se momentum vrati (1 s brzina i CVD u smjeru trenda) i cijena probije 60-sekundni ekstrem — nikad na sam dodir EMA-e. Stop = sl_atr × 1m ATR; ciljevi 3R/5R; isti izlazi kao A.

**Presuda: NO_TRADE** · konfiguracija odabrana na discoveryju: `B2@300s` · prihvaćeno Opportunity Engineom na validaciji: 0

## Svi kandidati bez filtra (validacija, BASE ako nije drukčije navedeno)

| konfiguracija @ odluka | val n | bruto R | neto R BASE | neto R STRESSED | neto R EXTREME | neto R MAKER | WR | avg win R | avg loss R | PF | hold s | naknade/bruto dobit | neto EUR/dan |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0@1s | 116213 | 0.133 | -3.508 | -5.204 | -8.297 | -2.168 | 0.04 | 0.77 | -3.67 | 0.01 | 36 | 5.57 | -3733.2 |
| B0@60s | 1630 | 0.051 | -3.611 | -5.297 | -8.392 | -2.281 | 0.03 | 0.78 | -3.73 | 0.01 | 34 | 6.17 | -53.9 |
| B0@180s | 615 | -0.048 | -3.661 | -5.283 | -8.357 | -2.310 | 0.02 | 0.70 | -3.73 | 0.00 | 33 | 7.50 | -20.6 |
| B0@300s | 379 | -0.029 | -3.600 | -5.201 | -8.196 | -2.265 | 0.03 | 0.87 | -3.73 | 0.01 | 30 | 6.32 | -12.5 |
| B1@1s | 128307 | 0.115 | -2.795 | -4.151 | -6.626 | -1.729 | 0.04 | 1.10 | -2.96 | 0.02 | 42 | 5.31 | -3284.4 |
| B1@60s | 1829 | 0.037 | -2.910 | -4.260 | -6.729 | -1.790 | 0.03 | 1.23 | -3.03 | 0.01 | 40 | 6.17 | -48.7 |
| B1@180s | 715 | -0.009 | -2.954 | -4.280 | -6.745 | -1.815 | 0.02 | 1.17 | -3.05 | 0.01 | 38 | 7.07 | -19.3 |
| B1@300s | 452 | -0.018 | -2.912 | -4.216 | -6.612 | -1.779 | 0.02 | 1.18 | -3.01 | 0.01 | 36 | 6.36 | -12.1 |
| B2@1s | 163819 | 0.037 | -1.422 | -2.101 | -3.341 | -0.886 | 0.04 | 0.67 | -1.50 | 0.02 | 45 | 5.21 | -2132.6 |
| B2@60s | 2301 | 0.013 | -1.448 | -2.119 | -3.348 | -0.903 | 0.03 | 0.72 | -1.52 | 0.02 | 44 | 5.53 | -30.5 |
| B2@180s | 889 | 0.003 | -1.455 | -2.120 | -3.349 | -0.903 | 0.03 | 1.05 | -1.52 | 0.02 | 42 | 5.60 | -11.8 |
| B2@300s | 558 | -0.007 | -1.446 | -2.093 | -3.292 | -0.906 | 0.04 | 0.68 | -1.53 | 0.02 | 43 | 5.44 | -7.4 |

## Walk-forward (kalibracija se širi kvartal po kvartal)

- 2025-10-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-01-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-04-01: prihvaćeno 0, neto R —, neto EUR 0.0
