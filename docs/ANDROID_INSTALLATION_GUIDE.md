# ANDROID INSTALLATION GUIDE (1.9.0)

Uređaj: Android 11+ (minSdk 30), ARM64 (npr. Samsung Galaxy S25 Ultra).

1. Preuzmi `MillionX-1.9.0-…-phone-arm64-DEBUG.apk` i otvori ga. Dopusti instalaciju iz tog izvora.
   Ako javi grešku potpisa, deinstaliraj staru verziju (DEBUG buildovi s CI-ja mogu imati drugi ključ). Podaci enginea na mobitelu se tada brišu.
2. Prvo pokretanje: „Run engine on this phone (free)“ ili „Connect to a server“ (VPS URL + pairing kod).
3. Samsung: Postavke → Aplikacije → MILLION-X → Baterija → **Neograničeno**; makni iz „Sleeping apps“.
4. Admin: odaberi kapital (500 EUR), timeframe, način (PAPER/DEMO) i izvršavanje (AUTO za demo).
5. AI: More → AI Command Center → dodaj besplatni Groq ključ → Save & check.
6. Binance Demo: More → Binance Account → DEMO ključ → „Let the bots trade on this DEMO account“.

DEBUG APK nije produkcijski release. Produkcijski potpis zahtijeva vlasnikov keystore (GitHub secrets `MX_KEYSTORE_*`).
