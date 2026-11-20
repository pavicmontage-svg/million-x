# API SETUP GUIDE (1.9.0)

Svi ključevi unose se u aplikaciji, provjeravaju se i spremaju **šifrirano na engineu** (Fernet, master key).
Nikad nisu u APK-u, izvornom kodu ni logovima (greške se čiste od ključeva).

## 1. Binance Demo (preporuka za testiranje)
1. Binance aplikacija → More → Demo Trading (ili demo.binance.com).
2. Profil → Demo Trading API → Create API (system generated). Bez withdrawal dozvole.
3. MILLION-X → More → Binance Account → DEMO → zalijepi API key + secret → „Check & connect“.
4. Admin → Trading mode → DEMO. Admin → „Run demo preflight“ testira long/short, stop, take profit, partial close i rekoncilijaciju na demo računu.

## 2. Binance Live (pravi novac) — tek nakon testova
- Ključ: Futures trading DA, withdrawals NE, IP restriction NA IP VPS-a (obavezno; aplikacija odbija ključ bez toga).
- Live je zaključan dok: demo preflight ne prođe, live provjera ključa i računa ne prođe i vlasnik ne potvrdi otključavanje (vrijedi 12 h). Profil MAX je na live zabranjen.

## 3. Besplatni AI provideri (redoslijed failovera)
More → AI Command Center → provider → „Add key“ → „Save & check“ (health check čita popis modela) → „Test“ (provjera JSON formata).

| Provider | Gdje ključ | Napomena |
|---|---|---|
| Groq (primarni) | console.groq.com → API Keys | besplatni tier s ograničenjima po minuti/danu; točne limite vidiš u Groq konzoli |
| Google Gemini (sekundarni) | aistudio.google.com/apikey | besplatni tier ima male dnevne kvote koje Google mijenja |
| OpenRouter (tercijarni) | openrouter.ai/keys | koriste se SAMO modeli čiji id završava s `:free` |

Model: prazno = automatski izbor s popisa modela tog providera (ne hardkodiraju se stari nazivi). Kad provider vrati 429, ulazi u cool-down prema `Retry-After`; ako svi padnu, AI odluke su NO TRADE, a engine radi dalje.

## 4. TradingView (samo s VPS-om)
1. More → AI Command Center nije potreban; u Adminu/API-ju: `GET /api/tradingview/setup` daje tajni ključ i predložak poruke (`POST` rotira ključ).
2. TradingView alert → Webhook URL: `https://<tvoja-domena>/api/tradingview/webhook`.
3. Message (JSON): `{"secret":"…","id":"{{strategy.order.id}}-{{timenow}}","symbol":"{{ticker}}","side":"LONG","timeframe":"{{interval}}","time":"{{timenow}}","price":{{close}},"stop":<cijena>,"take_profit":<cijena>}`.
4. Signal prolazi supervisor i risk engine; može biti odbijen. Mobitel (127.0.0.1) ne može primati webhookove.
