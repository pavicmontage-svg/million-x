# Scalping research 2.2 (event-driven, 1 s, aggTrades)

Candidates: 1293754 on 10 instruments. Chosen on discovery: {np.str_('A'): 'A2@60s', np.str_('B'): 'B2@300s', np.str_('M'): 'M2@180s'}.
**Verdict: {'A': 'NO_TRADE', 'B': 'NO_TRADE', 'M': 'NO_TRADE', 'A+B+M': 'NO_TRADE'}**

## Validation (Opportunity Engine filter, calibrated on discovery)

| engines | scenario | n | net R | net EUR | PF | WR | avg win R | avg loss R | max DD EUR | max loss streak | hold s | fee/gross | CI net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A | MM | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| B | MM | 0 | — | — | — | — | — | — | — | — | — | — | None |
| M | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| M | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| M | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| M | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| M | MM | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B+M | BASE | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B+M | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B+M | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B+M | MAKER | 0 | — | — | — | — | — | — | — | — | — | — | None |
| A+B+M | MM | 0 | — | — | — | — | — | — | — | — | — | — | None |

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
| B0@1s | 123895 | -2.837 | 116213 | -3.508 | 0.133 | -3733.09 | -2.168 |
| B0@60s | 1665 | -2.897 | 1630 | -3.611 | 0.051 | -53.89 | -2.281 |
| B0@180s | 641 | -3.032 | 615 | -3.661 | -0.048 | -20.62 | -2.310 |
| B0@300s | 373 | -2.800 | 379 | -3.600 | -0.029 | -12.49 | -2.265 |
| B1@1s | 130986 | -2.259 | 128307 | -2.795 | 0.115 | -3284.38 | -1.729 |
| B1@60s | 1740 | -2.255 | 1829 | -2.910 | 0.037 | -48.73 | -1.790 |
| B1@180s | 687 | -2.341 | 715 | -2.954 | -0.009 | -19.34 | -1.815 |
| B1@300s | 424 | -2.188 | 452 | -2.912 | -0.018 | -12.05 | -1.779 |
| B2@1s | 173273 | -1.153 | 163819 | -1.422 | 0.037 | -2132.55 | -0.886 |
| B2@60s | 2255 | -1.178 | 2301 | -1.448 | 0.013 | -30.51 | -0.903 |
| B2@180s | 866 | -1.229 | 889 | -1.455 | 0.003 | -11.85 | -0.903 |
| B2@300s | 510 | -1.134 | 558 | -1.446 | -0.007 | -7.39 | -0.906 |
| M0@1s | 35107 | -1.680 | 30174 | -1.687 | -0.006 | -466.18 | -1.056 |
| M0@60s | 456 | -1.680 | 387 | -1.693 | 0.019 | -6.00 | -1.067 |
| M0@180s | 129 | -1.660 | 101 | -1.797 | 0.019 | -1.66 | -1.151 |
| M0@300s | 70 | -1.868 | 62 | -1.482 | 0.124 | -0.84 | -0.828 |
| M1@1s | 6252 | -1.231 | 5389 | -1.223 | -0.002 | -60.36 | -0.776 |
| M1@60s | 80 | -0.948 | 80 | -1.013 | 0.054 | -0.74 | -0.679 |
| M1@180s | 16 | -1.409 | 23 | -0.859 | 0.281 | -0.18 | -0.602 |
| M1@300s | 14 | -1.231 | 14 | -1.008 | -0.107 | -0.13 | -0.692 |
| M2@1s | 35107 | -1.257 | 30174 | -1.261 | -0.001 | -348.56 | -0.789 |
| M2@60s | 456 | -1.243 | 387 | -1.266 | 0.018 | -4.49 | -0.796 |
| M2@180s | 129 | -1.225 | 101 | -1.322 | 0.040 | -1.22 | -0.807 |
| M2@300s | 70 | -1.375 | 62 | -1.112 | 0.093 | -0.63 | -0.580 |

## Maker-maker track (limit entry + limit take-profit, chosen on discovery)

- **A** `A2@60s`: n=375, net R=-0.342, PF=0.16, WR=0.18, net EUR/day=-1.18, CI=[-0.413, -0.2882], quarters=[-0.3487968772492655, -0.31361786029282884, -0.37083607270353] → **NO_TRADE**
- **B** `B2@300s`: n=409, net R=-0.905, PF=0.05, WR=0.06, net EUR/day=-3.39, CI=[-1.0144, -0.8186], quarters=[-0.8623470955576868, -0.9202198754359098, -0.9250240354972816] → **NO_TRADE**
- **M** `M1@1s`: n=4665, net R=-0.755, PF=0.06, WR=0.11, net EUR/day=-32.25, CI=[-0.7977, -0.7131], quarters=[-0.6979788560739757, -0.7410812126642935, -0.8793553416208175] → **NO_TRADE**

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
- M 5x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- M 10x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- M 15x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- M 20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- M STRESSED@20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B+M 5x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B+M 10x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B+M 15x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B+M 20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B+M STRESSED@20x: n=0, net EUR=—, trades/day=—, max DD EUR=—, skipped={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}

## Walk-forward (expanding calibration)

- A: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
- B: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
- M: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
- A+B+M: 2025-10-01: n=0 netR=— EUR=0.0; 2026-01-01: n=0 netR=— EUR=0.0; 2026-04-01: n=0 netR=— EUR=0.0
