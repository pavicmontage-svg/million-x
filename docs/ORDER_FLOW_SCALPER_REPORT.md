# ORDER FLOW SCALPER REPORT — MillionX 2.2 (Engine C)

## Logika

Engine C (`scalp/engines.py::engine_c`) koristi **samo stvarne podatke knjige i toka naloga**. Svaka ulazna vrijednost
dolazi iz live snimanja (collector), nikad iz OHLCV-a.

- **Ulaz:**
  - top-20 knjiga (500 ms): imbalance top-10, uklanjanje likvidnosti na bid/ask strani, spread, starost knjige;
  - agresivni buy/sell volumen zadnjih 10 s i pomak cijene u tih 10 s.
- **LONG:** knjiga teška na bid strani (imb ≥ 0,35), agresivna kupnja (delta ≥ 0,30) i uklanjanje likvidnosti na ask strani.
  SHORT je zrcalno.
- **Bez ulaza:**
  - spread > 2 bps;
  - **apsorpcija**: jaka agresija (|delta| > 0,6) bez pomaka cijene, što znači da pasivna strana upija tok.
- **INCOMPLETE** (nema ulaza): knjiga starija od 2 s ili je nema, odnosno nema trgovina u zadnjih 10 s.
- Stop je 1,5 × 1-minutna σ (najmanje 5 bps), cilj 3R, s istim izlazima kao A/B.

## Zašto nema povijesnog backtesta

- Binance ne objavljuje povijesni order book u sekundnoj rezoluciji.
  - `bookDepth` ima kumulativnu dubinu ±0,2–5 % svakih 30 s.
  - bookTicker postoji samo do 2024-03.
- Backtest C-a iz OHLCV-a bio bi lažni order flow, što specifikacija zabranjuje.
- C se zato vrednuje **samo na snimljenim podacima**:
  - skener ga pokreće svake sekunde dok radi snimanje;
  - svaka odluka i njezin "shadow" ishod (što bi se dogodilo) spremaju se u `scalp_opps`.

## Status

- **Presuda: NO_TRADE / RESEARCH.** C nije validiran jer nema dovoljno snimljenog uzorka, pa ništa ne izvršava.
- Za istraživački test treba ≥ 3 mjeseca snimanja (VPS 24/7) i ≥ 100 shadow ishoda po smjeru.
  Uz isti protokol kao A/B (kalibracija → validacija → troškovni stres) i uz troškove od ≈ 13 bps.
- Iskustvo iz 2.1 (CVD apsorpcija: bruto ≈ +5 bps na 60 min) sugerira da je C bez maker izvršenja malo vjerojatno isplativ.
