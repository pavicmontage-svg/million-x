# LIQUIDATION RESEARCH REPORT — MillionX 2.1

*Automatski generirano iz CI izlaza (`micro-research run 44841732631`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 (samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. Brojevi nisu ručno upisani.*

**Status: INCOMPLETE.**

no historical liquidation data for 2025-2026 on Binance (liquidationSnapshot archive ends 2024-03); not estimated.

- Istraživanje C (Liquidations + Order Book Imbalance) nije provedeno; likvidacijski dio E također.
- Likvidacije se NE procjenjuju iz cijene, volumena ni OI-ja (to bi bili izmišljeni podaci).
- Od 2.1 MillionX snima likvidacije uživo (`micro/collector.py`, `!forceOrder` stream); Binance objavljuje najviše 1 likvidaciju po simbolu u sekundi, pa je snimljeni volumen **donja granica**.
- Klasifikacija (long/short squeeze, kaskada, sweep, post-liquidation reversal) definirat će se i testirati tek kad snimljeni uzorak bude dovoljno velik (cilj: ≥ 3 mjeseca, ≥ 100 događaja po klasi), uz isti protokol (discovery/validacija, FDR).
