"""Entry point used by the Android app (Chaquopy).

The Kotlin foreground service calls ``start(base_dir, port, device_token)``. The server binds to
127.0.0.1 only, so nothing outside the phone can reach it; the WebView authenticates with the
device token generated and stored by the app in its private storage.
"""
from __future__ import annotations

import os
import socket
import threading
import traceback

_server = None
_thread: threading.Thread | None = None
_error: str | None = None


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start(base_dir: str, port: int, device_token: str) -> int:
    """Start the backend in a background thread; returns the port actually used."""
    global _server, _thread, _error
    if _thread is not None and _thread.is_alive():
        return _server.config.port
    os.environ.setdefault("QT_DATA_DIR", base_dir)
    os.environ["QT_WEB_DIR"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    for p in (port, port + 1, port + 2, port + 3):
        if _free(p):
            port = p
            break
    import uvicorn
    from .server import build
    app, *_ = build(base_dir, device_token=device_token)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="wsproto", lifespan="on")
    _server = uvicorn.Server(cfg)
    _error = None

    def run():
        global _error
        try:
            _server.run()
        except BaseException:
            _error = traceback.format_exc()

    _thread = threading.Thread(target=run, name="quanttitan", daemon=True)
    _thread.start()
    return port


def stop() -> None:
    if _server is not None:
        _server.should_exit = True


def status() -> str:
    if _error:
        return "error: " + _error[-500:]
    if _thread is not None and _thread.is_alive():
        return "running"
    return "stopped"
