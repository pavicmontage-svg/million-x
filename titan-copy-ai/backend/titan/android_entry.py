"""Entry point when TITAN runs INSIDE the Android app (Chaquopy).

Everything runs on the phone: SQLite database in the app's private storage, in-process event
bus and scheduler, the copy/risk engine threads, and the API on 127.0.0.1 only (nothing outside
the phone can reach it). The Android foreground service calls start_background() and keeps the
process alive; the UI talks to http://127.0.0.1:<port> with a token issued in-process.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import traceback
from logging.handlers import RotatingFileHandler

_lock = threading.Lock()
_server = None
_server_thread: threading.Thread | None = None
_runtime = None
_port = 0
_error: str | None = None
PORTS = (8765, 18765, 28765, 38765)


def _data(base_dir: str) -> str:
    d = os.path.join(base_dir, "titan")
    os.makedirs(os.path.join(d, "backups"), exist_ok=True)
    return d


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def setup_logging(base_dir: str) -> str:
    path = os.path.join(_data(base_dir), "engine.log")
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path


def configure(base_dir: str, master_key: str, anthropic_key: str = "") -> None:
    data = _data(base_dir)
    os.environ.update({
        "TITAN_EMBEDDED": "true",
        "TITAN_ENV": "android",
        "TITAN_DATABASE_URL": "sqlite:///" + os.path.join(data, "titan.db"),
        "TITAN_MASTER_KEY": master_key,
        "TITAN_DATA_DIR": data,
        "TITAN_BACKUP_DIR": os.path.join(data, "backups"),
    })
    if anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    from . import config, db
    from .events import MemoryBus, set_bus
    config.get_settings.cache_clear()
    set_bus(MemoryBus())
    db.configure(os.environ["TITAN_DATABASE_URL"])
    db.init_db()


def _serve(port: int):
    global _server, _error
    try:
        import uvicorn

        from .api.app import app
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, loop="asyncio", http="h11", ws="websockets",
                             lifespan="on", log_config=None, access_log=False)
        _server = uvicorn.Server(cfg)
        _server.run()
        if not _server.started:
            _error = f"API could not start on port {port}"
    except BaseException as e:  # reported to the Android service
        _error = f"{type(e).__name__}: {e}"
        logging.getLogger("titan").error("API crashed: %s\n%s", _error, traceback.format_exc())


def start_background(base_dir: str, master_key: str, anthropic_key: str = "") -> int:
    """Idempotent, non-blocking start. Returns the local port of the API."""
    global _server_thread, _runtime, _port, _error
    with _lock:
        if _server_thread is not None and _server_thread.is_alive() and _runtime is not None:
            return _port
        _error = None
        setup_logging(base_dir)
        try:
            configure(base_dir, master_key, anthropic_key)
            from .db import session_scope
            from .discovery.service import seed_watchlist
            from .services.venues import ensure_default_portfolios
            with session_scope() as s:
                ensure_default_portfolios(s)
                seed_watchlist(s)
            from .runtime import Runtime
            if _runtime is None or _runtime.stop.is_set():
                _runtime = Runtime(consumer="android")
                _runtime.start(scheduler=True)
            _port = next((p for p in PORTS if _port_free(p)), 0)
            if not _port:
                raise RuntimeError(f"no free local port among {PORTS}")
            _server_thread = threading.Thread(target=_serve, args=(_port,), name="titan-api", daemon=True)
            _server_thread.start()
            logging.getLogger("titan").info("TITAN embedded engine started; API on 127.0.0.1:%s", _port)
        except BaseException as e:
            _error = f"{type(e).__name__}: {e}"
            logging.getLogger("titan").error("start failed: %s\n%s", _error, traceback.format_exc())
            raise
        return _port


def local_token(device_name: str = "This phone") -> str:
    """Token for the app's own UI (in-process; never leaves the phone)."""
    from .db import session_scope
    from .security import issue_device_token
    with session_scope() as s:
        return issue_device_token(s, device_name)


def is_running() -> bool:
    return bool(_server_thread and _server_thread.is_alive() and _runtime and not _runtime.stop.is_set())


def last_error() -> str:
    return _error or ""


def port() -> int:
    return _port


def stop() -> None:
    global _runtime
    if _server is not None:
        _server.should_exit = True
    if _runtime is not None:
        _runtime.shutdown("Engine stopped on the phone.")
        _runtime = None


def log_tail(base_dir: str, lines: int = 150) -> str:
    path = os.path.join(_data(base_dir), "engine.log")
    if not os.path.exists(path):
        return "(no log yet)"
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - 64_000))
        return b"\n".join(fh.read().splitlines()[-lines:]).decode("utf-8", "replace")
