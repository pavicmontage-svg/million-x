# STRATEGY COMPARISON — v1 vs v2 vs prototipovi, i Strategy Selection Engine

*Automatski generirano iz Research Engine 2.0 (`research2 run 44829762193 (22/23 strategija; bot10_adaptive još radi)`) — selekcijsko razdoblje 2025-01-01 – 2026-06-30, 10 instrumenata, završni holdout nije korišten. Brojevi nisu ručno upisani.*

## Interpretacija

- **v2 vrata pomažu, ali ne dovoljno.** Na 15m v2 smanjuje gubitak po tradeu u 7 od 9 botova:
  - bot1: −0,266 → −0,187 R;
  - bot4: −0,206 → −0,151 R;
  - bot9: −0,232 → −0,099 R;
  - bot5 i bot6 su malo lošiji.
  Na 60m su v2 gubici najmanji (bot9_v2 −0,040 R, bot5_v2 −0,081 R, bot4_v2 −0,102 R), ali i dalje negativni.
- Smanjenje gubitka dolazi većinom od **manjeg broja trgovina i manjeg troška u R**, a ne od bolje ulazne logike.
  Bruto prednost signala i dalje nije dokazana.
- **Prototipovi A–D** su svi negativni (−0,13 do −0,22 R). Najbolji je D (Cost-Aware Momentum, 15m, −0,129 R),
  što ponovno potvrđuje da veći timeframe i filtar troška smanjuju gubitak, ali ne stvaraju prednost.
- **Selection Engine:** 100/100 ćelija NO TRADE, jer nijedna strategija nije validirana.

## v1 → v2 (isti timeframe)

| bot | TF | v1 exp R | v2 exp R | v1 n | v2 n |
|---|---|---|---|---|---|
| bot1_vwap | 15m | -0.266 | -0.187 | 1219 | 288 |
| bot1_vwap | 60m | — (v1 nije na tom TF-u) | -0.049 | — | 14 |
| bot2_ema | 15m | -0.182 | -0.149 | 12815 | 6259 |
| bot2_ema | 60m | — (v1 nije na tom TF-u) | -0.127 | — | 2147 |
| bot3_orderflow | 15m | -0.248 | -0.196 | 537 | 305 |
| bot3_orderflow | 60m | — (v1 nije na tom TF-u) | -0.523 | — | 6 |
| bot4_breakout | 15m | -0.206 | -0.151 | 4089 | 1473 |
| bot4_breakout | 60m | — (v1 nije na tom TF-u) | -0.102 | — | 1304 |
| bot5_squeeze | 15m | -0.143 | -0.146 | 737 | 736 |
| bot5_squeeze | 60m | — (v1 nije na tom TF-u) | -0.081 | — | 449 |
| bot6_rsidiv | 15m | -0.164 | -0.174 | 1972 | 127 |
| bot6_rsidiv | 60m | — (v1 nije na tom TF-u) | 0.169 | — | 8 |
| bot7_liquidity | 15m | -0.174 | -0.163 | 4058 | 240 |
| bot7_liquidity | 60m | — (v1 nije na tom TF-u) | 0.027 | — | 90 |
| bot8_supertrend | 15m | -0.118 | -0.109 | 562 | 146 |
| bot8_supertrend | 60m | — (v1 nije na tom TF-u) | -0.198 | — | 86 |
| bot9_rangeexp | 15m | -0.232 | -0.099 | 812 | 618 |
| bot9_rangeexp | 60m | — (v1 nije na tom TF-u) | -0.040 | — | 429 |

## Strategy Selection Engine

Ćelije (instrument × režim): 100, NO TRADE: 100, odabrane strategije: nijedna.

