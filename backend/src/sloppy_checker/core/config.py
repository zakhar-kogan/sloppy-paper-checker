from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPC_", env_file=".env", extra="ignore")

    env: str = "development"
    api_token: str = "development-only-change-me"
    encryption_key: str | None = None
    database_url: str = "sqlite:///./paper_checker.db"
    redis_url: str = "redis://localhost:6379/0"
    grobid_url: str | None = "http://localhost:8070"
    nebius_api_key: str | None = None
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "testserver"])
    cors_origins: list[str] = Field(default_factory=list)
    upload_dir: Path = Path("./uploads")
    max_upload_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: float = 12.0
    eager_tasks: bool = True

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("api_token")
    @classmethod
    def production_token(cls, value: str, info):
        if info.data.get("env") == "production" and len(value) < 32:
            raise ValueError("SPC_API_TOKEN must be at least 32 characters in production")
        return value


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()

