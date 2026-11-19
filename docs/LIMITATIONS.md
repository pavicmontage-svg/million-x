# Preostala ograničenja (v1.1)

Popis onoga što **nije** dokazano ili riješeno. Ništa od navedenog nije sakriveno u kodu.

## Tržište i burza
1. **Binance i EU (MiCA).** Od 1. 7. 2026. Binance za EEA rezidente ne prima nove naloge. Live preko Binancea iz Hrvatske najvjerojatnije nije moguć; Demo Trading služi za testiranje. Adapter je zaseban modul, a licencirana EU burza (npr. OKX EU, Kraken) zahtijeva novi adapter.
2. **Adapter nije testiran protiv prave Binance mreže.** Radno okruženje u kojem je kod napisan nema pristup Binanceu. Sve provjere izvršenja vrte se na simuliranoj burzi s ubrizganim greškama. Endpointi (`/fapi/v1/algoOrder`, `openAlgoOrders`, `positionRisk`, `income`) slijede službenu dokumentaciju, ali ih prvi put stvarno potvrđuje *demo preflight* na tvom serveru.
3. **Tržišni podaci** dolaze REST pollingom zatvorenih 1m svijeća (svakih 5 s), a ne WebSocketom. Kašnjenje ulaza je nekoliko sekundi i ograničeno je provjerom pomaka cijene (`max_price_drift_bps`).
4. **Algo nalozi.** Ako burza dopušta samo jedan closePosition stop po strani, zamjena stopa radi kao „otkaži pa postavi“, uz provjeru i vraćanje prethodne razine. U tom kratkom razmaku pozicija nema stop na burzi.
5. **Usklađivanje u „hedge“ načinu rada** nije podržano: sustav pretpostavlja one-way način (zadani na Binanceu).

## Strategije i dokazi
6. **Nijedan bot nema dokaz profitabilnosti.** Walk-forward i Monte Carlo pokreću se na serveru nad stvarnim podacima; ovdje nisu pokrenuti jer podaci nisu dostupni.
7. **Bot 9** je zamjena (Range Expansion Breakout), a ne Moneyline Breakout Entry v3.5. Original se implementira tek kad stigne izvorna matematička specifikacija.
8. **Order Flow bot** u backtestu koristi samo taker volumen; povijesni order book nije u Binanceovoj javnoj arhivi.
9. **Funding** se u backtestu procjenjuje konstantnom stopom; u liveu se čita iz stvarnih prihoda (`income`).
10. **4 EUR uz 0,5 % rizika** tehnički nije izvedivo (`docs/FEASIBILITY_4EUR.md`).

## AI organizacija
11. **LLM put nije testiran sa stvarnim modelom** (nema API ključa u ovom okruženju). Testiran je s lažnim LLM-om koji vraća JSON. Bez `ANTHROPIC_API_KEY` sve radi deterministički i to je vidljivo u aplikaciji.
12. **Cijene tokena** nisu ugrađene (`MX_LLM_PRICES`); bez njih se bilježe samo tokeni.
13. **Vijesti i likvidacijski klasteri** nemaju izvor podataka; Market Intelligence to izričito navodi.

## Android
14. **Produkcijski potpisan APK još ne postoji.** Za njega treba tvoj ključ za potpis (GitHub secretsi `MX_KEYSTORE_*`). Dostupan je samo **debug** APK, jasno označen da nije za produkciju.
15. **UI testovi** (4 instrumentirana testa) pokrivaju sigurnosno kritične komponente: emergency stop, globalnu traku kad je server nedostupan, uparivanje i stanje greške. Ne pokrivaju svaki ekran.
16. **Nije testirano na fizičkom S25 Ultra**, samo na emulatoru s Androidom 14 (API 34).
17. **Push obavijesti** rade tek kad se postavi Firebase (`google-services.json` + service account na serveru).

## Server
18. **SQLite** u jednom procesu je dovoljan za jednog korisnika, ali ne podržava rad s više servera (bez HA).
19. **Rate limiter** je u memoriji procesa, pa se nakon restarta resetira; throttling uparivanja je u bazi i perzistentan.
20. **Backupi** ostaju na istom disku (`data/backups`). Za otpornost na gubitak servera kopiraj ih izvan njega.
