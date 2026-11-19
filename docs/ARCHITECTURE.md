# Arhitektura MILLION-X AI

```
 Android (Kotlin/Compose, MVVM)            Server (Docker, 24/7)
 ┌───────────────────────────┐  HTTPS   ┌─────────────────────────────────────────────┐
 │ 10+ modula, biometrija,   │◄────────►│ Caddy (TLS) ─► Starlette API + WebSocket    │
 │ Keystore token, FCM       │   WSS    │   │                                         │
 └───────────────────────────┘          │   ├─ Runtime ─ TradingEngine (thread)       │
          ▲ push (FCM)                   │   │      ├─ 10 botova (strategies/*)        │
          └──────────────────────────────│   │      ├─ Supervisor (strategy intel.)    │
                                         │   │      ├─ RiskManager (CRO, tvrdi limiti) │
                                         │   │      ├─ CompetitionEngine (shadow)      │
                                         │   │      └─ Exchange adapter                │
                                         │   │           ├─ PaperExchange              │
                                         │   │           └─ BinanceFutures demo/live   │
                                         │   ├─ Organization: 6 mozgova + 5 izvršnih   │
                                         │   │      (Claude API, worker thread)        │
                                         │   ├─ Jobs: backtest / walk-forward / MC     │
                                         │   └─ SQLite (WAL): trades, audit (hash lanac)│
                                         └─────────────────────────────────────────────┘
```

## Tok jedne odluke (10 koraka, svaki se zapisuje u audit)

1. **Market Intelligence:** režim (BULL/BEAR/SIDEWAYS × HIGH/NORMAL/LOW vol) iz 15m svijeća.
2. **Strategy Intelligence (Supervisor):** skuplja signale svih botova, boduje ih (pouzdanost × ponder × uspješnost u režimu), uklanja suprotne i redundantne signale te umanjuje utjecaj koreliranih botova.
3. **Quant Research:** je li bot u statusu koji smije trgovati u trenutnom načinu rada.
4. **Capital Intelligence (RiskManager.size):** rizik od 0,5 % računa se sa stopom **i** troškovima; uključuje dinamički leverage cap (cilj volatilnosti, nikad maksimum burze), provjeru udaljenosti likvidacije, izloženost, minimalnu vrijednost naloga, udio naknada u riziku i neto R:R.
5. **Execution Intelligence:** provjera spreada i dubine; market nalog ako je unutar limita.
6. **CRO:** deterministički veto (iskorištenost margine, abnormalna aktivnost, withdrawal dozvola) i provjera je li live otključan.
7. **Execution:** nalog s determinističkim `clientOrderId` (zaštita od duplikata u dnevniku naloga).
8. **Burza potvrđuje:** status se provjerava upitom; `UNKNOWN` se nikad ne ponavlja naslijepo. Odmah nakon popunjavanja postavlja se **STOP_MARKET closePosition** (Algo Order API). Ako se stop ne može postaviti, pozicija se odmah zatvara.
9. Događaj ide svim „odjelima“ (WebSocket, audit, obavijesti).
10. Statistika se ažurira (competition, health check bota).

## 10 botova

Svaki je zaseban modul s vlastitom logikom (test `test_ten_distinct_modules`):

| # | Modul | Ideja | TF |
|---|---|---|---|
| 1 | `vwap_scalper.py` | povratak na session VWAP nakon izlaska iz pojasa + volumen + zatvaranje natrag u pojas | 3m |
| 2 | `ema_momentum.py` | EMA 9/21/50, potvrda trenda na 15m, pullback na EMA21, ponovni prelazak EMA9 | 5m |
| 3 | `order_flow.py` | neravnoteža taker volumena (CVD) + probijanje; live još i neravnoteža order booka | 1m |
| 4 | `breakout_hunter.py` | opening range (UTC) + Donchian, potvrda volumenom, ulaz na retestu | 5m |
| 5 | `bollinger_squeeze.py` | BB unutar Keltnera N svijeća, ulaz na potvrđenom proboju | 5m |
| 6 | `rsi_divergence.py` | divergencija na potvrđenim pivotima + proboj strukture | 3m |
| 7 | `liquidity_hunter.py` | sweep swing razine ili high/low prethodnog dana + povratak + volumen | 3m |
| 8 | `supertrend_momentum.py` | promjena smjera Supertrenda + 15m Supertrend + EMA50 + ADX | 5m |
| 9 | `range_expansion.py` | **zamjena za Moneyline** (NR7 kompresija → ekspanzija) | 5m |
| 10 | `adaptive.py` | prepoznaje režim i prosljeđuje signal najboljeg *validiranog* bota za taj režim; inače miruje | — |

