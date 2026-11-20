#!/data/data/com.termux/files/usr/bin/bash
# MILLION-X backend on the phone (Termux). Run with:  bash termux-start.sh
# Listens ONLY on 127.0.0.1 (not reachable from Wi-Fi or the internet). Starts in PAPER mode.
set -e
cd "$(dirname "$0")"
DATA="$HOME/mx-data"
mkdir -p "$DATA/backups" "$DATA/klines"

if ! python -c "import numpy, cryptography" 2>/dev/null; then
  echo ">> Installing numpy + cryptography (Termux packages)..."
  pkg install -y python-numpy python-cryptography
fi
if ! python -c "import starlette, uvicorn, requests, yaml, websockets" 2>/dev/null; then
  echo ">> Installing Python libraries..."
  pip install -r requirements-termux.txt
fi

KEYF="$HOME/.mx_master_key"
if [ ! -f "$KEYF" ]; then
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$KEYF"
fi
chmod 600 "$KEYF"
CODEF="$HOME/.mx_pair_code"
if [ ! -f "$CODEF" ]; then
  python -c "import secrets; print('-'.join(secrets.token_hex(2).upper() for _ in range(3)))" > "$CODEF"
fi
chmod 600 "$CODEF"

export MX_MASTER_KEY_FILE="$KEYF"
export MX_BOOTSTRAP_CODE="$(cat "$CODEF")"
export MX_DB_PATH="$DATA/millionx.db"
export MX_DATA_DIR="$DATA/klines"
export MX_CONFIG="$PWD/config.yaml"
export MX_API_HOST=127.0.0.1
export MX_API_PORT=8080
export MX_MODE="${MX_MODE:-paper}"

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

echo ""
echo "=============================================="
echo "  MILLION-X server is starting (mode: $MX_MODE)"
echo "  In the app enter:"
echo "    Server URL:    http://127.0.0.1:8080"
echo "    Pairing code:  $(cat "$CODEF")"
echo "  (the code works once, until the first owner is paired)"
echo "  Keep Termux running. Stop with CTRL+C."
echo "=============================================="
echo ""
exec python -m millionx.main
