# 2.2 — Scalping backtest (event-driven 1 s)

*Automatski generirano iz `scalp-research run 44881204071 (9/10 instrumenata; BTC se ponovno računa)` (event-driven 1 s simulacija na aggTrades, 9 instrumenata). Discovery 2025-01 – 2025-09 (kalibracija i izbor konfiguracije), validacija 2025-10 – 2026-06. Holdout 2026-07 – 2026-10 nije korišten. Brojevi nisu ručno upisani.*

**Presuda: {'A': 'NO_TRADE', 'B': 'NO_TRADE', 'A+B': 'NO_TRADE'}**

## Validacija nakon Opportunity Engine filtra

| engines | scenarij | n | neto R | neto EUR | PF | WR | avg win R | avg loss R | max DD EUR | max niz gubitaka | hold s | naknade/bruto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | BASE | 0 | — | — | — | — | — | — | — | — | — | — |
| A | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — |
| A | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — |
| A | MAKER | 0 | — | — | — | — | — | — | — | — | — | — |
| B | BASE | 0 | — | — | — | — | — | — | — | — | — | — |
| B | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — |
| B | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — |
| B | MAKER | 0 | — | — | — | — | — | — | — | — | — | — |
| A+B | BASE | 0 | — | — | — | — | — | — | — | — | — | — |
| A+B | STRESSED | 0 | — | — | — | — | — | — | — | — | — | — |
| A+B | EXTREME | 0 | — | — | — | — | — | — | — | — | — | — |
| A+B | MAKER | 0 | — | — | — | — | — | — | — | — | — | — |

## Portfelj (500 EUR, 2,50 EUR rizika, 2 pozicije, 10 EUR/dan, ISOLATED)

- A 5x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 10x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 15x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A 20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A STRESSED@20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 5x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 10x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 15x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B 20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- B STRESSED@20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 5x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 10x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 15x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B 20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}
- A+B STRESSED@20x: n=0, neto EUR=—, transakcija/dan=—, preskočeno={'max_positions': 0, 'margin': 0, 'daily_loss': 0, 'liquidation_buffer': 0}

Detalji po engineu: `MOMENTUM_SCALPER_REPORT.md`, `PULLBACK_SCALPER_REPORT.md`.

---


# BACKTEST REPORT — MillionX 2.0 (OOS, BASE troškovi)

*Automatski generirano iz Research Engine 2.0 (`research2 run 44829762193 (22/23 strategija; bot10_adaptive još radi)`) — selekcijsko razdoblje 2025-01-01 – 2026-06-30, 10 instrumenata, završni holdout nije korišten. Brojevi nisu ručno upisani.*

