# RISK VALIDATION REPORT — MillionX 2.0

## 1. Risk parametri (nepromijenjeni u odnosu na 1.9.0)

| parametar | vrijednost | gdje |
|---|---|---|
| kapital (pro način) | 500 EUR | `Settings.pro_capital_eur` |
| rizik po trgovini | 0,5 % (2,50 EUR) | `RiskLimits.risk_per_trade` |
| max dnevni gubitak | 2 % i apsolutno 10 EUR | `max_daily_loss`, `max_daily_loss_eur` |
| max otvorenih pozicija | 2 | `max_open_positions` |
| leverage | max 20×, razine 5/10/15/20 po kvaliteti | `max_leverage`, `leverage_tiers` |
| margin | ISOLATED | ExecutionManager |
| likvidacijski buffer | ≥ 3 × udaljenost stopa | `min_liq_buffer` |

U 2.0 nijedna strategija ni AI ne mogu povećati rizik po tradeu ni leverage:
- `governance.BOUNDS` za live i `SIM_BOUNDS` za simulaciju vrijede kao i prije.
- AI desk je u 2.0 samo savjetodavan (`ADVISORY_ONLY = True`, nije postavka vlasnika).
- Supervisor može leverage samo spustiti; konačnu veličinu računa Risk Engine.

## 2. Leverage 5/10/15/20× (faza 9)

Usporedba je napravljena na CI-ju (grana `research-leverage`, `leverage_tf60.md`): 1.9.0 sustav, 60m,
540 USDT, 0,5 % rizika, razdoblje 2025-07-01 – 2026-10-31.

| leverage | trgovine | neto (USDT) | PF | exp R | max DD | likvidacije |
|---|---|---|---|---|---|---|
| 5× | 324 | −85,93 | 0,66 | −0,191 | 17,0 % | 0 |
| 10× | 324 | −85,93 | 0,66 | −0,191 | 17,0 % | 0 |
| 15× | 324 | −85,93 | 0,66 | −0,191 | 17,0 % | 0 |
| 20× | 324 | −85,93 | 0,66 | −0,191 | 17,0 % | 0 |

**Zaključak:** uz dimenzioniranje po riziku, leverage mijenja samo potrebni margin, a ne veličinu pozicije,
pa ni P/L. Rezultat je identičan na svim razinama, a likvidacija nije bilo jer buffer od 3× stopa spušta
leverage kod širokih stopova. Veći leverage ne može pretvoriti negativnu strategiju u pozitivnu. Može samo
povećati rizik likvidacije ako se zaobiđe dimenzioniranje po riziku, a to sustav ne dopušta.

*Napomena o holdoutu:* ova usporedba (iz verzije 1.9.0) obuhvaća i razdoblje kasnijeg 2.0 holdouta
(2026-07 – 2026-10). Nije korištena za izbor ičega u 2.0, a v2 strategije i prototipovi na tom razdoblju
nisu pokretani. Ipak je zabilježeno: rezultati 1.9.0 sustava na tom razdoblju su viđeni.

## 3. Testovi koji potvrđuju risk pravila (stvarno pokrenuti)

Lokalno: `pytest` → **140 passed** (backend, Python 3.12). Ključni testovi:

- `test_risk_and_exits.py`:
  - rizik 0,5 % uključujući troškove;
  - leverage ograničen volatilnošću, ne maksimumom burze;
  - dnevni gubitak i drawdown zaustavljaju trgovanje;
  - cooldown nakon niza gubitaka;
  - "tighten-only";
  - stop ima prednost kad bar dotakne i stop i cilj;
  - gap fill;
  - djelomični TP → break-even → trailing;
  - time-stop;
  - 500 EUR sizing s razinama leveragea;
  - likvidacijski buffer;
  - apsolutni dnevni limit 10 EUR.
- `test_execution_safety.py`:
  - verificirani stop nakon filla;
  - djelomični fill;
  - nepoznati status naloga se nikad ne šalje ponovno;
  - odbijeni ili neprovjerljivi stop zatvara poziciju;
  - rekoncilijacija nakon restarta;
  - emergency stop;
  - istek ručnog odobrenja;
  - provjera pomaka cijene;
  - frekvencija naloga;
  - live je blokiran bez gatea;
  - stvarni saldo se provjerava prije svakog naloga.
- `test_governance_gate.py`:
  - granice svih limita;
  - konfiguracijska datoteka ne može zaobići granice;
  - live unlock zahtijeva demo preflight i frazu, istječe i poništava se promjenom konfiguracije ili ključa.
- `test_ai_desk.py`:
  - AI trader je samo savjetodavan i ne može se prebaciti na izvršavanje;
  - supervisor ne može odobriti bez razloga;
  - kad je AI nedostupan, ishod je NO TRADE.
- `test_validation.py`:
  - nema LIVE_ELIGIBLE bez walk-forwarda, Research 2.0 presude i ≥ 30 dana paper trgovanja;
  - shadow trgovine se ne računaju.
- `test_research2.py`:
  - v2 strategije i prototipovi nikad nisu u live skupu;
  - nema look-aheada;
  - kad live order book nedostaje, v2 bot3 ne ulazi.

## 4. Što se NIJE promijenilo i ostaje zabranjeno

- Stvarni nalozi tijekom razvoja: nijedan nije poslan. Testovi koriste PaperExchange i mockove.
- API ključevi: šifrirani (Fernet vault), nikad u logovima, kodu ni APK-u. Withdrawal dozvola mora biti isključena.
- Live trading ostaje zasebno zaključan (unlock gate) dok se ne ispune svi uvjeti i vlasnik izričito ne autorizira.
- Rezultat istraživanja (`CANDIDATE_FOR_PAPER`) **nije** aktivacija. Za LIVE_ELIGIBLE treba sve od navedenog:
  - svježa walk-forward potvrda;
  - Research 2.0 presuda;
  - ≥ 50 paper trgovina kroz ≥ 30 dana;
  - paper PF ≥ 1,05;
  - DD ≤ 20 %;
  - odluka vlasnika.
