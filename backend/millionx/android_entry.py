"""Entry point used when the backend runs INSIDE the Android app (Chaquopy).

The Android foreground service calls start_background(); the server listens
only on 127.0.0.1 so nothing outside the phone can reach it.
"""
from __future__ import annotations

import os
import secrets
import threading
from typing import Optional

_server = None
_thread: Optional[threading.Thread] = None
_error: Optional[str] = None


def _ensure_master_key(path: str) -> None:
    if not os.path.exists(path):
        from cryptography.fernet import Fernet
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(Fernet.generate_key().decode())
    os.chmod(path, 0o600)


def configure(base_dir: str, bootstrap_code: str, port: int = 8080) -> None:
    data = os.path.join(base_dir, "mx-data")
    os.makedirs(os.path.join(data, "backups"), exist_ok=True)
    os.makedirs(os.path.join(data, "klines"), exist_ok=True)
    key = os.path.join(base_dir, "mx_master.key")
    _ensure_master_key(key)
    os.environ.update({
        "MX_MASTER_KEY_FILE": key,
        "MX_BOOTSTRAP_CODE": bootstrap_code,
        "MX_DB_PATH": os.path.join(data, "millionx.db"),
        "MX_DATA_DIR": os.path.join(data, "klines"),
        "MX_API_HOST": "127.0.0.1",
        "MX_API_PORT": str(port),
        "MX_CONFIG": os.path.join(base_dir, "config.yaml"),
        "MX_LOG_LEVEL": os.environ.get("MX_LOG_LEVEL", "INFO"),
    })
    os.environ.setdefault("MX_MODE", "paper")


def run(base_dir: str, bootstrap_code: str, port: int = 8080) -> None:
    """Blocking: runs the API server + trading engine until stop() is called."""
    global _server, _error
    import uvicorn
    configure(base_dir, bootstrap_code, port)
    from .main import build
    app, s = build()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", lifespan="on")
    _server = uvicorn.Server(cfg)
    try:
        _server.run()
    except Exception as e:  # reported to the Android service
        _error = f"{type(e).__name__}: {e}"
        raise


def start_background(base_dir: str, bootstrap_code: str, port: int = 8080) -> bool:
    """Non-blocking start (idempotent). Returns False if already running."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return False
    _thread = threading.Thread(target=run, args=(base_dir, bootstrap_code, port), name="mx-server", daemon=True)
    _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def last_error() -> str:
    return _error or ""


def stop() -> None:
    if _server is not None:
        _server.should_exit = True


def new_pairing_code() -> str:
    return "-".join(secrets.token_hex(2).upper() for _ in range(3))
