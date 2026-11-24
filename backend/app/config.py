"""Application settings, loaded from the environment."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProvider = Literal["ollama", "anthropic", "openai", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DBOPT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- meta database ----------------------------------------------------
    meta_dsn: str = "postgresql+asyncpg://dbopt:dbopt@localhost:5433/dbopt_meta"

    # --- bundled sandbox --------------------------------------------------
    sandbox_dsn: str | None = "postgresql://dbopt_monitor:monitor@localhost:5434/shopdb"
    sandbox_apply_dsn: str | None = "postgresql://dbopt_apply:apply@localhost:5434/shopdb"
    sandbox_auto_register: bool = True

    # --- collector --------------------------------------------------------
    poll_interval_seconds: float = Field(default=5.0, ge=1.0, le=300.0)
    snapshot_retention_hours: int = Field(default=48, ge=1, le=24 * 30)

    # --- safety -----------------------------------------------------------
    statement_timeout_ms: int = Field(default=5_000, ge=100, le=600_000)
    lock_timeout_ms: int = Field(default=2_000, ge=100, le=600_000)
    idle_tx_timeout_ms: int = Field(default=10_000, ge=1_000, le=600_000)
    target_pool_max: int = Field(default=3, ge=1, le=20)
    redact_literals: bool = True

    # --- llm --------------------------------------------------------------
    llm_provider: LLMProvider = "none"
    llm_timeout_seconds: float = Field(default=120.0, ge=5.0, le=900.0)
    llm_max_output_tokens: int = Field(default=4096, ge=256, le=32_000)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:14b"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # --- api --------------------------------------------------------------
    # NoDecode: pydantic-settings otherwise JSON-decodes list fields *before* our
    # validator runs, so a plain "http://localhost:3000" in .env would crash on
    # startup with JSONDecodeError. The validator below accepts either a
    # comma-separated string or a JSON array.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none"

    def session_setup_sql(self) -> list[str]:
        """Guardrails applied to every session opened against a target database.

        These run on connect, before any application query. They bound the
        damage a bad plan or a forgotten transaction can do to the database we
        are supposed to be observing rather than disturbing.
        """
        return [
            f"SET statement_timeout = {self.statement_timeout_ms}",
            f"SET lock_timeout = {self.lock_timeout_ms}",
            f"SET idle_in_transaction_session_timeout = {self.idle_tx_timeout_ms}",
            "SET application_name = 'dbopt-monitor'",
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
