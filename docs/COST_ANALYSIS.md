# COST ANALYSIS — MillionX 2.1 (microstructure) + 2.0

## 2.1 — troškovi u odnosu na mikrostrukturne signale

| scenarij | sastav po round-tripu | trošak |
|---|---|---|
| BASE (taker) | 2 × 5 bps taker + 2 × 2 bps slippage | **14 bps** |
| STRESSED (taker) | 2 × 5 bps + 2 × 5 bps slippage + spread 3 bps + 1 min latencije | **23 bps** |
| maker/maker (nije testirano) | 2 × 2 bps maker prema konfiguraciji, bez slippagea, uz rizik da nalog ne bude ispunjen | ≈ 4 bps |

- Najveći bruto pomak na validaciji, među 96 testova A–F, je **+5,1 bps** (D2, 60 min). To je 37 % BASE troška.
  Uz taker izvršenje nijedan mikrostrukturni signal ne može pokriti troškove.
- Prag za isplativost: uvjet bi trebao imati bruto ≥ 14 bps (taker) ili ≥ 4 bps + adverse selection (maker), uz statističku potvrdu.
  Nijedan uvjet to nije pokazao.
- **Stvarni spread** za 2025–2026 nije dostupan iz arhive (bookTicker završava 2024-03). STRESSED scenarij koristi
  pretpostavljenih 3 bps. Collector od 2.1 mjeri stvarni spread i price impact za 50 000 USDT (book_1s.impact_*_bps).
- **Funding** ulazi u neto rezultat strategija od 1.9.0 (procjena iz konfigurirane stope). Kod horizonata od 1–60 min
  u ovom istraživanju funding je zanemariv i nije uključen.

---

## 2.0 — COST ANALYSIS — BASE / STRESSED / EXTREME (sačuvano iz 2.0)

*Automatski generirano iz Research Engine 2.0 (`research2 run 44829762193 (22/23 strategija; bot10_adaptive još radi)`) — selekcijsko razdoblje 2025-01-01 – 2026-06-30, 10 instrumenata, završni holdout nije korišten. Brojevi nisu ručno upisani.*

## Interpretacija

- Prelazak s BASE na STRESSED troškove (3–4 bps više po strani, 1 min latencije, 2 % odbijenih naloga) uzima dodatnih
  **do 0,08 R po trgovini** (medijan ≈ 0,04 R; strategije s n ≥ 30). Prelazak na EXTREME uzima još do 0,10 R (medijan ≈ 0,03 R).
- Strategije s puno trgovina na niskim timeframeovima (bot2/bot3/bot7 na 1–5m) gube 0,22–0,27 R već u BASE scenariju.
  Za njih bi bila potrebna bruto prednost od preko 0,15 R po trgovini samo da pokriju troškove, a takvu nije pokazala nijedna strategija.
- Jedina strategija koja je u BASE scenariju pozitivna na dovoljnom uzorku (bot7_liquidity_v2 60m, +0,027 R)
  postaje negativna u STRESSED scenariju. Margina od 0,03 R je manja od nesigurnosti troškova u stvarnom izvršavanju.

Scenariji: vidi `RESEARCH_METHODOLOGY.md` §3. STRESSED/EXTREME su ponovno simulirani za konfiguracije koje je WF stvarno odabrao, na istim test prozorima.

