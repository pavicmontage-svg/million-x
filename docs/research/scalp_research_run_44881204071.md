# Scalping research 2.2 (event-driven, 1 s, aggTrades)

Candidates: 1148645 on 10 instruments. Chosen on discovery: {'A': 'A2@60s', 'B': 'B2@300s'}.
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
| A0@1s | 81804 | -1.522 | 76352 | -1.527 | 0.048 | -1067.54 | -0.935 |
| A0@60s | 921 | -1.479 | 875 | -1.569 | 0.014 | -12.57 | -1.009 |
| A0@180s | 271 | -1.535 | 252 | -1.614 | -0.025 | -3.72 | -1.101 |
| A0@300s | 130 | -1.667 | 147 | -1.858 | -0.097 | -2.50 | -1.184 |
| A1@1s | 32021 | -1.497 | 32371 | -1.453 | 0.048 | -430.62 | -0.890 |
| A1@60s | 344 | -1.336 | 428 | -1.427 | 0.104 | -5.59 | -0.860 |
| A1@180s | 88 | -1.518 | 117 | -1.598 | -0.034 | -1.71 | -0.984 |
| A1@300s | 49 | -1.299 | 63 | -1.860 | 0.121 | -1.07 | -1.152 |
| A2@1s | 32021 | -0.572 | 32371 | -0.551 | 0.011 | -163.48 | -0.340 |
| A2@60s | 344 | -0.518 | 428 | -0.574 | 0.000 | -2.25 | -0.342 |
| A2@180s | 88 | -0.583 | 117 | -0.598 | -0.011 | -0.64 | -0.361 |
| A2@300s | 49 | -0.450 | 63 | -0.773 | -0.030 | -0.45 | -0.464 |
| B0@1s | 123895 | -2.837 | 116213 | -3.508 | 0.133 | -3733.17 | -2.168 |
| B0@60s | 1665 | -2.897 | 1630 | -3.611 | 0.051 | -53.90 | -2.281 |
| B0@180s | 641 | -3.032 | 615 | -3.661 | -0.048 | -20.62 | -2.310 |
| B0@300s | 373 | -2.800 | 379 | -3.600 | -0.029 | -12.49 | -2.265 |
| B1@1s | 130986 | -2.259 | 128307 | -2.795 | 0.115 | -3284.40 | -1.729 |
| B1@60s | 1740 | -2.255 | 1829 | -2.910 | 0.037 | -48.73 | -1.790 |
| B1@180s | 687 | -2.341 | 715 | -2.954 | -0.009 | -19.34 | -1.815 |
| B1@300s | 424 | -2.188 | 452 | -2.912 | -0.018 | -12.05 | -1.779 |
| B2@1s | 173273 | -1.153 | 163819 | -1.422 | 0.037 | -2132.57 | -0.886 |
| B2@60s | 2255 | -1.178 | 2301 | -1.448 | 0.013 | -30.51 | -0.903 |
| B2@180s | 866 | -1.229 | 889 | -1.455 | 0.003 | -11.85 | -0.903 |
| B2@300s | 510 | -1.134 | 558 | -1.446 | -0.007 | -7.39 | -0.906 |

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
