# EXECUTION & LATENCY REPORT — MillionX 2.2

Sve vrijednosti su izmjerene. Što nije moguće izmjeriti bez stvarnih naloga, izričito je navedeno.

## 1. Latencija podataka (burza → aplikacija)

Izmjereno na CI runneru (GitHub, SAD), collector run `44844375356`, 3 min, BTC + ETH:

| stream | mjera | vrijednost |
|---|---|---|
| depth20@500ms (/public) | lokalni prijem − event time | medijan **79 ms**, p95 80 ms, max 81 ms (358 snimki) |
| forceOrder (/market) | lokalni prijem − event time | 106–1086 ms (Binance šalje likvidacije u 1-sekundnim snimkama) |

Uživo skener prikazuje medijan latencije trade → aplikacija po instrumentu (ekran Scalping → "Exchange → app latency").
Iz Hrvatske se očekuje drugačija vrijednost, koju treba očitati u aplikaciji.

## 2. Vrijeme odluke skenera

Mjereno lokalno (Python 3.12, x86-64), 10 instrumenata, pun 4-satni međuspremnik:

| verzija | po sekundi (10 instrumenata) |
|---|---|
| prva (puni izračun značajki svake sekunde) | medijan ≈ 199 ms, max 240 ms |
| s egzaktnim predfiltrom | medijan **0,1 ms**, max ≈ 184 ms (sekunda osvježenja snimke; osvježenja su raspoređena po instrumentima) |

**Predfiltar** je nužan uvjet oba enginea: A traži proboj 3-minutnog raspona, a B proboj 60-sekundnog raspona.
Kad cijena nije izvan raspona zadnjih 60 s, nijedan engine ne može okinuti. Puni izračun se tada preskače, a odluke
su identične. Na mobitelu (ARM) vrijeme treba očitati u aplikaciji ("Decision time"). Za rad 24/7 preporuka je VPS.

## 3. Izvršenje u 2.2

- **Nema stvarnih naloga.** Live je zaključan, a skener izvršava samo virtualni PaperBook. Latencija postavljanja
  naloga na Binance zato nije izmjerena, i ne smije se pretpostaviti.
- PaperBook ulazi po **prvoj cijeni sljedeće sekunde** nakon odluke, što je minimalna latencija od 1 s.
  Backtest koristi 1 s (BASE), 3 s (STRESSED) i 5 s (EXTREME).
- **Maker vs taker:**
  - zadano je taker na ulazu i izlazu;
  - maker naknada (2 bps) se u backtestu računa samo za simulirani limit nalog koji je **potvrđen**: kasnije
    trgovine moraju proći kroz limit, dodir nije fill, a ispunjava se najviše 10 % prometa koji je prošao kroz cijenu;
  - inače vrijedi taker.
- **Djelomični fill:** maker simulacija bilježi ispunjeni udio. Taker nalozi s rizikom od 2,50 EUR su daleko ispod
  dubine top-20 knjige (impact za 50 000 USDT bilježi se u `book_1s.impact_*_bps`).
- **Duplikati / rekoncilijacija / zaštitni nalozi:** postojeći `ExecutionManager` (idempotentni clientOrderId, stop koji
  potvrđuje Binance, rekoncilijacija nakon restarta) ostaje jedini put do burze. Skener ga u 2.2 ne poziva.
- **Ispadi burze:** backtest simulira 2–5-minutne ispade (≈ 1 u 3 dana). Ulaz nije moguć, a izlaz se odgađa do
  povratka i gubitak se bilježi (izlazni razlog `*_after_outage`).
