# Je li moguće trgovati s 4 EUR?

Brojke su izračunate kodom (`python -m millionx.cli feasibility`) uz: 4 EUR ≈ 4,32 USDT (tečaj iz konfiguracije 1,08), rizik 0,5 %, taker naknadu 0,05 % i slippage 2 bps po strani. Stvarne naknade i minimumi čitaju se s burze (`/fapi/v1/commissionRate`, `exchangeInfo`) i mogu se mijenjati.

| Pretpostavka | Rizik po trgovini | Najveći nalog koji 0,5 % rizika dopušta (stop 0,4 %) | Minimum burze | Izvedivo? |
|---|---|---|---|---|
| 4 EUR, simbol s minimumom 5 USDT | 0,0216 USDT | 4,0 USDT | 5 USDT | **Ne** |
| 4 EUR, ETHUSDT (minimum 20 USDT) | 0,0216 USDT | 4,0 USDT | 20 USDT | **Ne**: naknade su 130 % rizika |
| 4 EUR, BTCUSDT (minimum 100 USDT) | 0,0216 USDT | 4,0 USDT | 100 USDT | **Ne**: naknade su 648 % rizika |
| 40 EUR, simbol s minimumom 5 USDT | 0,216 USDT | 40 USDT | 5 USDT | Da (naknade ≈ 3 % rizika) |

S 4 EUR trgovina na simbolu s minimumom od 5 USDT moguća je samo uz stop ≤ 0,29 %, a tada naknade pojedu oko 32 % rizika svake trgovine.

**Matematika cilja:** uz rizik od 0,5 % i dobitak od 1R, jedno udvostručenje traži ln 2 / ln 1,005 ≈ **139 dobitnih trgovina zaredom bez ijednog gubitka**. Za 18 udvostručenja to je ≈ 2.502 dobitne zaredom, i to prije naknada.

## Tehnički ispravna rješenja

1. **EXPERIMENTAL MODE (4 EUR) koristiti za testiranje mehanike** (minimumi, naknade, zaokruživanje, stopovi) u paperu ili demu, a ne kao put prema cilju. Engine nalog odbija s točnim razlogom (`below_exchange_minimum`, `fees … % of risk`).
2. **Stvarni kapital od ≥ 40 EUR** (MULTI-BOT MODE), gdje je rizik od 0,5 % tehnički izvediv na likvidnim simbolima s minimumom od 5 USDT.
3. **Veći rizik po trgovini** (npr. 1–2 %) odluka je vlasnika i mijenja se u *Risk Center*. Server odbija vrijednosti iznad 2 %. To povećava vjerojatnost propasti računa; Monte Carlo na stvarnim rezultatima prikazuje tu vjerojatnost na Dashboardu.

Aplikacija vjerojatnost postizanja cilja prikazuje tek kad postoji najmanje 20 stvarnih zatvorenih trgovina, i to Monte Carlo simulacijom iz tih rezultata. Prije toga piše „nije procjenjivo“.
