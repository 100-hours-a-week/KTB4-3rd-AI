from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "KTB4 AI API"
    app_env: Literal["local", "test", "development", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_workers: Literal[1] = 1

    congestion_window_minutes: int = Field(default=20, gt=0)
    congestion_cache_ttl_minutes: int = Field(default=25, gt=0)
    congestion_model_concurrency: int = Field(default=1, gt=0)
    congestion_model_provider: str = ""
    congestion_model_name: str = ""
    openai_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
