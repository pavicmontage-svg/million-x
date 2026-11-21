# FUNDING RATE RESEARCH REPORT — MillionX 2.1

*Automatski generirano iz CI izlaza (`micro-research run 44841732631`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 (samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. Brojevi nisu ručno upisani.*

## Interpretacija

- Funding je mjeren kao causal percentil premium indeksa (30 dana) uz promjenu OI-ja i cijene.
- Bruto pomak na validaciji je **−1,3 do +1,1 bps (A)** i **−2,8 do +0,4 bps (E)**. Trošak je 14 bps.
- Smjer naučen na discoveryju se mijenja između horizonata. Ekstremni funding u ovom razdoblju ne predviđa ni preokret ni nastavak.
- **Nijedan kandidat.**

Kombinacije koje koriste ovaj izvor: A, E. Kvaliteta izvora po instrumentu: `DATA_QUALITY_REPORT.md` (izvor `funding`).

| uvjet | h | smjer | n (val) | bruto bps | neto bps BASE | 95 % CI | FDR q | STRESSED bps | WR | PF | /dan/instr. | instr. + | kandidat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1_funding_hi_oi_up_price_up | 1m | follow | 70231 | 0.1 | -13.9 | -14.0 … -13.7 | 1.000 | -22.8 | 0.08 | 0.07 | 25.73 | 0/10 | ne |
| A1_funding_hi_oi_up_price_up | 5m | fade(- | 17023 | -0.6 | -14.6 | -15.1 … -14.0 | 1.000 | -23.4 | 0.22 | 0.20 | 6.24 | 0/10 | ne |
| A1_funding_hi_oi_up_price_up | 15m | follow | 12879 | 0.3 | -13.7 | -14.9 … -12.4 | 1.000 | -23.0 | 0.30 | 0.42 | 4.72 | 0/10 | ne |
| A1_funding_hi_oi_up_price_up | 60m | follow | 8378 | -1.3 | -15.3 | -18.6 … -12.1 | 1.000 | -24.9 | 0.37 | 0.59 | 3.07 | 0/10 | ne |
| A2_funding_lo_oi_up_price_dn | 1m | follow | 58426 | -0.1 | -14.1 | -14.3 … -13.9 | 1.000 | -23.1 | 0.09 | 0.08 | 21.40 | 0/10 | ne |
| A2_funding_lo_oi_up_price_dn | 5m | follow | 14372 | -0.1 | -14.1 | -15.1 … -13.2 | 1.000 | -22.9 | 0.24 | 0.26 | 5.26 | 0/10 | ne |
| A2_funding_lo_oi_up_price_dn | 15m | fade(- | 11024 | -0.3 | -14.3 | -15.9 … -12.7 | 1.000 | -23.5 | 0.31 | 0.43 | 4.04 | 0/10 | ne |
| A2_funding_lo_oi_up_price_dn | 60m | fade(- | 7455 | 1.1 | -12.9 | -16.7 … -9.2 | 1.000 | -22.0 | 0.38 | 0.66 | 2.73 | 0/10 | ne |
| A3_funding_hi_oi_dn | 1m | fade(- | 107339 | -0.1 | -14.1 | -14.3 … -13.9 | 1.000 | -23.1 | 0.09 | 0.07 | 39.32 | 0/10 | ne |
| A3_funding_hi_oi_dn | 5m | fade(- | 23454 | -0.6 | -14.6 | -15.7 … -13.8 | 1.000 | -23.4 | 0.23 | 0.23 | 8.59 | 0/10 | ne |
| A3_funding_hi_oi_dn | 15m | follow | 18298 | 0.4 | -13.6 | -15.0 … -11.9 | 1.000 | -22.8 | 0.30 | 0.45 | 6.70 | 0/10 | ne |
| A3_funding_hi_oi_dn | 60m | follow | 11874 | -0.7 | -14.7 | -18.0 … -11.1 | 1.000 | -23.9 | 0.38 | 0.61 | 4.35 | 0/10 | ne |
| A4_funding_lo_oi_dn | 1m | follow | 117397 | 0.1 | -13.9 | -14.4 … -13.5 | 1.000 | -22.8 | 0.10 | 0.10 | 43.00 | 0/10 | ne |
| A4_funding_lo_oi_dn | 5m | follow | 25503 | 0.9 | -13.1 | -16.2 … -10.0 | 1.000 | -22.6 | 0.25 | 0.35 | 9.34 | 0/10 | ne |
| A4_funding_lo_oi_dn | 15m | follow | 18886 | 0.5 | -13.5 | -15.2 … -11.9 | 1.000 | -22.3 | 0.33 | 0.47 | 6.92 | 0/10 | ne |
| A4_funding_lo_oi_dn | 60m | follow | 11722 | 0.2 | -13.8 | -17.6 … -10.3 | 1.000 | -23.1 | 0.41 | 0.64 | 4.29 | 0/10 | ne |
| E1_funding_hi_oi_up_trending | 1m | fade(- | 64850 | -0.1 | -14.1 | -14.2 … -14.0 | 1.000 | -23.1 | 0.07 | 0.05 | 23.75 | 0/10 | ne |
| E1_funding_hi_oi_up_trending | 5m | fade(- | 14277 | -0.4 | -14.4 | -15.0 … -13.7 | 1.000 | -23.2 | 0.21 | 0.21 | 5.23 | 0/10 | ne |
| E1_funding_hi_oi_up_trending | 15m | follow | 10558 | 0.2 | -13.8 | -15.2 … -12.5 | 1.000 | -23.1 | 0.30 | 0.42 | 3.87 | 0/10 | ne |
| E1_funding_hi_oi_up_trending | 60m | follow | 6724 | -0.7 | -14.7 | -18.0 … -11.4 | 1.000 | -23.6 | 0.38 | 0.60 | 2.46 | 0/10 | ne |
| E2_funding_hi_oi_up_ranging | 1m | fade(- | 59751 | -0.0 | -14.0 | -14.2 … -13.9 | 1.000 | -23.1 | 0.09 | 0.07 | 21.89 | 0/10 | ne |
| E2_funding_hi_oi_up_ranging | 5m | follow | 13363 | 0.3 | -13.7 | -14.4 … -13.0 | 1.000 | -23.0 | 0.23 | 0.27 | 4.89 | 0/10 | ne |
| E2_funding_hi_oi_up_ranging | 15m | follow | 9961 | -0.3 | -14.3 | -15.7 … -13.0 | 1.000 | -23.6 | 0.30 | 0.42 | 3.65 | 0/10 | ne |
| E2_funding_hi_oi_up_ranging | 60m | follow | 6468 | -2.1 | -16.1 | -19.8 … -12.6 | 1.000 | -25.5 | 0.38 | 0.59 | 2.37 | 0/10 | ne |
| E3_funding_lo_oi_up_trending | 1m | follow | 59600 | 0.1 | -13.9 | -14.1 … -13.8 | 1.000 | -22.9 | 0.08 | 0.06 | 21.83 | 0/10 | ne |
| E3_funding_lo_oi_up_trending | 5m | follow | 13141 | 0.4 | -13.6 | -14.3 … -12.9 | 1.000 | -22.7 | 0.22 | 0.24 | 4.81 | 0/10 | ne |
| E3_funding_lo_oi_up_trending | 15m | fade(- | 9595 | -0.7 | -14.7 | -16.1 … -13.3 | 1.000 | -23.6 | 0.30 | 0.40 | 3.51 | 0/10 | ne |
| E3_funding_lo_oi_up_trending | 60m | fade(- | 6188 | -0.2 | -14.2 | -18.6 … -8.6 | 1.000 | -23.1 | 0.38 | 0.63 | 2.27 | 0/10 | ne |
| E4_funding_lo_oi_up_ranging | 1m | follow | 50713 | -0.0 | -14.0 | -14.3 … -13.8 | 1.000 | -23.1 | 0.09 | 0.08 | 18.58 | 0/10 | ne |
| E4_funding_lo_oi_up_ranging | 5m | follow | 11335 | 0.1 | -13.9 | -14.9 … -13.0 | 1.000 | -23.0 | 0.23 | 0.27 | 4.15 | 0/10 | ne |
| E4_funding_lo_oi_up_ranging | 15m | fade(- | 8557 | 0.1 | -13.9 | -15.5 … -12.4 | 1.000 | -22.7 | 0.33 | 0.44 | 3.13 | 0/10 | ne |
| E4_funding_lo_oi_up_ranging | 60m | follow | 5682 | -2.8 | -16.8 | -20.6 … -13.2 | 1.000 | -26.2 | 0.39 | 0.58 | 2.08 | 0/10 | ne |
