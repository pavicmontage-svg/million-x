# PAPER TRADING REPORT — MillionX 2.2

## Stanje

- **Paper izvršenja: 0.** Paper (virtualni PaperBook na živim podacima) izvršava samo prilike koje Opportunity Engine prihvati
  **i** čija je strategija validirana. Nijedna nije prošla validaciju (A, B, A+B: NO_TRADE; C: nema uzorka), pa nema paper trgovina.
  To je očekivani i ispravni ishod, a ne kvar.
- **Skener ipak radi i mjeri.** Uz uključeno snimanje (More → Research Data → Record ili VPS `docker compose up -d collector`)
  svake sekunde procjenjuje 10 instrumenata i za svaku priliku sprema:
  - snimku značajki u trenutku odluke (rekonstrukcija "što je bot vidio");
  - odluku i razloge;
  - **shadow ishod**: što bi se dogodilo prema istim pravilima izlaza.
  Iz toga se uživo mjere detected / rejected / executed / missed, neto po trgovini, neto po satu i prosječno trajanje (ekran Scalping).
- **Execution simulation:**
  - PaperBook ulazi po prvoj cijeni sljedeće sekunde, uz BASE troškove;
  - ISOLATED leverage se smanjuje dok likvidacija nije ≥ 3× stop;
  - margin mora stati u 500 EUR, najviše 2 pozicije, dnevni limit 10 EUR.
  Testirano u `test_scalp_live.py`.

## Uvjeti za početak paper izvršenja

Strategija mora na validaciji imati sve od navedenog:
- n ≥ 100, PF ≥ 1,15, neto ≥ 0,05 R;
- donja granica CI > 0;
- STRESSED > 0;
- pozitivno u ≥ 2 od 3 walk-forward kvartala.

Za live je potrebno još ≥ 30 dana papera i odluka vlasnika (Activation 2.0, nepromijenjeno).