| strategija | n | WR | PF | exp R | avg win | avg loss | Sharpe | max DD | MC DD p95 | presuda |
|---|---|---|---|---|---|---|---|---|---|---|
| bot6_rsidiv_v2 60m | 8 | 0.500 | 1.45 | 0.169 | 0.5424 | -0.3732 | 0.17 | 0.005 | — | RESEARCH |
| bot7_liquidity_v2 60m | 90 | 0.589 | 1.07 | 0.027 | 0.3529 | -0.4723 | 0.03 | 0.046 | 0.075 | RESEARCH |
| bot9_rangeexp_v2 60m | 429 | 0.534 | 0.91 | -0.040 | 0.3836 | -0.4822 | -0.04 | 0.150 | 0.242 | RESEARCH |
| bot1_vwap_v2 60m | 14 | 0.643 | 0.86 | -0.049 | 0.2318 | -0.4859 | -0.06 | 0.013 | — | RESEARCH |
| bot5_squeeze_v2 60m | 449 | 0.512 | 0.83 | -0.081 | 0.3858 | -0.4883 | -0.08 | 0.249 | 0.305 | RESEARCH |
| bot9_rangeexp_v2 15m | 618 | 0.523 | 0.78 | -0.099 | 0.3395 | -0.4757 | -0.11 | 0.290 | 0.395 | RESEARCH |
| bot4_breakout_v2 60m | 1304 | 0.505 | 0.79 | -0.102 | 0.3743 | -0.4857 | -0.13 | 0.513 | 0.618 | RESEARCH |
| bot8_supertrend_v2 15m | 146 | 0.479 | 0.75 | -0.109 | 0.3479 | -0.4254 | -0.13 | 0.081 | 0.159 | RESEARCH |
| bot8_supertrend 15m | 562 | 0.482 | 0.74 | -0.118 | 0.3450 | -0.4349 | -0.14 | 0.298 | 0.398 | RESEARCH |
| bot2_ema_v2 60m | 2147 | 0.476 | 0.75 | -0.127 | 0.3918 | -0.4767 | 0.02 | 0.760 | 0.825 | RESEARCH |
| protoD_cost_momentum 15m | 1955 | 0.394 | 0.78 | -0.129 | 0.5863 | -0.4872 | -0.01 | 0.724 | 0.818 | RESEARCH |
| bot5_squeeze 15m | 737 | 0.525 | 0.68 | -0.143 | 0.2915 | -0.4730 | -0.18 | 0.423 | 0.513 | RESEARCH |
| bot5_squeeze_v2 15m | 736 | 0.501 | 0.70 | -0.146 | 0.3370 | -0.4848 | -0.17 | 0.423 | 0.523 | RESEARCH |
| bot2_ema_v2 15m | 6259 | 0.482 | 0.69 | -0.149 | 0.3476 | -0.4681 | -0.01 | 0.992 | 0.995 | RESEARCH |
| bot4_breakout_v2 15m | 1473 | 0.486 | 0.69 | -0.151 | 0.3479 | -0.4756 | 0.02 | 0.684 | 0.757 | RESEARCH |
| bot7_liquidity_v2 15m | 240 | 0.492 | 0.67 | -0.163 | 0.3305 | -0.4805 | -0.20 | 0.191 | 0.268 | RESEARCH |
| bot6_rsidiv 15m | 1972 | 0.440 | 0.67 | -0.164 | 0.3864 | -0.4507 | -0.01 | 0.817 | 0.861 | RESEARCH |
| bot6_rsidiv_v2 15m | 127 | 0.425 | 0.67 | -0.174 | 0.4206 | -0.4622 | -0.19 | 0.125 | 0.184 | RESEARCH |
| bot7_liquidity 15m | 4058 | 0.496 | 0.63 | -0.174 | 0.3041 | -0.4713 | 0.02 | 0.973 | 0.981 | RESEARCH |
| bot2_ema 15m | 12815 | 0.486 | 0.62 | -0.182 | 0.3085 | -0.4680 | 0.00 | 1.000 | 1.000 | RESEARCH |
| bot1_vwap_v2 15m | 288 | 0.514 | 0.60 | -0.187 | 0.2784 | -0.4868 | -0.24 | 0.266 | 0.327 | RESEARCH |
| protoA_trend_pullback 5m | 1151 | 0.383 | 0.62 | -0.193 | 0.4029 | -0.4066 | -0.02 | 0.692 | 0.745 | RESEARCH |
| bot8_supertrend 5m | 867 | 0.442 | 0.56 | -0.194 | 0.2836 | -0.3982 | -0.27 | 0.572 | 0.644 | RESEARCH |
| bot3_orderflow_v2 15m | 305 | 0.485 | 0.61 | -0.196 | 0.3123 | -0.4847 | -0.24 | 0.260 | 0.349 | RESEARCH |
| bot8_supertrend_v2 60m | 86 | 0.419 | 0.62 | -0.198 | 0.3781 | -0.4426 | -0.23 | 0.093 | 0.147 | RESEARCH |
| protoB_sweep_reversal 5m | 2758 | 0.484 | 0.55 | -0.205 | 0.2556 | -0.4389 | 0.02 | 0.944 | 0.958 | RESEARCH |
| bot4_breakout 15m | 4089 | 0.492 | 0.57 | -0.206 | 0.2816 | -0.4751 | -0.01 | 0.986 | 0.991 | RESEARCH |
| bot6_rsidiv 3m | 9835 | 0.439 | 0.57 | -0.213 | 0.3193 | -0.4388 | 0.01 | 1.000 | 1.000 | RESEARCH |
| protoC_breakout_retest 5m | 2516 | 0.391 | 0.60 | -0.224 | 0.4344 | -0.4620 | 0.02 | 0.942 | 0.960 | RESEARCH |
| bot2_ema 5m | 24007 | 0.472 | 0.52 | -0.225 | 0.2593 | -0.4448 | -0.00 | 1.000 | 1.000 | RESEARCH |
| bot7_liquidity 3m | 15484 | 0.483 | 0.51 | -0.231 | 0.2461 | -0.4542 | -0.00 | 1.000 | 1.000 | RESEARCH |
| bot1_vwap 3m | 4091 | 0.401 | 0.56 | -0.232 | 0.3744 | -0.4444 | 0.02 | 0.992 | 0.995 | RESEARCH |
| bot9_rangeexp 15m | 812 | 0.473 | 0.52 | -0.232 | 0.2669 | -0.4600 | -0.21 | 0.613 | 0.679 | RESEARCH |
| bot4_breakout 5m | 9937 | 0.476 | 0.49 | -0.241 | 0.2451 | -0.4531 | -0.01 | 1.000 | 1.000 | RESEARCH |
| bot3_orderflow 15m | 537 | 0.467 | 0.51 | -0.248 | 0.2711 | -0.4709 | -0.32 | 0.495 | 0.560 | RESEARCH |
| bot5_squeeze 5m | 1029 | 0.476 | 0.48 | -0.249 | 0.2433 | -0.4587 | 0.04 | 0.728 | 0.774 | RESEARCH |
| bot3_orderflow 1m | 75565 | 0.484 | 0.47 | -0.252 | 0.2296 | -0.4591 | 0.01 | 1.000 | 1.000 | RESEARCH |
| bot1_vwap 15m | 1219 | 0.441 | 0.50 | -0.266 | 0.3027 | -0.4772 | -0.00 | 0.805 | 0.847 | RESEARCH |
| bot9_rangeexp 5m | 1791 | 0.434 | 0.43 | -0.270 | 0.2349 | -0.4190 | 0.03 | 0.913 | 0.931 | RESEARCH |
| bot3_orderflow_v2 60m | 6 | 0.333 | 0.19 | -0.523 | 0.1837 | -0.4838 | -0.76 | 0.016 | — | RESEARCH |

