from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPC_", env_file=".env", extra="ignore")

    env: str = "development"
    api_token: str = "development-only-change-me"
    database_url: str = "sqlite:///./paper_checker.db"
    document_store: str = "filesystem"
    document_store_path: Path = Path("./data/documents")
    analysis_dispatcher: str = "inline"
    s3_endpoint_url: str | None = None
    s3_region: str = "eu-north1"
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    nebius_project_id: str | None = None
    nebius_job_image: str | None = None
    nebius_job_platform: str = "cpu-d3"
    nebius_job_preset: str = "4vcpu-16gb"
    nebius_job_subnet_id: str | None = None
    nebius_job_secret_id: str | None = None
    nebius_api_key: str | None = None
    nebius_api_key_file: Path | None = None
    token_factory_worker_model: str = "Qwen/Qwen3-30B-A3B-Instruct-2507"
    token_factory_reviewer_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507"
    unpaywall_email: str = "operator@example.invalid"
    ncbi_email: str = "operator@example.invalid"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "testserver"])
    cors_origins: list[str] = Field(default_factory=list)
    max_upload_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: float = 12.0
    provider_timeout_seconds: float = Field(default=120.0, ge=10, le=600)
    reviewer_deadline_seconds: float = Field(default=240.0, ge=30, le=900)
    report_retention_hours: int = Field(default=24, ge=1, le=720)
    resolution_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    guest_cookie_name: str = "spc_guest"
    hosted_runs_per_session: int | None = Field(default=None, ge=1, le=100)
    concurrent_runs_per_session: int = Field(default=1, ge=1, le=10)
    observability_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None
    otel_service_name: str = "sloppy-paper-checker"

    @field_validator("document_store")
    @classmethod
    def valid_document_store(cls, value: str) -> str:
        if value not in {"filesystem", "s3"}:
            raise ValueError("SPC_DOCUMENT_STORE must be filesystem or s3")
        return value

    @field_validator("analysis_dispatcher")
    @classmethod
    def valid_dispatcher(cls, value: str) -> str:
        if value not in {"inline", "nebius_job"}:
            raise ValueError("SPC_ANALYSIS_DISPATCHER must be inline or nebius_job")
        return value

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("nebius_api_key_file", mode="before")
    @classmethod
    def empty_secret_file(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("api_token")
    @classmethod
    def production_token(cls, value: str, info):
        if info.data.get("env") == "production" and len(value) < 32:
            raise ValueError("SPC_API_TOKEN must be at least 32 characters in production")
        return value

    @staticmethod
    def _secret_value(value: str | None, path: Path | None) -> str | None:
        if value:
            return value
        if path:
            secret = path.read_text().strip()
            return secret or None
        return None

    @property
    def token_factory_api_key(self) -> str | None:
        return self._secret_value(self.nebius_api_key, self.nebius_api_key_file)

    def validate_adapters(self) -> None:
        if self.observability_enabled and not self.otel_exporter_otlp_endpoint:
            raise ValueError(
                "SPC_OBSERVABILITY_ENABLED requires SPC_OTEL_EXPORTER_OTLP_ENDPOINT"
            )
        if self.document_store == "s3" and not all(
            (self.s3_endpoint_url, self.s3_bucket)
        ):
            raise ValueError("S3 document storage requires SPC_S3_ENDPOINT_URL and SPC_S3_BUCKET")
        if self.analysis_dispatcher == "nebius_job":
            missing = [
                name
                for name, value in {
                    "SPC_NEBIUS_PROJECT_ID": self.nebius_project_id,
                    "SPC_NEBIUS_JOB_IMAGE": self.nebius_job_image,
                    "SPC_NEBIUS_JOB_SECRET_ID": self.nebius_job_secret_id,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError("Nebius job dispatcher requires " + ", ".join(missing))
            if self.database_url.startswith("sqlite"):
                raise ValueError("Nebius jobs require PostgreSQL; SQLite is local-only")
            if self.document_store != "s3":
                raise ValueError("Nebius jobs require the S3 document store")


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
