# Deployment

## Android (primary)
See ANDROID.md — install the APK from GitHub Releases. Nothing else is needed.

## Server (optional, Docker)
```bash
cd quant-titan
cp .env.example .env          # set QT_MASTER_KEY (Fernet key) and a strong POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec app python -m quanttitan create-admin admin   # then open https://<host> (put a TLS proxy in front)
```
`QT_ALLOW_LIVE` stays `0` unless you deliberately enable live trading. Generate a master key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and back it up.

## Bare metal
```bash
pip install -r requirements.txt
QT_DATA_DIR=/var/lib/quanttitan python -m quanttitan serve --host 127.0.0.1 --port 8765
```
Expose it only through an HTTPS reverse proxy (Caddy/nginx).

## CI/CD
`.github/workflows/quant-titan.yml`: tests on Python 3.12 (current libraries) and on Python 3.10 with the exact
APK versions, storage tests on PostgreSQL 16, then the APK build and the `quant-titan-latest` GitHub release.