## LONG / SHORT (OOS)

| strategija | LONG n | LONG exp R | SHORT n | SHORT exp R |
|---|---|---|---|---|
| bot1_vwap 3m | 1993 | -0.243 | 2098 | -0.222 |
| bot1_vwap 15m | 573 | -0.271 | 646 | -0.261 |
| bot1_vwap_v2 15m | 157 | -0.271 | 131 | -0.087 |
| bot1_vwap_v2 60m | 5 | -0.035 | 9 | -0.057 |
| bot2_ema 5m | 11850 | -0.242 | 12157 | -0.208 |
| bot2_ema 15m | 6357 | -0.195 | 6458 | -0.169 |
| bot2_ema_v2 15m | 3012 | -0.170 | 3247 | -0.130 |
| bot2_ema_v2 60m | 1038 | -0.159 | 1109 | -0.096 |
| bot3_orderflow 1m | 34719 | -0.260 | 40846 | -0.244 |
| bot3_orderflow 15m | 194 | -0.293 | 343 | -0.223 |
| bot3_orderflow_v2 15m | 75 | -0.178 | 230 | -0.202 |
| bot3_orderflow_v2 60m | 2 | 0.367 | 4 | -0.968 |
| bot4_breakout 5m | 4586 | -0.245 | 5351 | -0.238 |
| bot4_breakout 15m | 1864 | -0.210 | 2225 | -0.202 |
| bot4_breakout_v2 15m | 607 | -0.142 | 866 | -0.157 |
| bot4_breakout_v2 60m | 598 | -0.060 | 706 | -0.138 |
| bot5_squeeze 5m | 489 | -0.219 | 540 | -0.276 |
| bot5_squeeze 15m | 365 | -0.169 | 372 | -0.118 |
| bot5_squeeze_v2 15m | 323 | -0.117 | 413 | -0.168 |
| bot5_squeeze_v2 60m | 205 | -0.169 | 244 | -0.007 |
| bot6_rsidiv 3m | 3336 | -0.229 | 6499 | -0.204 |
| bot6_rsidiv 15m | 693 | -0.182 | 1279 | -0.155 |
| bot6_rsidiv_v2 15m | 53 | -0.259 | 74 | -0.112 |
| bot6_rsidiv_v2 60m | 2 | 1.050 | 6 | -0.124 |
| bot7_liquidity 3m | 8006 | -0.237 | 7478 | -0.225 |
| bot7_liquidity 15m | 2069 | -0.180 | 1989 | -0.168 |
| bot7_liquidity_v2 15m | 112 | -0.315 | 128 | -0.031 |
| bot7_liquidity_v2 60m | 48 | 0.141 | 42 | -0.102 |
| bot8_supertrend 5m | 433 | -0.196 | 434 | -0.192 |
| bot8_supertrend 15m | 289 | -0.130 | 273 | -0.105 |
| bot8_supertrend_v2 15m | 78 | -0.048 | 68 | -0.179 |
| bot8_supertrend_v2 60m | 53 | -0.066 | 33 | -0.411 |
| bot9_rangeexp 5m | 901 | -0.283 | 890 | -0.256 |
| bot9_rangeexp 15m | 378 | -0.250 | 434 | -0.217 |
| bot9_rangeexp_v2 15m | 242 | -0.080 | 376 | -0.112 |
| bot9_rangeexp_v2 60m | 155 | 0.002 | 274 | -0.064 |
| protoA_trend_pullback 5m | 572 | -0.177 | 579 | -0.208 |
| protoB_sweep_reversal 5m | 1364 | -0.228 | 1394 | -0.183 |
| protoC_breakout_retest 5m | 1344 | -0.197 | 1172 | -0.254 |
| protoD_cost_momentum 15m | 727 | -0.141 | 1228 | -0.122 |

