# VPS DEPLOYMENT GUIDE (1.9.0)

Za neprekidno trgovanje 24/7 preporuka je VPS. Android aplikacija **ne zamjenjuje** VPS:
sustav može ugasiti pozadinski proces, a TradingView webhook treba javni HTTPS.

## Brzi start (Hetzner / bilo koji Ubuntu 24.04 VPS, ~4–6 EUR/mj.)
1. Napravi server (Ubuntu 24.04, 2 GB RAM). Lokacija izvan SAD-a (Binance vraća 451 za SAD).
2. U „Cloud config“ zalijepi `deploy/cloud-init.yaml` (upiši GitHub token za privatni repo i pairing kod). Skripta instalira Docker, ufw, Caddy s HTTPS-om (sslip.io domena) i pokreće `docker compose up -d`.
3. U aplikaciji: Pairing → URL `https://<ip>.sslip.io` → pairing kod.

## Što compose radi
| Servis | Uloga |
|---|---|
| `backend` | API + trading engine; `restart: unless-stopped`; healthcheck na `/api/health` svake minute; rotacija logova (5 × 20 MB); memory limit 1 GB |
| `backup` | `python -m millionx.cli backup` jednom dnevno (rotacija u `data/backups`) |
| `caddy` | HTTPS (Let's Encrypt) ispred backenda |

## Nadzor
- `GET /api/health`: engine, svježina podataka, veza s burzom, incidenti, schema, `process` (RAM MB, CPU s, load, threadovi).
- AI troškovi: AI Command Center (zahtjevi, tokeni i greške po provideru danas).
- Oporavak nakon prekida veze: rekoncilijacija s Binanceom na startu i na svakoj svijeći; pozicije bez stopa dobivaju stop ili se zatvaraju.
- Restore baze: `docker compose stop backend`, kopiraj željeni `data/backups/millionx-*.db` preko `data/millionx.db`, `docker compose start backend` (migracije i rekoncilijacija rade se pri startu). Watchdog već radi dnevni backup (14 kopija); servis `backup` je dodatna kopija.

## Baza
SQLite (WAL) s append-only audit tablicom i migracijama. PostgreSQL i Redis **nisu** dio 1.9.0:
engine je jedan proces i SQLite pokriva opterećenje; selidba bi bila zaseban projekt s vlastitim testovima.
