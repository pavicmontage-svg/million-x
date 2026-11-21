# OPEN INTEREST RESEARCH REPORT — MillionX 2.1

*Automatski generirano iz CI izlaza (`micro-research run 44841732631`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 (samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. Brojevi nisu ručno upisani.*

## Interpretacija

- Kvadranti cijena × OI (B1–B4), funding × OI (A) i OI po režimu (E): bruto pomak je unutar ±2,8 bps.
- B3 "price up + OI down" (short covering), fade, ima statistički razlučiv bruto učinak, ali praktički nulti: 0,05 bps na 1 min i 0,2 bps na 5 min,
  na vrlo velikom uzorku (n = 916 725 i 209 423).
- OI je dostupan u 5-min koracima i koristi se tek nakon jednog punog perioda. Horizont od 1 minute s OI-jem zato
  ne vidi svježiju informaciju od 5–10 min.
- **Nijedan kandidat.**

Kombinacije koje koriste ovaj izvor: A, B, E. Kvaliteta izvora po instrumentu: `DATA_QUALITY_REPORT.md` (izvor `open_interest`).

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
| B1_price_up_oi_up_cvd_up | 1m | follow | 714655 | -0.0 | -14.0 | -14.1 … -14.0 | 1.000 | -23.0 | 0.05 | 0.03 | 261.78 | 0/10 | ne |
| B1_price_up_oi_up_cvd_up | 5m | follow | 164769 | -0.1 | -14.1 | -14.3 … -13.9 | 1.000 | -23.1 | 0.15 | 0.15 | 60.35 | 0/10 | ne |
| B1_price_up_oi_up_cvd_up | 15m | follow | 69262 | -0.3 | -14.3 | -14.8 … -13.8 | 1.000 | -23.3 | 0.25 | 0.31 | 25.37 | 0/10 | ne |
| B1_price_up_oi_up_cvd_up | 60m | follow | 27685 | -1.8 | -15.8 | -17.6 … -13.9 | 1.000 | -24.9 | 0.34 | 0.51 | 10.14 | 0/10 | ne |
| B2_price_dn_oi_up_cvd_dn | 1m | fade(- | 689381 | 0.0 | -14.0 | -14.0 … -13.9 | 1.000 | -23.0 | 0.05 | 0.04 | 252.52 | 0/10 | ne |
| B2_price_dn_oi_up_cvd_dn | 5m | fade(- | 160318 | 0.1 | -13.9 | -14.1 … -13.7 | 1.000 | -22.8 | 0.17 | 0.18 | 58.72 | 0/10 | ne |
| B2_price_dn_oi_up_cvd_dn | 15m | fade(- | 68597 | 0.2 | -13.8 | -14.4 … -13.3 | 1.000 | -22.8 | 0.27 | 0.34 | 25.13 | 0/10 | ne |
| B2_price_dn_oi_up_cvd_dn | 60m | follow | 28090 | -1.0 | -15.0 | -16.7 … -13.3 | 1.000 | -24.2 | 0.38 | 0.54 | 10.29 | 0/10 | ne |
| B3_price_up_oi_dn | 1m | fade(- | 916725 | 0.0 | -14.0 | -14.0 … -13.9 | 1.000 | -23.0 | 0.05 | 0.03 | 335.80 | 0/10 | ne |
| B3_price_up_oi_dn | 5m | fade(- | 209423 | 0.2 | -13.8 | -14.0 … -13.6 | 1.000 | -22.9 | 0.17 | 0.14 | 76.71 | 0/10 | ne |
| B3_price_up_oi_dn | 15m | fade(- | 86676 | 0.4 | -13.6 | -14.1 … -13.1 | 1.000 | -22.8 | 0.28 | 0.31 | 31.75 | 0/10 | ne |
| B3_price_up_oi_dn | 60m | follow | 32670 | -1.6 | -15.6 | -17.3 … -13.9 | 1.000 | -24.6 | 0.35 | 0.51 | 11.97 | 0/10 | ne |
| B4_price_dn_oi_dn | 1m | follow | 1043882 | 0.0 | -14.0 | -14.1 … -13.9 | 1.000 | -23.0 | 0.05 | 0.04 | 382.37 | 0/10 | ne |
| B4_price_dn_oi_dn | 5m | follow | 234535 | 0.1 | -13.9 | -14.3 … -13.6 | 1.000 | -23.0 | 0.17 | 0.17 | 85.91 | 0/10 | ne |
| B4_price_dn_oi_dn | 15m | follow | 94715 | 0.2 | -13.8 | -14.8 … -12.8 | 1.000 | -22.9 | 0.28 | 0.34 | 34.69 | 0/10 | ne |
| B4_price_dn_oi_dn | 60m | follow | 34382 | -0.7 | -14.7 | -16.4 … -12.9 | 1.000 | -23.8 | 0.38 | 0.55 | 12.59 | 0/10 | ne |
| B5_price_up_cvd_dn_divergence | 1m | fade(- | 79877 | 0.0 | -14.0 | -14.0 … -13.9 | 1.000 | -22.9 | 0.06 | 0.04 | 29.26 | 0/10 | ne |
| B5_price_up_cvd_dn_divergence | 5m | fade(- | 24019 | 0.3 | -13.7 | -14.2 … -13.3 | 1.000 | -22.5 | 0.21 | 0.19 | 8.80 | 0/10 | ne |
| B5_price_up_cvd_dn_divergence | 15m | fade(- | 12941 | 0.7 | -13.3 | -14.3 … -12.3 | 1.000 | -22.6 | 0.32 | 0.38 | 4.74 | 0/10 | ne |
| B5_price_up_cvd_dn_divergence | 60m | fade(- | 7607 | 2.6 | -11.4 | -15.0 … -6.6 | 1.000 | -20.6 | 0.41 | 0.65 | 2.79 | 0/10 | ne |
| B6_price_dn_cvd_up_divergence | 1m | follow | 45701 | 0.0 | -14.0 | -14.1 … -13.8 | 1.000 | -23.0 | 0.06 | 0.03 | 16.74 | 0/10 | ne |
| B6_price_dn_cvd_up_divergence | 5m | fade(- | 14345 | -0.2 | -14.2 | -14.7 … -13.7 | 1.000 | -23.1 | 0.19 | 0.19 | 5.25 | 0/10 | ne |
| B6_price_dn_cvd_up_divergence | 15m | fade(- | 8037 | -0.7 | -14.7 | -15.8 … -13.5 | 1.000 | -23.6 | 0.29 | 0.35 | 2.94 | 0/10 | ne |
| B6_price_dn_cvd_up_divergence | 60m | fade(- | 5081 | 1.3 | -12.7 | -15.6 … -9.8 | 1.000 | -21.4 | 0.38 | 0.62 | 1.86 | 0/10 | ne |
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