## Po režimu (Regime 2.0), samo ćelije s n ≥ 30

- **bot1_vwap 3m**: HIGH_VOLATILITY -0.272 R (n=204), LOW_VOLATILITY -0.244 R (n=632), MARKET_DISLOCATION -0.182 R (n=46), SIDEWAYS -0.250 R (n=1238), STRONG_BEAR_TREND -0.117 R (n=88), STRONG_BULL_TREND -0.312 R (n=113), VOLATILITY_CONTRACTION -0.126 R (n=245), VOLATILITY_EXPANSION -0.305 R (n=242), WEAK_BEAR_TREND -0.211 R (n=669), WEAK_BULL_TREND -0.212 R (n=614)
- **bot1_vwap 15m**: HIGH_VOLATILITY -0.102 R (n=58), LOW_VOLATILITY -0.438 R (n=118), SIDEWAYS -0.220 R (n=251), STRONG_BEAR_TREND -0.232 R (n=49), STRONG_BULL_TREND -0.307 R (n=60), VOLATILITY_EXPANSION -0.328 R (n=129), WEAK_BEAR_TREND -0.274 R (n=234), WEAK_BULL_TREND -0.275 R (n=278)
- **bot1_vwap_v2 15m**: HIGH_VOLATILITY -0.038 R (n=45), SIDEWAYS -0.165 R (n=71), VOLATILITY_EXPANSION -0.077 R (n=30), WEAK_BEAR_TREND -0.371 R (n=84), WEAK_BULL_TREND -0.129 R (n=51)
- **bot2_ema 5m**: HIGH_VOLATILITY -0.187 R (n=736), LOW_VOLATILITY -0.255 R (n=1652), MARKET_DISLOCATION -0.052 R (n=128), SIDEWAYS -0.246 R (n=3469), STRONG_BEAR_TREND -0.217 R (n=4052), STRONG_BULL_TREND -0.255 R (n=3439), VOLATILITY_CONTRACTION -0.229 R (n=771), VOLATILITY_EXPANSION -0.186 R (n=1660), WEAK_BEAR_TREND -0.206 R (n=4123), WEAK_BULL_TREND -0.224 R (n=3977)
- **bot2_ema 15m**: HIGH_VOLATILITY -0.093 R (n=515), LOW_VOLATILITY -0.208 R (n=1207), MARKET_DISLOCATION -0.310 R (n=53), SIDEWAYS -0.177 R (n=2628), STRONG_BEAR_TREND -0.161 R (n=1556), STRONG_BULL_TREND -0.201 R (n=1276), VOLATILITY_CONTRACTION -0.219 R (n=384), VOLATILITY_EXPANSION -0.141 R (n=697), WEAK_BEAR_TREND -0.188 R (n=2336), WEAK_BULL_TREND -0.193 R (n=2163)
- **bot2_ema_v2 15m**: HIGH_VOLATILITY -0.049 R (n=275), LOW_VOLATILITY -0.157 R (n=263), MARKET_DISLOCATION -0.357 R (n=39), SIDEWAYS -0.141 R (n=1132), STRONG_BEAR_TREND -0.125 R (n=1034), STRONG_BULL_TREND -0.181 R (n=809), VOLATILITY_CONTRACTION -0.080 R (n=81), VOLATILITY_EXPANSION -0.121 R (n=520), WEAK_BEAR_TREND -0.163 R (n=1107), WEAK_BULL_TREND -0.181 R (n=999)
- **bot2_ema_v2 60m**: HIGH_VOLATILITY -0.034 R (n=273), LOW_VOLATILITY -0.089 R (n=388), SIDEWAYS -0.136 R (n=1036), VOLATILITY_CONTRACTION -0.151 R (n=39), VOLATILITY_EXPANSION -0.186 R (n=169), WEAK_BEAR_TREND -0.141 R (n=116), WEAK_BULL_TREND -0.249 R (n=103)
- **bot3_orderflow 1m**: HIGH_VOLATILITY -0.250 R (n=3919), LOW_VOLATILITY -0.229 R (n=6646), MARKET_DISLOCATION -0.271 R (n=232), SIDEWAYS -0.247 R (n=17909), STRONG_BEAR_TREND -0.268 R (n=9927), STRONG_BULL_TREND -0.248 R (n=8864), VOLATILITY_CONTRACTION -0.282 R (n=2527), VOLATILITY_EXPANSION -0.239 R (n=4744), WEAK_BEAR_TREND -0.265 R (n=10871), WEAK_BULL_TREND -0.243 R (n=9926)
- **bot3_orderflow 15m**: LOW_VOLATILITY -0.288 R (n=98), SIDEWAYS -0.224 R (n=121), STRONG_BEAR_TREND -0.357 R (n=69), STRONG_BULL_TREND -0.341 R (n=46), WEAK_BEAR_TREND -0.251 R (n=108), WEAK_BULL_TREND -0.131 R (n=52)
- **bot3_orderflow_v2 15m**: SIDEWAYS -0.235 R (n=54), STRONG_BEAR_TREND -0.231 R (n=83), WEAK_BEAR_TREND -0.227 R (n=67)
- **bot4_breakout 5m**: HIGH_VOLATILITY -0.179 R (n=458), LOW_VOLATILITY -0.232 R (n=1019), MARKET_DISLOCATION -0.286 R (n=87), SIDEWAYS -0.248 R (n=2269), STRONG_BEAR_TREND -0.269 R (n=1265), STRONG_BULL_TREND -0.188 R (n=976), VOLATILITY_CONTRACTION -0.306 R (n=339), VOLATILITY_EXPANSION -0.190 R (n=645), WEAK_BEAR_TREND -0.223 R (n=1562), WEAK_BULL_TREND -0.300 R (n=1317)
- **bot4_breakout 15m**: HIGH_VOLATILITY -0.233 R (n=79), LOW_VOLATILITY -0.129 R (n=164), MARKET_DISLOCATION -0.101 R (n=92), SIDEWAYS -0.166 R (n=401), STRONG_BEAR_TREND -0.222 R (n=750), STRONG_BULL_TREND -0.236 R (n=595), VOLATILITY_CONTRACTION -0.094 R (n=31), VOLATILITY_EXPANSION -0.191 R (n=461), WEAK_BEAR_TREND -0.210 R (n=810), WEAK_BULL_TREND -0.222 R (n=706)
- **bot4_breakout_v2 15m**: HIGH_VOLATILITY -0.245 R (n=59), MARKET_DISLOCATION -0.081 R (n=69), SIDEWAYS -0.081 R (n=125), STRONG_BEAR_TREND -0.191 R (n=329), STRONG_BULL_TREND -0.099 R (n=234), VOLATILITY_EXPANSION -0.169 R (n=264), WEAK_BEAR_TREND -0.148 R (n=227), WEAK_BULL_TREND -0.144 R (n=151)
- **bot4_breakout_v2 60m**: SIDEWAYS -0.237 R (n=70), STRONG_BEAR_TREND -0.169 R (n=432), STRONG_BULL_TREND -0.054 R (n=288), VOLATILITY_EXPANSION -0.087 R (n=309), WEAK_BEAR_TREND +0.011 R (n=65), WEAK_BULL_TREND -0.043 R (n=88)
- **bot5_squeeze 5m**: HIGH_VOLATILITY -0.334 R (n=56), LOW_VOLATILITY -0.226 R (n=119), SIDEWAYS -0.216 R (n=280), STRONG_BEAR_TREND -0.281 R (n=120), STRONG_BULL_TREND -0.216 R (n=96), VOLATILITY_CONTRACTION -0.313 R (n=113), WEAK_BEAR_TREND -0.209 R (n=117), WEAK_BULL_TREND -0.272 R (n=123)
- **bot5_squeeze 15m**: HIGH_VOLATILITY -0.202 R (n=38), LOW_VOLATILITY -0.176 R (n=184), SIDEWAYS -0.112 R (n=285), STRONG_BEAR_TREND -0.051 R (n=37), WEAK_BEAR_TREND -0.181 R (n=73), WEAK_BULL_TREND -0.180 R (n=51)
- **bot5_squeeze_v2 15m**: HIGH_VOLATILITY -0.209 R (n=47), LOW_VOLATILITY -0.419 R (n=46), SIDEWAYS -0.154 R (n=243), STRONG_BEAR_TREND -0.171 R (n=91), STRONG_BULL_TREND -0.001 R (n=56), WEAK_BEAR_TREND -0.101 R (n=109), WEAK_BULL_TREND -0.217 R (n=91)
- **bot5_squeeze_v2 60m**: STRONG_BEAR_TREND -0.118 R (n=134), STRONG_BULL_TREND -0.199 R (n=100), VOLATILITY_EXPANSION -0.165 R (n=78), WEAK_BEAR_TREND +0.213 R (n=50), WEAK_BULL_TREND +0.024 R (n=54)
- **bot6_rsidiv 3m**: HIGH_VOLATILITY -0.178 R (n=548), LOW_VOLATILITY -0.215 R (n=888), MARKET_DISLOCATION -0.162 R (n=76), SIDEWAYS -0.209 R (n=2234), STRONG_BEAR_TREND -0.232 R (n=1018), STRONG_BULL_TREND -0.222 R (n=1232), VOLATILITY_CONTRACTION -0.260 R (n=311), VOLATILITY_EXPANSION -0.198 R (n=812), WEAK_BEAR_TREND -0.198 R (n=1132), WEAK_BULL_TREND -0.219 R (n=1584)
- **bot6_rsidiv 15m**: LOW_VOLATILITY -0.173 R (n=39), SIDEWAYS -0.042 R (n=101), STRONG_BEAR_TREND -0.195 R (n=464), STRONG_BULL_TREND -0.157 R (n=699), VOLATILITY_CONTRACTION -0.749 R (n=31), VOLATILITY_EXPANSION -0.152 R (n=214), WEAK_BEAR_TREND -0.278 R (n=87), WEAK_BULL_TREND -0.131 R (n=288)
- **bot6_rsidiv_v2 15m**: SIDEWAYS +0.008 R (n=47)
- **bot7_liquidity 3m**: HIGH_VOLATILITY -0.211 R (n=669), LOW_VOLATILITY -0.291 R (n=1600), MARKET_DISLOCATION -0.063 R (n=104), SIDEWAYS -0.214 R (n=3520), STRONG_BEAR_TREND -0.215 R (n=2126), STRONG_BULL_TREND -0.239 R (n=1790), VOLATILITY_CONTRACTION -0.237 R (n=410), VOLATILITY_EXPANSION -0.275 R (n=916), WEAK_BEAR_TREND -0.256 R (n=2291), WEAK_BULL_TREND -0.191 R (n=2058)
- **bot7_liquidity 15m**: HIGH_VOLATILITY -0.126 R (n=171), LOW_VOLATILITY -0.225 R (n=166), MARKET_DISLOCATION -0.051 R (n=204), SIDEWAYS -0.139 R (n=688), STRONG_BEAR_TREND -0.208 R (n=667), STRONG_BULL_TREND -0.169 R (n=537), VOLATILITY_EXPANSION -0.196 R (n=439), WEAK_BEAR_TREND -0.174 R (n=606), WEAK_BULL_TREND -0.192 R (n=563)
- **bot7_liquidity_v2 15m**: HIGH_VOLATILITY -0.480 R (n=31), SIDEWAYS -0.044 R (n=90), VOLATILITY_EXPANSION +0.177 R (n=35), WEAK_BEAR_TREND -0.366 R (n=30)
- **bot8_supertrend 5m**: HIGH_VOLATILITY -0.263 R (n=38), LOW_VOLATILITY -0.190 R (n=139), SIDEWAYS -0.242 R (n=225), STRONG_BEAR_TREND -0.002 R (n=75), STRONG_BULL_TREND -0.183 R (n=79), VOLATILITY_CONTRACTION -0.356 R (n=64), VOLATILITY_EXPANSION -0.128 R (n=33), WEAK_BEAR_TREND -0.122 R (n=94), WEAK_BULL_TREND -0.195 R (n=117)
- **bot8_supertrend 15m**: HIGH_VOLATILITY -0.114 R (n=45), LOW_VOLATILITY +0.004 R (n=69), SIDEWAYS -0.106 R (n=236), VOLATILITY_EXPANSION -0.162 R (n=33), WEAK_BEAR_TREND -0.118 R (n=57), WEAK_BULL_TREND -0.141 R (n=49)
- **bot8_supertrend_v2 15m**: SIDEWAYS -0.151 R (n=49), WEAK_BULL_TREND -0.102 R (n=30)
- **bot8_supertrend_v2 60m**: STRONG_BULL_TREND +0.110 R (n=32)
- **bot9_rangeexp 5m**: HIGH_VOLATILITY -0.183 R (n=91), LOW_VOLATILITY -0.303 R (n=207), SIDEWAYS -0.281 R (n=460), STRONG_BEAR_TREND -0.244 R (n=162), STRONG_BULL_TREND -0.283 R (n=117), VOLATILITY_CONTRACTION -0.359 R (n=386), WEAK_BEAR_TREND -0.244 R (n=176), WEAK_BULL_TREND -0.114 R (n=186)
- **bot9_rangeexp 15m**: LOW_VOLATILITY -0.315 R (n=224), SIDEWAYS -0.214 R (n=200), VOLATILITY_CONTRACTION -0.281 R (n=156), WEAK_BEAR_TREND -0.127 R (n=74), WEAK_BULL_TREND -0.121 R (n=75)
- **bot9_rangeexp_v2 15m**: HIGH_VOLATILITY -0.194 R (n=41), LOW_VOLATILITY -0.272 R (n=90), SIDEWAYS -0.171 R (n=180), STRONG_BEAR_TREND -0.039 R (n=61), STRONG_BULL_TREND -0.207 R (n=45), VOLATILITY_CONTRACTION +0.073 R (n=36), WEAK_BEAR_TREND +0.053 R (n=88), WEAK_BULL_TREND +0.027 R (n=66)
- **bot9_rangeexp_v2 60m**: SIDEWAYS -0.104 R (n=55), STRONG_BEAR_TREND -0.064 R (n=89), STRONG_BULL_TREND +0.117 R (n=63), WEAK_BEAR_TREND -0.088 R (n=109), WEAK_BULL_TREND -0.074 R (n=53)
- **protoA_trend_pullback 5m**: LOW_VOLATILITY -0.045 R (n=117), SIDEWAYS -0.266 R (n=184), STRONG_BEAR_TREND -0.189 R (n=242), STRONG_BULL_TREND -0.220 R (n=215), VOLATILITY_CONTRACTION -0.281 R (n=64), VOLATILITY_EXPANSION -0.149 R (n=40), WEAK_BEAR_TREND -0.222 R (n=141), WEAK_BULL_TREND -0.156 R (n=121)
- **protoB_sweep_reversal 5m**: HIGH_VOLATILITY -0.222 R (n=190), LOW_VOLATILITY -0.192 R (n=707), SIDEWAYS -0.209 R (n=1300), VOLATILITY_CONTRACTION -0.252 R (n=206), VOLATILITY_EXPANSION -0.213 R (n=47), WEAK_BEAR_TREND -0.236 R (n=143), WEAK_BULL_TREND -0.141 R (n=154)
- **protoC_breakout_retest 5m**: HIGH_VOLATILITY -0.155 R (n=104), MARKET_DISLOCATION -0.225 R (n=88), SIDEWAYS -0.396 R (n=123), STRONG_BEAR_TREND -0.332 R (n=411), STRONG_BULL_TREND -0.212 R (n=578), VOLATILITY_EXPANSION -0.175 R (n=790), WEAK_BEAR_TREND -0.260 R (n=202), WEAK_BULL_TREND -0.129 R (n=207)
- **protoD_cost_momentum 15m**: HIGH_VOLATILITY -0.185 R (n=281), MARKET_DISLOCATION +0.073 R (n=125), SIDEWAYS -0.112 R (n=163), STRONG_BEAR_TREND -0.231 R (n=388), STRONG_BULL_TREND -0.268 R (n=191), VOLATILITY_EXPANSION -0.045 R (n=416), WEAK_BEAR_TREND -0.144 R (n=270), WEAK_BULL_TREND +0.061 R (n=121)