| strategija | BASE exp R | STRESSED exp R | EXTREME exp R | Δ R po dodatnom bps | preživi STRESSED |
|---|---|---|---|---|---|
| bot1_vwap 3m | -0.232 | -0.246 | -0.264 | {'STRESSED': -0.00173, 'EXTREME': -0.0014} | ne |
| bot1_vwap 15m | -0.266 | -0.292 | -0.295 | {'STRESSED': -0.00331, 'EXTREME': -0.00125} | ne |
| bot1_vwap_v2 15m | -0.187 | -0.230 | -0.270 | {'STRESSED': -0.00533, 'EXTREME': -0.00362} | ne |
| bot1_vwap_v2 60m | -0.049 | -0.130 | -0.197 | {'STRESSED': -0.01007, 'EXTREME': -0.00641} | ne |
| bot2_ema 5m | -0.225 | -0.250 | -0.260 | {'STRESSED': -0.00321, 'EXTREME': -0.00151} | ne |
| bot2_ema 15m | -0.182 | -0.218 | -0.246 | {'STRESSED': -0.00449, 'EXTREME': -0.00278} | ne |
| bot2_ema_v2 15m | -0.149 | -0.203 | -0.249 | {'STRESSED': -0.00665, 'EXTREME': -0.00434} | ne |
| bot2_ema_v2 60m | -0.127 | -0.160 | -0.216 | {'STRESSED': -0.00414, 'EXTREME': -0.00388} | ne |
| bot3_orderflow 1m | -0.252 | -0.263 | -0.267 | {'STRESSED': -0.00146, 'EXTREME': -0.00066} | ne |
| bot3_orderflow 15m | -0.248 | -0.251 | -0.254 | {'STRESSED': -0.00039, 'EXTREME': -0.00027} | ne |
| bot3_orderflow_v2 15m | -0.196 | -0.262 | -0.283 | {'STRESSED': -0.00832, 'EXTREME': -0.00379} | ne |
| bot3_orderflow_v2 60m | -0.523 | -0.562 | -0.698 | {'STRESSED': -0.00495, 'EXTREME': -0.00761} | ne |
| bot4_breakout 5m | -0.241 | -0.256 | -0.265 | {'STRESSED': -0.00177, 'EXTREME': -0.00103} | ne |
| bot4_breakout 15m | -0.206 | -0.222 | -0.252 | {'STRESSED': -0.00201, 'EXTREME': -0.002} | ne |
| bot4_breakout_v2 15m | -0.151 | -0.203 | -0.256 | {'STRESSED': -0.00659, 'EXTREME': -0.00457} | ne |
| bot4_breakout_v2 60m | -0.102 | -0.150 | -0.208 | {'STRESSED': -0.00597, 'EXTREME': -0.00459} | ne |
| bot5_squeeze 5m | -0.249 | -0.282 | -0.292 | {'STRESSED': -0.00416, 'EXTREME': -0.00189} | ne |
| bot5_squeeze 15m | -0.143 | -0.204 | -0.263 | {'STRESSED': -0.00755, 'EXTREME': -0.00523} | ne |
| bot5_squeeze_v2 15m | -0.146 | -0.225 | -0.269 | {'STRESSED': -0.00995, 'EXTREME': -0.00536} | ne |
| bot5_squeeze_v2 60m | -0.081 | -0.150 | -0.202 | {'STRESSED': -0.00867, 'EXTREME': -0.00525} | ne |
| bot6_rsidiv 3m | -0.213 | -0.248 | -0.261 | {'STRESSED': -0.0044, 'EXTREME': -0.00212} | ne |
| bot6_rsidiv 15m | -0.164 | -0.199 | -0.230 | {'STRESSED': -0.00436, 'EXTREME': -0.00284} | ne |
| bot6_rsidiv_v2 15m | -0.174 | -0.227 | -0.255 | {'STRESSED': -0.00672, 'EXTREME': -0.00352} | ne |
| bot6_rsidiv_v2 60m | 0.169 | 0.177 | 0.206 | {'STRESSED': 0.00104, 'EXTREME': 0.00162} | da |
| bot7_liquidity 3m | -0.231 | -0.255 | -0.261 | {'STRESSED': -0.00298, 'EXTREME': -0.00129} | ne |
| bot7_liquidity 15m | -0.174 | -0.211 | -0.242 | {'STRESSED': -0.00464, 'EXTREME': -0.00295} | ne |
| bot7_liquidity_v2 15m | -0.163 | -0.207 | -0.275 | {'STRESSED': -0.00542, 'EXTREME': -0.00484} | ne |
| bot7_liquidity_v2 60m | 0.027 | -0.032 | -0.118 | {'STRESSED': -0.00745, 'EXTREME': -0.00633} | ne |
| bot8_supertrend 5m | -0.194 | -0.236 | -0.256 | {'STRESSED': -0.0053, 'EXTREME': -0.00269} | ne |
| bot8_supertrend 15m | -0.118 | -0.162 | -0.228 | {'STRESSED': -0.00558, 'EXTREME': -0.00479} | ne |
| bot8_supertrend_v2 15m | -0.109 | -0.160 | -0.258 | {'STRESSED': -0.00634, 'EXTREME': -0.00645} | ne |
| bot8_supertrend_v2 60m | -0.198 | -0.219 | -0.273 | {'STRESSED': -0.00265, 'EXTREME': -0.00327} | ne |
| bot9_rangeexp 5m | -0.270 | -0.263 | -0.259 | {'STRESSED': 0.00085, 'EXTREME': 0.00047} | ne |
| bot9_rangeexp 15m | -0.232 | -0.243 | -0.244 | {'STRESSED': -0.00129, 'EXTREME': -0.00052} | ne |
| bot9_rangeexp_v2 15m | -0.099 | -0.151 | -0.170 | {'STRESSED': -0.00645, 'EXTREME': -0.00309} | ne |
| bot9_rangeexp_v2 60m | -0.040 | -0.098 | -0.152 | {'STRESSED': -0.00726, 'EXTREME': -0.00486} | ne |
| protoA_trend_pullback 5m | -0.193 | -0.240 | -0.262 | {'STRESSED': -0.00589, 'EXTREME': -0.00299} | ne |
| protoB_sweep_reversal 5m | -0.205 | -0.241 | -0.256 | {'STRESSED': -0.0044, 'EXTREME': -0.00221} | ne |
| protoC_breakout_retest 5m | -0.224 | -0.239 | -0.248 | {'STRESSED': -0.00188, 'EXTREME': -0.00104} | ne |
| protoD_cost_momentum 15m | -0.129 | -0.169 | -0.238 | {'STRESSED': -0.00498, 'EXTREME': -0.00476} | ne |

Ograničenja: djelomični fillovi nisu modelirani; povijesni order book ne postoji (bot3 = CVD-only); funding je procjena iz konfigurirane stope.
