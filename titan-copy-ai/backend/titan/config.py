"""Runtime configuration (environment variables prefixed TITAN_).

Secrets never live in source code: the master encryption key, database password,
Anthropic key and FCM credentials are read from the environment or from files
mounted by the secrets manager (Docker secrets / systemd credentials).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TITAN_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://postgres@/titan?host=/tmp"
    redis_url: str = "redis://localhost:6379/0"

    # 32-byte urlsafe-base64 key for AES-256-GCM credential encryption.
    master_key: str | None = None
    master_key_file: Path | None = None

    # One-time pairing code for the first Android device (printed by `titan pair`).
    bootstrap_code: str | None = None

    # --- Trading mode guards -------------------------------------------------
    # Live trading is disabled by default. Even when this flag is true, every
    # live portfolio additionally needs a recorded, time-limited owner approval
    # that passes the live gate (30 days paper evidence, risk metrics).
    live_trading_enabled: bool = False

    forex_initial_capital_eur: float = 500.0
    crypto_initial_capital_usdt: float = 500.0

    # Signal staleness limits (seconds) per market.
    max_signal_age_forex_s: float = 20.0
    max_signal_age_crypto_s: float = 10.0

    # --- AI ---------------------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_enabled: bool = True

    # --- Notifications ------------------------------------------------------
    fcm_project_id: str | None = None
    fcm_service_account_file: Path | None = None

    # --- Market data --------------------------------------------------------
    # Public Binance market-data host (no key). Used for paper pricing of crypto.
    binance_public_data_url: str = "https://data-api.binance.vision"

    cors_origins: list[str] = []

    def resolved_master_key(self) -> str | None:
        if self.master_key:
            return self.master_key
        if self.master_key_file and self.master_key_file.exists():
            return self.master_key_file.read_text().strip()
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
