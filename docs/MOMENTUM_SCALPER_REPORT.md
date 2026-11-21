# MOMENTUM SCALPER REPORT — MillionX 2.2

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

Engine A (Momentum): 30 s brzina ≥ z·σ, relativni volumen ≥ prag, udio agresivnih kupnji/prodaja ≥ prag, proboj 3-min raspona, rast CVD akceleracije, potvrda 3-min momenta, nije iscrpljen (pomak od 5-min ekstrema ≤ ext_max σ). Stop = sl_sigma × 1-min σ; cilj 2R/3R/5R; break-even, trailing, exhaustion i order-flow reversal izlazi; max 15 min (do 45 min samo za poziciju u trailingu). Odluka svake sekunde (1 s) ili samo na zatvaranju 1m/3m/5m bara.

**Presuda: NO_TRADE** · konfiguracija odabrana na discoveryju: `A2@60s` · prihvaćeno Opportunity Engineom na validaciji: 0

## Svi kandidati bez filtra (validacija, BASE ako nije drukčije navedeno)

| konfiguracija @ odluka | val n | bruto R | neto R BASE | neto R STRESSED | neto R EXTREME | neto R MAKER | WR | avg win R | avg loss R | PF | hold s | naknade/bruto dobit | neto EUR/dan |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0@1s | 76352 | 0.048 | -1.527 | -2.269 | -3.607 | -0.935 | 0.09 | 0.90 | -1.77 | 0.05 | 42 | 3.03 | -1067.5 |
| A0@60s | 875 | 0.014 | -1.569 | -2.291 | -3.638 | -1.009 | 0.10 | 1.01 | -1.84 | 0.06 | 40 | 2.76 | -12.6 |
| A0@180s | 252 | -0.025 | -1.614 | -2.344 | -3.677 | -1.101 | 0.10 | 0.71 | -1.87 | 0.04 | 41 | 3.63 | -3.7 |
| A0@300s | 147 | -0.097 | -1.858 | -2.662 | -4.104 | -1.184 | 0.08 | 1.00 | -2.11 | 0.04 | 36 | 3.73 | -2.5 |
| A1@1s | 32371 | 0.048 | -1.453 | -2.158 | -3.430 | -0.890 | 0.10 | 0.96 | -1.74 | 0.07 | 41 | 2.73 | -430.6 |
| A1@60s | 428 | 0.104 | -1.427 | -2.097 | -3.402 | -0.860 | 0.14 | 1.00 | -1.82 | 0.09 | 41 | 2.35 | -5.6 |
| A1@180s | 117 | -0.034 | -1.598 | -2.301 | -3.594 | -0.984 | 0.09 | 0.60 | -1.83 | 0.03 | 40 | 3.85 | -1.7 |
| A1@300s | 63 | 0.121 | -1.860 | -2.692 | -4.394 | -1.152 | 0.13 | 0.99 | -2.27 | 0.06 | 42 | 2.06 | -1.1 |
| A2@1s | 32371 | 0.011 | -0.551 | -0.816 | -1.292 | -0.340 | 0.10 | 0.45 | -0.66 | 0.08 | 47 | 2.65 | -163.5 |
| A2@60s | 428 | 0.000 | -0.574 | -0.811 | -1.299 | -0.342 | 0.13 | 0.35 | -0.71 | 0.07 | 49 | 2.62 | -2.3 |
| A2@180s | 117 | -0.011 | -0.598 | -0.849 | -1.329 | -0.361 | 0.10 | 0.51 | -0.72 | 0.08 | 44 | 3.32 | -0.6 |
| A2@300s | 63 | -0.030 | -0.773 | -1.086 | -1.713 | -0.464 | 0.11 | 0.31 | -0.91 | 0.04 | 46 | 2.23 | -0.4 |

## Walk-forward (kalibracija se širi kvartal po kvartal)

- 2025-10-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-01-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-04-01: prihvaćeno 0, neto R —, neto EUR 0.0
