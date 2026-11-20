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


PORTS = (8080, 18080, 28080, 38080)


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_port(base_dir: str) -> int:
    """First free port (another app may already use 8080). Written to mx_port.txt."""
    for p in PORTS:
        if _port_free(p):
            with open(os.path.join(base_dir, "mx_port.txt"), "w") as fh:
                fh.write(str(p))
            return p
    raise RuntimeError(f"no free local port among {PORTS}")


def setup_logging(base_dir: str) -> str:
    import logging
    from logging.handlers import RotatingFileHandler
    path = os.path.join(base_dir, "mx-data", "server.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    return path


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
    import logging
    import traceback
    _error = None
    try:
        setup_logging(base_dir)
        import uvicorn
        configure(base_dir, bootstrap_code, port)
        from .main import build
        app, s = build()
        logging.getLogger("millionx").info("starting local server on 127.0.0.1:%s", port)
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", lifespan="on", log_config=None)
        _server = uvicorn.Server(cfg)
        _server.run()
        if not _server.started:
            _error = f"server could not start on port {port} (see server.log)"
    except BaseException as e:  # reported to the Android service and written to server.log
        _error = f"{type(e).__name__}: {e}"
        logging.getLogger("millionx").error("local server crashed: %s\n%s", _error, traceback.format_exc())


_port = 0


def start_background(base_dir: str, bootstrap_code: str, port: int = 0) -> int:
    """Non-blocking start (idempotent). Returns the port the server uses."""
    global _thread, _port
    if _thread is not None and _thread.is_alive():
        return _port
    _port = port or choose_port(base_dir)
    _thread = threading.Thread(target=run, args=(base_dir, bootstrap_code, _port), name="mx-server", daemon=True)
    _thread.start()
    return _port


def local_pairing_code(base_dir: str, ttl_s: int = 300) -> str:
    """Fresh single-use OWNER code for the app that embeds this server.

    Only callable in-process (the app itself), never over HTTP, so re-pairing the
    phone works even after the first bootstrap code was used or app data was lost."""
    from .security import create_pairing_code
    from .storage import Store
    st = Store(os.path.join(base_dir, "mx-data", "millionx.db"))
    try:
        st.x("DELETE FROM auth_failures WHERE kind='pair' AND ip IN ('127.0.0.1', '::1')")
        return create_pairing_code(st, "owner", ttl_s)
    finally:
        st.db.close()


def log_tail(base_dir: str, lines: int = 80) -> str:
    path = os.path.join(base_dir, "mx-data", "server.log")
    if not os.path.exists(path):
        return "(no server.log yet)"
    with open(path, errors="replace") as fh:
        return "".join(fh.readlines()[-lines:])


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def last_error() -> str:
    return _error or ""


def stop() -> None:
    if _server is not None:
        _server.should_exit = True


def new_pairing_code() -> str:
    return "-".join(secrets.token_hex(2).upper() for _ in range(3))
