# PROFITABILITY REPORT — MillionX 2.0

*Automatski generirano iz Research Engine 2.0 (`research2 run 44829762193 (22/23 strategija; bot10_adaptive još radi)`) — selekcijsko razdoblje 2025-01-01 – 2026-06-30, 10 instrumenata, završni holdout nije korišten. Brojevi nisu ručno upisani.*

## Zaključak (interpretacija)

**MillionX 2.0 nema strategiju s dokazanom prednošću.** Nijedna od 40 testiranih kombinacija nije prošla Activation 2.0.
Strategy Selection Engine zato za svih 100 kombinacija instrumenta i režima vraća **NO TRADE**. To je ispravan ishod,
a ne kvar: sustav ne trguje ako nema dokaza da je trgovanje bolje od netrgovanja.

Dvije kombinacije imaju pozitivno OOS očekivanje. Obje su statistički beznačajne:
- **bot6_rsidiv_v2 60m:** +0,169 R, ali na samo **8** trgovina. DSR se ne može izračunati, a premalo je trgovina za bilo kakav zaključak.
- **bot7_liquidity_v2 60m:** +0,027 R na 90 trgovina, PF 1,07. Pada u STRESSED troškovima (−0,032 R).
  DSR je 0,004 (traži se ≥ 0,95), a PBO 0,90, što znači vrlo vjerojatno slučajnost ili pretreniranost.

**Pragovi nisu snižavani** da bi ijedna strategija prošla. Rizik po tradeu i leverage nisu mijenjani.
Ništa nije aktivirano za paper ni za live.

**Preporuka:**
- Ne trgovati stvarnim novcem.
- v2 strategije ostaju *research-only* i ne trguju ni na paperu.
  Najmanji gubitak uz dovoljan broj trgovina imaju na 60m bot9_rangeexp_v2 (−0,040 R, n = 429), bot5_squeeze_v2 (−0,081 R, n = 449)
  i bot4_breakout_v2 (−0,102 R, n = 1304). To je i dalje gubitak.
- Sljedeći korak istraživanja su drukčiji izvori informacija kojih u klinesima nema:
  - stvarni funding po simbolu, open interest, likvidacije;
  - snimljeni order book (uživo, kroz demo);
  - cross-sectional signali među 10 instrumenata.
  Daljnje "ugađanje" postojećih indikatora nije smisleno.

*bot10_adaptive (meta-strategija nad v1 botovima) u trenutku pisanja još se računa na CI-ju. Svi njegovi članovi su negativni,
pa njegov rezultat ne može promijeniti zaključak. Bit će dodan kad run završi.*

- Testirano kombinacija (strategija × TF): **40**
- Pozitivan OOS exp R (BASE troškovi): **2** — bot6_rsidiv_v2 60m, bot7_liquidity_v2 60m
- Prošlo Activation 2.0 (kandidat za paper): **0** — nijedna
- Selection Engine: 100 od 100 ćelija = NO TRADE

## Koje provjere padaju (broj strategija)

- net_ev_after_costs_ci: 40 / 40
- deflated_sharpe: 40 / 40
- profit_factor: 39 / 40
- expectancy_r: 39 / 40
- positive_windows: 39 / 40
- cross_asset: 39 / 40
- stressed_costs: 39 / 40
- multi_regime: 37 / 40
- parameter_stability: 36 / 40
- mc_dd_p95: 34 / 40
- max_drawdown: 31 / 40
- pbo: 7 / 40
- oos_trades: 5 / 40
