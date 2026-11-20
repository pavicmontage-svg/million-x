# STRATEGY IMPROVEMENTS — MillionX 2.0 (faze 2–5)

Sve izmjene su **verzionirane**:
- v1 moduli (`strategies/*.py` za bot1–bot9) nisu mijenjani i jednaki su verziji 1.9.0 (grana `release-1.9.0`).
- Nove strategije su zasebne klase registrirane kao *research-only*. Live/paper engine ih ne gradi
  (`build_bots()` bez `include`), pa ih ne može trgovati dok ne prođu Activation 2.0 i vlasnik ih ne aktivira.

Razlog svake izmjene je u `STRATEGY_FORENSIC_REPORT.md`. Izmjene su mehaničke (troškovi, kauzalni filtri
režima). Nisu rezultat traženja parametara koji najbolje izgledaju u uzorku.

## 1. Regime Intelligence 2.0 (`regime2.py`)

Ima 10 kauzalnih režima po instrumentu:
- STRONG_BULL_TREND, WEAK_BULL_TREND;
- STRONG_BEAR_TREND, WEAK_BEAR_TREND;
- SIDEWAYS, HIGH_VOLATILITY, LOW_VOLATILITY;
- VOLATILITY_EXPANSION, VOLATILITY_CONTRACTION;
- MARKET_DISLOCATION.

Ulazi po timeframeu:
- 1h za širi kontekst, 15m za glavni trend, 5m za setup (VWAP z-score), 1m za detekciju šoka.
- ADX/DI, nagib EMA-e, ATR i percentil realizirane volatilnosti, širenje volumena, struktura iz potvrđenih
  pivota, smjer i korelacija BTC-a.
- Funding i OI koriste se samo uživo kad su dostupni; njihov izostanak nikad nije potvrda.
- Čitaju se samo zatvorene svijeće višeg TF-a.

Testovi: `test_regime2.py`. Research Engine 2.0 raščlanjuje svaki OOS rezultat po ovim režimima, a Selection Engine
bira strategiju po (instrument × režim).

## 2. v2 strategije (`strategies/v2.py`), verzija 2.0.0

| v2 | vrsta | vrata (sve kauzalno) | grid (jedini parametri koji se biraju) |
|---|---|---|---|
| bot1_vwap_v2 | mean-reversion | 15m ADX < prag, ne protiv 1h trenda, cost gate | cost k 4/6/8 × ADX 20/25 |
| bot2_ema_v2 | trend | samo u smjeru 1h trenda, cost gate | cost k 4/6/8 |
| bot3_orderflow_v2 | trend | 1h trend, cost gate; **CVD-only u backtestu**; uživo bez order booka nema ulaza | cost k 4/6/8 |
| bot4_breakout_v2 | trend | 1h trend, cost gate | cost k 4/6/8 |
| bot5_squeeze_v2 | trend | 1h trend, cost gate | cost k 4/6/8 |
| bot6_rsidiv_v2 | mean-reversion | 15m ADX < prag, ne protiv 1h trenda, cost gate | cost k × ADX |
| bot7_liquidity_v2 | mean-reversion | 15m ADX < prag, ne protiv 1h trenda, cost gate | cost k × ADX |
| bot8_supertrend_v2 | trend | 1h trend, cost gate | cost k 4/6/8 |
| bot9_rangeexp_v2 | trend | 1h trend, cost gate | cost k 4/6/8 |

- **Cost gate:** stop koji strategija sama predloži mora biti ≥ k × round-trip trošak (0,14 % u BASE scenariju).
  - Forenzika pokazuje da trošak na 1–5m iznosi 0,15–0,17 R po trgovini.
  - Ovim se ne ulazi kad su naknade velik dio rizika.
- **Izvršni timeframe:** 15m i 60m (oba se testiraju). Trošak u R pada s timeframeom, a na 1–5m nijedna
  strategija nema bruto prednost veću od troška.
- **Bez jednostranog biasa:** SHORT je bio bolji od LONG u 23/27 slučajeva, ali to je svojstvo razdoblja,
  pa nije ugrađeno.
- **Bez isključivanja simbola** na temelju uzorka.
- **bot9:** i dalje NIJE "Moneyline Breakout Entry v3.5". Bez originalne specifikacije nije implementiran
  (`/api/strategies/moneyline` vraća `implemented: false`).
- **bot10 (adaptive)** ostaje v1 meta-strategija. Njegovu ulogu u 2.0 preuzima deterministički Strategy
  Selection Engine (ispod).

## 3. Istraživački prototipovi A–D (`strategies/research_protos.py`), samo za istraživanje

| | logika | TF |
|---|---|---|
| A — Trend Pullback Continuation | 1h i 15m trend se slažu; 5m pullback u EMA21 pojas uz RSI reset; ulaz kad se vrati iznad/ispod EMA9 uz volumen | 5m + 15m + 1h |
| B — Liquidity Sweep Reversal | 5m svijeća probije potvrđeni 15m swing i zatvori se natrag unutra uz volume spike; samo kad je 15m ADX nizak | 5m + 15m |
| C — Volatility Breakout Retest | 15m Bollinger širina u najnižem percentilu, 15m Donchian proboj, zatim 5m retest razine koji drži | 5m + 15m |
| D — Cost-Aware Momentum | 15m proboj N-bar high/low s ATR-normaliziranim momentumom, u smjeru 1h trenda; samo kad je ATR ≫ trošak | 15m + 1h |

Ne smiju se koristiti za stvarno trgovanje. Mogu postati kandidati za paper trading samo kroz Research 2.0
i Activation 2.0.

## 4. Strategy Selection Engine (`selection.py`)

Bira po (instrument × režim):
- uzorak mora biti ≥ 30;
- donja granica 95 % CI ponderiranog očekivanja mora biti > 0;
- Bayesov shrinkage prema 0 R (k = 50);
- raspad s poluživotom 60 dana;
- penali za drawdown, turnover i korelaciju dnevnog R.

Ako uvjeti nisu ispunjeni, ishod je **NO TRADE**. AI ne sudjeluje. Testovi: `test_selection.py`.

## 5. AI (faza 10)

- `ADVISORY_ONLY = True`: ideje Independent Tradera se bilježe (`advisory_recorded`) i uspoređuju, ali se ne šalju.
- Ova vrijednost nije postavka koju API može promijeniti.
- Supervisor može samo dodatno ograničiti.
- LLM nema pristup Risk Engineu, aktivacijskim kriterijima ni API ključevima.

## 6. Rezultati

Rezultati v2 strategija i prototipova dolaze isključivo iz Research Engine 2.0 na CI-ju. Opisani su u
`BACKTEST_REPORT.md`, `WALK_FORWARD_REPORT.md`, `COST_ANALYSIS.md`, `STRATEGY_COMPARISON.md` i
`PROFITABILITY_REPORT.md`. Ovaj dokument ne tvrdi da su izmjene profitabilne.