| instrument | režim | odluka | najbolji kandidat | n | CI low | razlog |
|---|---|---|---|---|---|---|
| ADAUSDT | HIGH_VOLATILITY | NO_TRADE | bot1_vwap_v2@15 | 5 | 0.236 | not validated (Research 2.0 verdict); sample 5 < 30 |
| ADAUSDT | LOW_VOLATILITY | NO_TRADE | protoA_trend_pullback@5 | 12 | -0.074 | not validated (Research 2.0 verdict); sample 12 < 30; CI lower bound <= 0 |
| ADAUSDT | MARKET_DISLOCATION | NO_TRADE | bot2_ema@5 | 13 | -0.247 | not validated (Research 2.0 verdict); sample 13 < 30; CI lower bound <= 0 |
| ADAUSDT | SIDEWAYS | NO_TRADE | bot6_rsidiv@15 | 7 | 0.400 | not validated (Research 2.0 verdict); sample 7 < 30 |
| ADAUSDT | STRONG_BEAR_TREND | NO_TRADE | bot1_vwap@3 | 12 | -0.119 | not validated (Research 2.0 verdict); sample 12 < 30; CI lower bound <= 0 |
| ADAUSDT | STRONG_BULL_TREND | NO_TRADE | bot4_breakout_v2@15 | 19 | 0.221 | not validated (Research 2.0 verdict); sample 19 < 30 |
| ADAUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot6_rsidiv_v2@15 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| ADAUSDT | VOLATILITY_EXPANSION | NO_TRADE | protoB_sweep_reversal@5 | 4 | 0.336 | not validated (Research 2.0 verdict); sample 4 < 30 |
| ADAUSDT | WEAK_BEAR_TREND | NO_TRADE | bot8_supertrend_v2@60 | 2 | 0.816 | not validated (Research 2.0 verdict); sample 2 < 30 |
| ADAUSDT | WEAK_BULL_TREND | NO_TRADE | bot5_squeeze_v2@60 | 4 | 0.728 | not validated (Research 2.0 verdict); sample 4 < 30 |
| AVAXUSDT | HIGH_VOLATILITY | NO_TRADE | protoA_trend_pullback@5 | 3 | 0.147 | not validated (Research 2.0 verdict); sample 3 < 30 |
| AVAXUSDT | LOW_VOLATILITY | NO_TRADE | bot6_rsidiv_v2@15 | 3 | 0.700 | not validated (Research 2.0 verdict); sample 3 < 30 |
| AVAXUSDT | MARKET_DISLOCATION | NO_TRADE | bot6_rsidiv@15 | 2 | 0.523 | not validated (Research 2.0 verdict); sample 2 < 30 |
| AVAXUSDT | SIDEWAYS | NO_TRADE | bot9_rangeexp_v2@60 | 10 | 0.483 | not validated (Research 2.0 verdict); sample 10 < 30 |
| AVAXUSDT | STRONG_BEAR_TREND | NO_TRADE | bot2_ema_v2@15 | 112 | -0.133 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| AVAXUSDT | STRONG_BULL_TREND | NO_TRADE | bot9_rangeexp_v2@60 | 7 | -0.147 | not validated (Research 2.0 verdict); sample 7 < 30; CI lower bound <= 0 |
| AVAXUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot4_breakout_v2@15 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| AVAXUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot7_liquidity@15 | 32 | -0.011 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| AVAXUSDT | WEAK_BEAR_TREND | NO_TRADE | bot4_breakout_v2@60 | 6 | 0.236 | not validated (Research 2.0 verdict); sample 6 < 30 |
| AVAXUSDT | WEAK_BULL_TREND | NO_TRADE | protoD_cost_momentum@15 | 13 | -0.327 | not validated (Research 2.0 verdict); sample 13 < 30; CI lower bound <= 0 |
| BNBUSDT | HIGH_VOLATILITY | NO_TRADE | bot4_breakout_v2@60 | 3 | 0.326 | not validated (Research 2.0 verdict); sample 3 < 30 |
| BNBUSDT | LOW_VOLATILITY | NO_TRADE | bot2_ema_v2@60 | 29 | -0.202 | not validated (Research 2.0 verdict); sample 29 < 30; CI lower bound <= 0 |
| BNBUSDT | MARKET_DISLOCATION | NO_TRADE | bot1_vwap@15 | 4 | 0.126 | not validated (Research 2.0 verdict); sample 4 < 30 |
| BNBUSDT | SIDEWAYS | NO_TRADE | bot7_liquidity_v2@15 | 6 | -0.123 | not validated (Research 2.0 verdict); sample 6 < 30; CI lower bound <= 0 |
| BNBUSDT | STRONG_BEAR_TREND | NO_TRADE | protoD_cost_momentum@15 | 17 | -0.404 | not validated (Research 2.0 verdict); sample 17 < 30; CI lower bound <= 0 |
| BNBUSDT | STRONG_BULL_TREND | NO_TRADE | bot8_supertrend@15 | 3 | 0.581 | not validated (Research 2.0 verdict); sample 3 < 30 |
| BNBUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot1_vwap@15 | 4 | 0.141 | not validated (Research 2.0 verdict); sample 4 < 30 |
| BNBUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot5_squeeze_v2@60 | 10 | -0.383 | not validated (Research 2.0 verdict); sample 10 < 30; CI lower bound <= 0 |
| BNBUSDT | WEAK_BEAR_TREND | NO_TRADE | bot4_breakout_v2@15 | 15 | 0.111 | not validated (Research 2.0 verdict); sample 15 < 30 |
| BNBUSDT | WEAK_BULL_TREND | NO_TRADE | bot1_vwap_v2@15 | 3 | 0.913 | not validated (Research 2.0 verdict); sample 3 < 30 |
| BTCUSDT | HIGH_VOLATILITY | NO_TRADE | bot6_rsidiv_v2@15 | 2 | 0.704 | not validated (Research 2.0 verdict); sample 2 < 30 |
| BTCUSDT | LOW_VOLATILITY | NO_TRADE | bot2_ema_v2@60 | 19 | -0.254 | not validated (Research 2.0 verdict); sample 19 < 30; CI lower bound <= 0 |
| BTCUSDT | MARKET_DISLOCATION | NO_TRADE | protoD_cost_momentum@15 | 5 | -0.518 | not validated (Research 2.0 verdict); sample 5 < 30; CI lower bound <= 0 |
| BTCUSDT | SIDEWAYS | NO_TRADE | bot4_breakout_v2@60 | 8 | 0.169 | not validated (Research 2.0 verdict); sample 8 < 30 |
| BTCUSDT | STRONG_BEAR_TREND | NO_TRADE | bot3_orderflow_v2@15 | 2 | 0.487 | not validated (Research 2.0 verdict); sample 2 < 30 |
| BTCUSDT | STRONG_BULL_TREND | NO_TRADE | bot9_rangeexp_v2@60 | 8 | -0.434 | not validated (Research 2.0 verdict); sample 8 < 30; CI lower bound <= 0 |
| BTCUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot2_ema_v2@60 | 3 | -0.429 | not validated (Research 2.0 verdict); sample 3 < 30; CI lower bound <= 0 |
| BTCUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot5_squeeze_v2@15 | 4 | 0.460 | not validated (Research 2.0 verdict); sample 4 < 30 |
| BTCUSDT | WEAK_BEAR_TREND | NO_TRADE | bot4_breakout_v2@60 | 3 | 0.432 | not validated (Research 2.0 verdict); sample 3 < 30 |
| BTCUSDT | WEAK_BULL_TREND | NO_TRADE | bot4_breakout_v2@60 | 4 | 0.218 | not validated (Research 2.0 verdict); sample 4 < 30 |
| DOGEUSDT | HIGH_VOLATILITY | NO_TRADE | bot5_squeeze@5 | 8 | -0.007 | not validated (Research 2.0 verdict); sample 8 < 30; CI lower bound <= 0 |
| DOGEUSDT | LOW_VOLATILITY | NO_TRADE | bot6_rsidiv@15 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| DOGEUSDT | MARKET_DISLOCATION | NO_TRADE | bot7_liquidity@15 | 21 | -0.269 | not validated (Research 2.0 verdict); sample 21 < 30; CI lower bound <= 0 |
| DOGEUSDT | SIDEWAYS | NO_TRADE | protoD_cost_momentum@15 | 23 | -0.234 | not validated (Research 2.0 verdict); sample 23 < 30; CI lower bound <= 0 |
| DOGEUSDT | STRONG_BEAR_TREND | NO_TRADE | bot9_rangeexp@15 | 7 | -0.356 | not validated (Research 2.0 verdict); sample 7 < 30; CI lower bound <= 0 |
| DOGEUSDT | STRONG_BULL_TREND | NO_TRADE | bot8_supertrend_v2@60 | 6 | -0.286 | not validated (Research 2.0 verdict); sample 6 < 30; CI lower bound <= 0 |
| DOGEUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot2_ema_v2@15 | 18 | 0.057 | not validated (Research 2.0 verdict); sample 18 < 30 |
| DOGEUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot5_squeeze_v2@15 | 4 | 0.615 | not validated (Research 2.0 verdict); sample 4 < 30 |
| DOGEUSDT | WEAK_BEAR_TREND | NO_TRADE | protoD_cost_momentum@15 | 36 | -0.363 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| DOGEUSDT | WEAK_BULL_TREND | NO_TRADE | bot9_rangeexp@15 | 5 | -0.043 | not validated (Research 2.0 verdict); sample 5 < 30; CI lower bound <= 0 |
| ETHUSDT | HIGH_VOLATILITY | NO_TRADE | bot8_supertrend@15 | 8 | -0.480 | not validated (Research 2.0 verdict); sample 8 < 30; CI lower bound <= 0 |
| ETHUSDT | LOW_VOLATILITY | NO_TRADE | bot8_supertrend@15 | 7 | 0.065 | not validated (Research 2.0 verdict); sample 7 < 30 |
| ETHUSDT | MARKET_DISLOCATION | NO_TRADE | protoD_cost_momentum@15 | 15 | -0.571 | not validated (Research 2.0 verdict); sample 15 < 30; CI lower bound <= 0 |
| ETHUSDT | SIDEWAYS | NO_TRADE | bot9_rangeexp_v2@60 | 8 | -0.408 | not validated (Research 2.0 verdict); sample 8 < 30; CI lower bound <= 0 |
| ETHUSDT | STRONG_BEAR_TREND | NO_TRADE | bot3_orderflow_v2@15 | 5 | 0.557 | not validated (Research 2.0 verdict); sample 5 < 30 |
| ETHUSDT | STRONG_BULL_TREND | NO_TRADE | bot3_orderflow_v2@15 | 3 | 0.709 | not validated (Research 2.0 verdict); sample 3 < 30 |
| ETHUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot2_ema_v2@15 | 10 | -0.232 | not validated (Research 2.0 verdict); sample 10 < 30; CI lower bound <= 0 |
| ETHUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot7_liquidity_v2@15 | 5 | 0.581 | not validated (Research 2.0 verdict); sample 5 < 30 |
| ETHUSDT | WEAK_BEAR_TREND | NO_TRADE | bot9_rangeexp_v2@15 | 9 | -0.073 | not validated (Research 2.0 verdict); sample 9 < 30; CI lower bound <= 0 |
| ETHUSDT | WEAK_BULL_TREND | NO_TRADE | bot9_rangeexp_v2@15 | 7 | 0.255 | not validated (Research 2.0 verdict); sample 7 < 30 |
| LINKUSDT | HIGH_VOLATILITY | NO_TRADE | bot2_ema_v2@60 | 29 | -0.167 | not validated (Research 2.0 verdict); sample 29 < 30; CI lower bound <= 0 |
| LINKUSDT | LOW_VOLATILITY | NO_TRADE | bot4_breakout_v2@15 | 2 | -0.347 | not validated (Research 2.0 verdict); sample 2 < 30; CI lower bound <= 0 |
| LINKUSDT | MARKET_DISLOCATION | NO_TRADE | protoD_cost_momentum@15 | 14 | -0.147 | not validated (Research 2.0 verdict); sample 14 < 30; CI lower bound <= 0 |
| LINKUSDT | SIDEWAYS | NO_TRADE | bot6_rsidiv_v2@15 | 7 | -0.184 | not validated (Research 2.0 verdict); sample 7 < 30; CI lower bound <= 0 |
| LINKUSDT | STRONG_BEAR_TREND | NO_TRADE | bot3_orderflow_v2@15 | 9 | -0.010 | not validated (Research 2.0 verdict); sample 9 < 30; CI lower bound <= 0 |
| LINKUSDT | STRONG_BULL_TREND | NO_TRADE | bot3_orderflow_v2@15 | 4 | -0.300 | not validated (Research 2.0 verdict); sample 4 < 30; CI lower bound <= 0 |
| LINKUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot5_squeeze@15 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| LINKUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot7_liquidity_v2@60 | 4 | -0.232 | not validated (Research 2.0 verdict); sample 4 < 30; CI lower bound <= 0 |
| LINKUSDT | WEAK_BEAR_TREND | NO_TRADE | bot9_rangeexp_v2@15 | 10 | 0.088 | not validated (Research 2.0 verdict); sample 10 < 30 |
| LINKUSDT | WEAK_BULL_TREND | NO_TRADE | bot6_rsidiv@15 | 27 | -0.213 | not validated (Research 2.0 verdict); sample 27 < 30; CI lower bound <= 0 |
| LTCUSDT | HIGH_VOLATILITY | NO_TRADE | protoD_cost_momentum@15 | 29 | -0.260 | not validated (Research 2.0 verdict); sample 29 < 30; CI lower bound <= 0 |
| LTCUSDT | LOW_VOLATILITY | NO_TRADE | bot2_ema_v2@60 | 45 | -0.185 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| LTCUSDT | MARKET_DISLOCATION | NO_TRADE | bot4_breakout_v2@15 | 6 | -0.619 | not validated (Research 2.0 verdict); sample 6 < 30; CI lower bound <= 0 |
| LTCUSDT | SIDEWAYS | NO_TRADE | bot7_liquidity_v2@15 | 7 | -0.187 | not validated (Research 2.0 verdict); sample 7 < 30; CI lower bound <= 0 |
| LTCUSDT | STRONG_BEAR_TREND | NO_TRADE | bot9_rangeexp_v2@60 | 5 | -0.032 | not validated (Research 2.0 verdict); sample 5 < 30; CI lower bound <= 0 |
| LTCUSDT | STRONG_BULL_TREND | NO_TRADE | bot4_breakout_v2@60 | 26 | -0.112 | not validated (Research 2.0 verdict); sample 26 < 30; CI lower bound <= 0 |
| LTCUSDT | VOLATILITY_CONTRACTION | NO_TRADE | protoC_breakout_retest@5 | 2 | 0.691 | not validated (Research 2.0 verdict); sample 2 < 30 |
| LTCUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot6_rsidiv_v2@15 | 2 | 0.748 | not validated (Research 2.0 verdict); sample 2 < 30 |
| LTCUSDT | WEAK_BEAR_TREND | NO_TRADE | bot7_liquidity_v2@60 | 2 | 0.910 | not validated (Research 2.0 verdict); sample 2 < 30 |
| LTCUSDT | WEAK_BULL_TREND | NO_TRADE | protoD_cost_momentum@15 | 16 | -0.328 | not validated (Research 2.0 verdict); sample 16 < 30; CI lower bound <= 0 |
| SOLUSDT | HIGH_VOLATILITY | NO_TRADE | bot6_rsidiv_v2@60 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| SOLUSDT | LOW_VOLATILITY | NO_TRADE | bot9_rangeexp_v2@60 | 3 | 0.800 | not validated (Research 2.0 verdict); sample 3 < 30 |
| SOLUSDT | MARKET_DISLOCATION | NO_TRADE | protoD_cost_momentum@15 | 11 | -0.202 | not validated (Research 2.0 verdict); sample 11 < 30; CI lower bound <= 0 |
| SOLUSDT | SIDEWAYS | NO_TRADE | bot6_rsidiv_v2@15 | 4 | 0.794 | not validated (Research 2.0 verdict); sample 4 < 30 |
| SOLUSDT | STRONG_BEAR_TREND | NO_TRADE | bot1_vwap@3 | 6 | -0.416 | not validated (Research 2.0 verdict); sample 6 < 30; CI lower bound <= 0 |
| SOLUSDT | STRONG_BULL_TREND | NO_TRADE | bot9_rangeexp_v2@60 | 7 | 0.056 | not validated (Research 2.0 verdict); sample 7 < 30 |
| SOLUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot9_rangeexp_v2@15 | 7 | 0.532 | not validated (Research 2.0 verdict); sample 7 < 30 |
| SOLUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot2_ema_v2@60 | 15 | -0.041 | not validated (Research 2.0 verdict); sample 15 < 30; CI lower bound <= 0 |
| SOLUSDT | WEAK_BEAR_TREND | NO_TRADE | bot5_squeeze_v2@60 | 6 | 0.325 | not validated (Research 2.0 verdict); sample 6 < 30 |
| SOLUSDT | WEAK_BULL_TREND | NO_TRADE | bot1_vwap@3 | 60 | -0.171 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| XRPUSDT | HIGH_VOLATILITY | NO_TRADE | protoC_breakout_retest@5 | 17 | -0.319 | not validated (Research 2.0 verdict); sample 17 < 30; CI lower bound <= 0 |
| XRPUSDT | LOW_VOLATILITY | NO_TRADE | protoA_trend_pullback@5 | 13 | 0.141 | not validated (Research 2.0 verdict); sample 13 < 30 |
| XRPUSDT | MARKET_DISLOCATION | NO_TRADE | bot6_rsidiv@3 | 8 | -0.004 | not validated (Research 2.0 verdict); sample 8 < 30; CI lower bound <= 0 |
| XRPUSDT | SIDEWAYS | NO_TRADE | bot4_breakout_v2@15 | 11 | -0.052 | not validated (Research 2.0 verdict); sample 11 < 30; CI lower bound <= 0 |
| XRPUSDT | STRONG_BEAR_TREND | NO_TRADE | bot9_rangeexp@15 | 3 | -0.525 | not validated (Research 2.0 verdict); sample 3 < 30; CI lower bound <= 0 |
| XRPUSDT | STRONG_BULL_TREND | NO_TRADE | bot8_supertrend_v2@15 | 1 | -inf | not validated (Research 2.0 verdict); sample 1 < 30; CI lower bound <= 0 |
| XRPUSDT | VOLATILITY_CONTRACTION | NO_TRADE | bot2_ema@15 | 31 | -0.174 | not validated (Research 2.0 verdict); CI lower bound <= 0 |
| XRPUSDT | VOLATILITY_EXPANSION | NO_TRADE | bot9_rangeexp_v2@60 | 2 | 0.242 | not validated (Research 2.0 verdict); sample 2 < 30 |
| XRPUSDT | WEAK_BEAR_TREND | NO_TRADE | bot9_rangeexp_v2@15 | 12 | 0.267 | not validated (Research 2.0 verdict); sample 12 < 30 |
| XRPUSDT | WEAK_BULL_TREND | NO_TRADE | bot5_squeeze_v2@60 | 8 | 0.462 | not validated (Research 2.0 verdict); sample 8 < 30 |
