"""Server entry point: python -m millionx.main"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import uvicorn

from .api import create_app
from .config import load_settings
from .runtime import Runtime
from .security import Vault
from .storage import Store


def build():
    logging.basicConfig(level=os.environ.get("MX_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    store = Store(settings.db_path)
    try:
        vault = Vault()
    except RuntimeError as e:
        logging.getLogger("millionx").warning("%s — API keys cannot be stored; paper mode only", e)
        vault = None
    rt = Runtime(settings, store, vault)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if os.environ.get("MX_AUTOSTART", "1") == "1":
            await asyncio.get_running_loop().run_in_executor(None, rt.start_engine)
            rt.start_watchdog()
        yield
        await asyncio.get_running_loop().run_in_executor(None, rt.stop_engine)

    app = create_app(rt, lifespan)
    return app, settings


def main():
    app, s = build()
    uvicorn.run(app, host=s.api_host, port=s.api_port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
