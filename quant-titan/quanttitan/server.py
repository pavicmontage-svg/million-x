"""Application assembly shared by the server CLI, Docker and the Android app."""
from __future__ import annotations

import os

from .api.app import create_app
from .core.logging import setup_logging
from .data.history import HistoryStore
from .runtime.trader import Trader
from .security.auth import Auth
from .security.secrets import SecretStore, load_master_key
from .storage.db import Database


def build(data_dir: str = "data", *, device_token: str | None = None, autostart: bool = True, replay_symbols: list[str] | None = None,
          replay_tf: str = "3m"):
    os.makedirs(data_dir, exist_ok=True)
    setup_logging(os.environ.get("QT_LOG_LEVEL", "INFO"), json_output=os.environ.get("QT_LOG_JSON") == "1")
    db = Database(os.environ.get("QT_DATABASE_URL") or f"sqlite:///{os.path.join(data_dir, 'quanttitan.db')}")
    secrets = SecretStore(db, load_master_key(os.path.join(data_dir, "master.key")))
    replay = None
    funding = None
    if replay_symbols:
        hs = HistoryStore(os.path.join(data_dir, "history"))
        replay = {s: hs.load(s, replay_tf) for s in replay_symbols}
        replay = {s: c for s, c in replay.items() if c is not None}
        funding = {s: hs.load_funding(s) for s in replay}
    trader = Trader(db, secrets, replay_data=replay, replay_funding=funding)
    auth = Auth(db, device_token)
    app = create_app(trader, auth, autostart=autostart, history_dir=os.path.join(data_dir, "history"))
    return app, trader, auth, db
