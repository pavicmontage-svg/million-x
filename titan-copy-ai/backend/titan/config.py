"""Runtime configuration from environment variables (prefix TITAN_).

Plain dataclass (no pydantic) so the same backend runs on a server and embedded in the
Android app, where pydantic-core has no build. Secrets never live in source code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    env: str = "development"
    database_url: str = "postgresql+psycopg://postgres@/titan?host=/tmp"
    redis_url: str = "redis://localhost:6379/0"
    # embedded = running inside the Android app (SQLite, in-process bus and scheduler)
    embedded: bool = False

    master_key: str | None = None            # 32-byte urlsafe-base64 key (AES-256-GCM)
    master_key_file: Path | None = None
    bootstrap_code: str | None = None        # one-time pairing code valid until the first device pairs

    # Live trading is disabled by default; live portfolios additionally need a recorded,
    # time-limited owner approval that passes the live gate.
    live_trading_enabled: bool = False
    forex_initial_capital_eur: float = 500.0
    crypto_initial_capital_usdt: float = 500.0
    max_signal_age_forex_s: float = 20.0
    max_signal_age_crypto_s: float = 10.0

    anthropic_api_key: str | None = None     # ANTHROPIC_API_KEY or TITAN_ANTHROPIC_API_KEY
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_enabled: bool = True

    fcm_project_id: str | None = None
    fcm_service_account_file: Path | None = None
    binance_public_data_url: str = "https://data-api.binance.vision"
    cors_origins: list[str] = field(default_factory=list)
    data_dir: Path | None = None             # embedded: app files directory (logs, backups)

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict = {}
        for f in fields(cls):
            raw = os.environ.get(f"TITAN_{f.name.upper()}")
            if raw is None or raw == "":
                continue
            t = str(f.type)
            if "bool" in t:
                kw[f.name] = _bool(raw)
            elif "float" in t:
                kw[f.name] = float(raw)
            elif "Path" in t:
                kw[f.name] = Path(raw)
            elif "list" in t:
                kw[f.name] = [x.strip() for x in raw.split(",") if x.strip()]
            else:
                kw[f.name] = raw
        if "anthropic_api_key" not in kw and os.environ.get("ANTHROPIC_API_KEY"):
            kw["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
        return cls(**kw)

    def resolved_master_key(self) -> str | None:
        if self.master_key:
            return self.master_key
        if self.master_key_file and self.master_key_file.exists():
            return self.master_key_file.read_text().strip()
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
