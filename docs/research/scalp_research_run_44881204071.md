# Scalping research 2.2 (event-driven, 1 s, aggTrades)

Candidates: 1019371 on 9 instruments. Chosen on discovery: {'A': 'A2@60s', 'B': 'B2@300s'}.
**Verdict: {'A': 'NO_TRADE', 'B': 'NO_TRADE', 'A+B': 'NO_TRADE'}**

## Validation (Opportunity Engine filter, calibrated on discovery)

| engines | scenario | n | net R | net EUR | PF | WR | avg win R | avg loss R | max DD EUR | max loss streak | hold s | fee/gross | CI net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |

## Raw candidates (no filter), BASE costs: discovery → validation net R (gross R)

| engine cfg @ tf | disc n | disc net R | val n | val net R | val gross R | val net EUR/day | val MAKER net R |
|---|---|---|---|---|---|---|---|
| A0@1s | 70116 | -1.243 | 64913 | -1.339 | 0.042 | -796.19 | -0.824 |
| A0@60s | 771 | -1.158 | 740 | -1.342 | 0.005 | -9.10 | -0.880 |
| A0@180s | 225 | -1.215 | 207 | -1.352 | -0.013 | -2.56 | -0.931 |
| A0@300s | 104 | -1.228 | 115 | -1.511 | -0.069 | -1.59 | -0.927 |
| A1@1s | 26872 | -1.194 | 27218 | -1.269 | 0.042 | -316.35 | -0.782 |
| A1@60s | 291 | -1.107 | 355 | -1.207 | 0.067 | -3.92 | -0.744 |
| A1@180s | 67 | -1.228 | 95 | -1.291 | -0.069 | -1.12 | -0.782 |
| A1@300s | 38 | -0.918 | 47 | -1.477 | 0.213 | -0.64 | -0.858 |
| A2@1s | 26872 | -0.457 | 27218 | -0.482 | 0.010 | -120.06 | -0.300 |
| A2@60s | 291 | -0.426 | 355 | -0.486 | -0.008 | -1.58 | -0.310 |
| A2@180s | 67 | -0.458 | 95 | -0.479 | -0.021 | -0.42 | -0.291 |
| A2@300s | 38 | -0.291 | 47 | -0.625 | 0.009 | -0.27 | -0.363 |
| B0@1s | 112936 | -2.629 | 103882 | -3.366 | 0.122 | -3202.19 | -2.093 |
| B0@60s | 1512 | -2.617 | 1461 | -3.430 | 0.044 | -45.90 | -2.176 |
| B0@180s | 587 | -2.668 | 556 | -3.543 | -0.048 | -18.04 | -2.263 |
| B0@300s | 339 | -2.526 | 344 | -3.490 | -0.055 | -10.99 | -2.183 |
| B1@1s | 118943 | -2.089 | 114415 | -2.677 | 0.104 | -2804.44 | -1.664 |
| B1@60s | 1572 | -2.063 | 1633 | -2.781 | 0.016 | -41.59 | -1.724 |
| B1@180s | 628 | -2.099 | 648 | -2.828 | -0.018 | -16.78 | -1.764 |
| B1@300s | 382 | -2.035 | 409 | -2.849 | -0.042 | -10.67 | -1.738 |
| B2@1s | 157507 | -1.067 | 146090 | -1.361 | 0.032 | -1820.92 | -0.853 |
| B2@60s | 2034 | -1.076 | 2066 | -1.377 | 0.009 | -26.04 | -0.858 |
| B2@180s | 790 | -1.099 | 807 | -1.396 | 0.002 | -10.32 | -0.873 |
| B2@300s | 458 | -1.039 | 513 | -1.408 | -0.016 | -6.62 | -0.875 |

## Portfolio (500 EUR, 2.50 EUR risk, 2 positions, 10 EUR/day)

- A 5x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 10x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 15x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A STRESSED@20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 5x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 10x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 15x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B STRESSED@20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 5x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 10x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 15x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B STRESSED@20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}

## Walk-forward (expanding calibration)

- A: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
- B: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
- A+B: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
