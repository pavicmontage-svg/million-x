# ORDER BOOK (BOOKDEPTH ±%) RESEARCH REPORT — MillionX 2.1

*Automatski generirano iz CI izlaza (`micro-research run 44841732631`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 (samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. Brojevi nisu ručno upisani.*

## Interpretacija

- Povijesni order book postoji samo kao kumulativna dubina unutar ±0,2 … ±5 % od cijene, svakih 30 s (bookDepth).
- Razine 5/10/20, zidovi, uklanjanje likvidnosti i price impact snimaju se tek od 2.1 (collector), pa za njih još nema povijesti.
- F1 (bid-heavy unutar 1 % + rast cijene, fade, 60 min): bruto +3,7 bps (CI bruto > 0), neto negativno.
  Imbalance unutar ±0,2 % (F3/F4) i tanka likvidnost (F5/F6): bez bruto učinka iznad troška.
- **Nijedan kandidat.**

Kombinacije koje koriste ovaj izvor: F. Kvaliteta izvora po instrumentu: `DATA_QUALITY_REPORT.md` (izvor `order_book_depth`).

| uvjet | h | smjer | n (val) | bruto bps | neto bps BASE | 95 % CI | FDR q | STRESSED bps | WR | PF | /dan/instr. | instr. + | kandidat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1_bid_heavy_1pct_and_up_momentum | 1m | fade(- | 72188 | 0.0 | -14.0 | -14.1 … -13.9 | 1.000 | -23.0 | 0.04 | 0.03 | 26.44 | 0/10 | ne |
| F1_bid_heavy_1pct_and_up_momentum | 5m | fade(- | 20483 | 0.1 | -13.9 | -14.2 … -13.6 | 1.000 | -22.9 | 0.17 | 0.15 | 7.50 | 0/10 | ne |
| F1_bid_heavy_1pct_and_up_momentum | 15m | fade(- | 12737 | 0.7 | -13.3 | -14.1 … -12.5 | 1.000 | -22.3 | 0.28 | 0.34 | 4.67 | 0/10 | ne |
| F1_bid_heavy_1pct_and_up_momentum | 60m | fade(- | 7514 | 3.7 | -10.3 | -12.9 … -7.5 | 1.000 | -19.4 | 0.40 | 0.67 | 2.75 | 0/10 | ne |
| F2_ask_heavy_1pct_and_dn_momentum | 1m | fade(- | 91343 | 0.2 | -13.8 | -14.4 … -13.2 | 1.000 | -22.8 | 0.08 | 0.10 | 33.46 | 0/10 | ne |
| F2_ask_heavy_1pct_and_dn_momentum | 5m | fade(- | 24606 | 0.3 | -13.7 | -16.0 … -11.0 | 1.000 | -22.7 | 0.20 | 0.29 | 9.01 | 0/10 | ne |
| F2_ask_heavy_1pct_and_dn_momentum | 15m | fade(- | 14154 | 0.3 | -13.7 | -16.9 … -9.6 | 1.000 | -22.9 | 0.29 | 0.45 | 5.18 | 0/10 | ne |
| F2_ask_heavy_1pct_and_dn_momentum | 60m | fade(- | 7873 | -0.2 | -14.2 | -18.9 … -8.8 | 1.000 | -23.3 | 0.39 | 0.62 | 2.88 | 0/10 | ne |
| F3_bid_heavy_0_2pct | 1m | follow | 114825 | 0.0 | -14.0 | -14.0 … -13.9 | 1.000 | -23.0 | 0.05 | 0.04 | 42.06 | 0/10 | ne |
| F3_bid_heavy_0_2pct | 5m | follow | 22965 | 0.1 | -13.9 | -14.3 … -13.5 | 1.000 | -22.8 | 0.16 | 0.16 | 8.41 | 0/10 | ne |
| F3_bid_heavy_0_2pct | 15m | follow | 16617 | -0.2 | -14.2 | -15.1 … -13.3 | 1.000 | -23.3 | 0.26 | 0.33 | 6.09 | 0/10 | ne |
| F3_bid_heavy_0_2pct | 60m | follow | 10519 | -2.4 | -16.4 | -18.9 … -13.8 | 1.000 | -25.4 | 0.36 | 0.52 | 3.85 | 0/10 | ne |
| F4_ask_heavy_0_2pct | 1m | follow | 117304 | -0.0 | -14.0 | -14.1 … -14.0 | 1.000 | -22.9 | 0.05 | 0.04 | 42.97 | 0/10 | ne |
| F4_ask_heavy_0_2pct | 5m | follow | 23460 | -0.2 | -14.2 | -14.6 … -13.8 | 1.000 | -22.9 | 0.16 | 0.17 | 8.59 | 0/10 | ne |
| F4_ask_heavy_0_2pct | 15m | follow | 17036 | -0.2 | -14.2 | -15.1 … -13.3 | 1.000 | -22.9 | 0.27 | 0.34 | 6.24 | 0/10 | ne |
| F4_ask_heavy_0_2pct | 60m | follow | 10617 | -0.9 | -14.9 | -17.3 … -12.3 | 1.000 | -23.2 | 0.37 | 0.56 | 3.89 | 0/10 | ne |
| F5_liquidity_thin_up_momentum | 1m | follow | 199923 | 0.0 | -14.0 | -14.1 … -13.9 | 1.000 | -22.9 | 0.09 | 0.08 | 73.23 | 0/10 | ne |
| F5_liquidity_thin_up_momentum | 5m | follow | 50278 | -0.0 | -14.0 | -14.6 … -13.4 | 1.000 | -22.9 | 0.21 | 0.26 | 18.42 | 0/10 | ne |
| F5_liquidity_thin_up_momentum | 15m | follow | 26639 | -0.3 | -14.3 | -15.5 … -13.1 | 1.000 | -23.3 | 0.30 | 0.42 | 9.76 | 0/10 | ne |
| F5_liquidity_thin_up_momentum | 60m | follow | 12970 | -1.4 | -15.4 | -18.5 … -12.0 | 1.000 | -24.5 | 0.38 | 0.60 | 4.75 | 0/10 | ne |
| F6_liquidity_thin_dn_momentum | 1m | follow | 183155 | -0.0 | -14.0 | -14.3 … -13.7 | 1.000 | -23.0 | 0.10 | 0.10 | 67.09 | 0/10 | ne |
| F6_liquidity_thin_dn_momentum | 5m | follow | 47240 | 0.3 | -13.7 | -15.2 … -11.8 | 1.000 | -23.6 | 0.23 | 0.31 | 17.30 | 0/10 | ne |
| F6_liquidity_thin_dn_momentum | 15m | follow | 25304 | -0.1 | -14.1 | -15.9 … -12.2 | 1.000 | -23.1 | 0.32 | 0.45 | 9.27 | 0/10 | ne |
| F6_liquidity_thin_dn_momentum | 60m | follow | 12383 | -3.1 | -17.1 | -21.0 … -13.4 | 1.000 | -26.2 | 0.39 | 0.58 | 4.54 | 0/10 | ne |
