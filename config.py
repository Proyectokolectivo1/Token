"""
Configuración central del proyecto.
Carga desde variables de entorno (.env en dev).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: Literal["development", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_log_level: str = "info"
    app_base_url: str = "http://localhost:8080"

    # DB
    database_url: str = Field(..., alias="DATABASE_URL")
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # LLM
    llm_provider: Literal["openai", "groq", "together", "local"] = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str | None = None

    # Target site
    target_site: str = "statebate.com"
    target_base_url: str = "https://statebate.com"
    target_room_list_path: str = "/list?page={page}"
    scrape_request_delay: float = 2.0
    scrape_concurrency: int = 2
    scrape_user_agent_rotation: bool = True
    scrape_use_proxy: bool = False

    # Pixel / boosting
    pixel_secret: SecretStr = SecretStr("change-me-please")
    boost_affiliate_id: str | None = None
    boost_recommender_lookback_days: int = 14

    # GitHub
    github_token: SecretStr = SecretStr("")
    github_repo: str = ""

    # Seguridad
    api_key: SecretStr = SecretStr("")

    @computed_field  # type: ignore[misc]
    @property
    def is_prod(self) -> bool:
        return self.app_env == "production"

    @computed_field  # type: ignore[misc]
    @property
    def llm_config(self) -> dict:
        """Dict listo para ScrapegraphAI graph_config."""
        cfg: dict = {
            "model": self.llm_model,
            "api_key": self.llm_api_key.get_secret_value(),
        }
        if self.llm_base_url:
            cfg["base_url"] = self.llm_base_url
        return cfg


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