Svi indikatori su kauzalni, a test `test_no_lookahead` provjerava da se signal ne mijenja kad se dodaju buduće svijeće.

## Izlazi (jednaki u backtestu, paperu i liveu)

SL → djelomični TP (50 %) na TP1 → break-even (ulaz ± 2× taker naknada) → ATR trailing → konačni TP2 → vremenski stop (180 min). U backtestu se, ako su unutar iste svijeće pogođeni i stop i cilj, uzima da je stop pogođen prvi; ako cijena otvori iza stopa, izlaz je po cijeni otvaranja.

## Strategy Competition Engine

Svaki signal svakog bota otvara **virtualnu** poziciju (fiksni rizik od 0,5 % na knjigu od 100). Dnevni ciklus:
1. predlaže pondere (ocjena prilagođena riziku, skupljena prema 1,0 kad je uzorak malen; raspon 0,25–2,0),
2. simulira zadnjih 14 dana sa starim i s novim ponderima,
3. nove pondere primjenjuje samo ako simulacija nije lošija.

Ponderi **nikad ne podižu** rizik iznad 0,5 %, nego ga samo smanjuju. Statistika po režimu (`RegimeStats`) hrani Bota 10 i ekran Strategy Center.

## Životni ciklus bota

`RESEARCH → BACKTEST_PASSED → PAPER → LIVE_ELIGIBLE`; uz to postoje `SUSPENDED` (automatski, shadow trgovanje se nastavlja i bot se može oporaviti u PAPER) i `DISABLED` (korisnik).

Kriteriji aktivacije (`config.yaml → activation`): najmanje 100 OOS trgovina, PF ≥ 1,15, očekivanje ≥ 0,05R, max DD ≤ 20 %, Monte Carlo DD p95 ≤ 30 %, najmanje 60 % pozitivnih walk-forward prozora, zatim najmanje 50 paper trgovina s PF ≥ 1,05.

## AI organizacija

- 11 agenata, svaki s vlastitim, verzioniranim system promptom (iz tvoje specifikacije), modelom, logovima, pouzdanošću, tokenima i troškom te greškama.
- Svaki agent dobiva **evidence packet** (stvarni podaci s ID-jem). Nalaz koji citira nepostojeći ID označava se kao *untraceable* i nikad se ne primjenjuje.
- Automatski se primjenjuje samo **pooštravanje** iz `brain_risk` i `exec_cro`: smanjenje rizika, suspenzija bota ili suspenzija svega. Sve ostalo čeka vlasnika.
- Board meeting: jednom dnevno (ili ručno); neslaganja (stav rizik/aktivnost) se bilježe i prikazuju.
- LLM pozivi rade u zasebnom threadu; trading petlja ih nikad ne čeka.

## Sigurnost

- Binance ključevi su šifrirani Fernetom; master ključ je samo u env varijabli `MX_MASTER_KEY`.
- Ključevi s withdrawal dozvolom se odbijaju (provjera `apiRestrictions`); upozorenje se šalje ako ključ nije ograničen na IP.
- Android: token je u AES-GCM šifri s ključem u Android Keystoreu, uz biometrijsko zaključavanje i ponovnu potvrdu za osjetljive radnje.
- RBAC: `owner` (sve) i `viewer` (čitanje + emergency stop). AI agenti nemaju token.
- `trades` i `audit` su append-only (SQLite triggeri), a audit je ulančan SHA-256 hashevima (`/api/audit/verify`).
- Ustajali podaci ili prekid veze blokiraju nove ulaze; pozicije štite stopovi na strani burze.
- Emergency stop: globalan, perzistentan, zatvara pozicije i ostaje aktivan i nakon restarta.
