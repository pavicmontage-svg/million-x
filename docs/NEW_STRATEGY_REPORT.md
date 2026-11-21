# NEW STRATEGY REPORT — MillionX 2.1

## Odluka: nijedna nova strategija nije razvijena ni aktivirana

Specifikacija 2.1 (§10) traži da se nove strategije razvijaju **tek nakon što istraživanje pokaže statističku prednost**.
Istraživanje A–F (`MICROSTRUCTURE_RESEARCH_REPORT.md`, CI run `44841732631`) nije pokazalo prednost:

| prioritetna strategija iz specifikacije | podloga u istraživanju | najbolji bruto rezultat (validacija) | neto (BASE 14 bps) | odluka |
|---|---|---|---|---|
| 1. Order Flow Absorption Scalper | D1/D2 (CVD apsorpcija + VWAP) | D2 fade 60 min: +5,1 bps (n = 984) | −8,9 bps | **ne razvija se** |
| 2. Open Interest Momentum | B1–B4, A | B3 fade 15 min: +0,4 bps | −13,6 bps | **ne razvija se** |
| 3. Liquidation Cascade Reversal | C | — (nema povijesnih likvidacija) | — | **INCOMPLETE**, čeka vlastito snimanje |
| 4. Funding Extreme Reversal | A1–A4, E | ≤ +1,1 bps; smjer nestabilan | ≤ −12,9 bps | **ne razvija se** |
| 5. CVD Momentum Continuation | B5/B6, D3/D4 | B5 fade 60 min: +2,6 bps (i to je *fade*, ne continuation) | −11,4 bps | **ne razvija se** |

Pravilo iz §14 i Activation kriteriji v2.0 ostaju nepromijenjeni. Nijedan modul nije dodan u aktivni ni u paper trading.
Za sve instrumente i režime vrijedi **NO TRADE**.

## Što treba da se ovo promijeni (plan, bez tvrdnji o profitabilnosti)

1. **Snimanje:** collector radi 24/7, po mogućnosti na VPS-u (`docker compose up -d collector`), najmanje 3 mjeseca.
   Snima knjigu do 20 razina, svaku agregiranu trgovinu, likvidacije (donja granica) i funding/OI.
2. **Novi, unaprijed registriran test** na snimljenim podacima:
   - D2 (apsorpcija) i F1 (bid-heavy fade) uz **maker izvršenje** (≈ 4 bps round-trip);
   - model reda čekanja i adverse selectiona iz snimljene knjige;
   - isti protokol: discovery/validacija, FDR, STRESSED troškovi, ≥ 6/10 instrumenata.
3. Tek ako prođe, modul se piše kao zasebna strategija (entry, exit, SL, TP, R/R, režimski filtar, CI, backtest report)
   i ide kroz Activation 2.0: Research presuda, ≥ 30 dana paper trgovanja, odluka vlasnika.
