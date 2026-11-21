# MOMENTUM SCALPER REPORT — MillionX 2.2

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

Engine A (Momentum): 30 s brzina ≥ z·σ, relativni volumen ≥ prag, udio agresivnih kupnji/prodaja ≥ prag, proboj 3-min raspona, rast CVD akceleracije, potvrda 3-min momenta, nije iscrpljen (pomak od 5-min ekstrema ≤ ext_max σ). Stop = sl_sigma × 1-min σ; cilj 2R/3R/5R; break-even, trailing, exhaustion i order-flow reversal izlazi; max 15 min (do 45 min samo za poziciju u trailingu). Odluka svake sekunde (1 s) ili samo na zatvaranju 1m/3m/5m bara.

**Presuda: NO_TRADE** · konfiguracija odabrana na discoveryju: `A2@60s` · prihvaćeno Opportunity Engineom na validaciji: 0

## Svi kandidati bez filtra (validacija, BASE ako nije drukčije navedeno)

| konfiguracija @ odluka | val n | bruto R | neto R BASE | neto R STRESSED | neto R EXTREME | neto R MAKER | WR | avg win R | avg loss R | PF | hold s | naknade/bruto dobit | neto EUR/dan |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0@1s | 64913 | 0.042 | -1.339 | -1.991 | -3.165 | -0.824 | 0.10 | 0.90 | -1.58 | 0.06 | 42 | 2.85 | -796.2 |
| A0@60s | 740 | 0.005 | -1.342 | -1.957 | -3.102 | -0.880 | 0.10 | 1.05 | -1.60 | 0.07 | 40 | 2.59 | -9.1 |
| A0@180s | 207 | -0.013 | -1.352 | -1.956 | -3.071 | -0.931 | 0.09 | 0.77 | -1.57 | 0.05 | 41 | 3.46 | -2.6 |
| A0@300s | 115 | -0.069 | -1.511 | -2.195 | -3.373 | -0.927 | 0.09 | 1.01 | -1.75 | 0.05 | 35 | 3.37 | -1.6 |
| A1@1s | 27218 | 0.042 | -1.269 | -1.885 | -2.998 | -0.782 | 0.11 | 0.96 | -1.55 | 0.08 | 41 | 2.57 | -316.3 |
| A1@60s | 355 | 0.067 | -1.207 | -1.763 | -2.842 | -0.744 | 0.14 | 0.98 | -1.56 | 0.10 | 41 | 2.28 | -3.9 |
| A1@180s | 95 | -0.069 | -1.291 | -1.826 | -2.826 | -0.782 | 0.07 | 0.70 | -1.45 | 0.04 | 40 | 3.90 | -1.1 |
| A1@300s | 47 | 0.213 | -1.477 | -2.167 | -3.628 | -0.858 | 0.13 | 1.15 | -1.86 | 0.09 | 42 | 1.74 | -0.6 |
| A2@1s | 27218 | 0.010 | -0.482 | -0.713 | -1.129 | -0.300 | 0.11 | 0.44 | -0.59 | 0.09 | 47 | 2.49 | -120.1 |
| A2@60s | 355 | -0.008 | -0.486 | -0.691 | -1.095 | -0.310 | 0.12 | 0.33 | -0.60 | 0.08 | 48 | 2.53 | -1.6 |
| A2@180s | 95 | -0.021 | -0.479 | -0.669 | -1.045 | -0.291 | 0.08 | 0.41 | -0.56 | 0.07 | 43 | 3.55 | -0.4 |
| A2@300s | 47 | 0.009 | -0.625 | -0.884 | -1.431 | -0.363 | 0.11 | 0.32 | -0.74 | 0.05 | 46 | 1.95 | -0.3 |

## Walk-forward (kalibracija se širi kvartal po kvartal)

- 2025-10-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-01-01: prihvaćeno 0, neto R —, neto EUR 0.0
- 2026-04-01: prihvaćeno 0, neto R —, neto EUR 0.0
