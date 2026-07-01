from __future__ import annotations

from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "ICI Platform"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"

    # Database
    database_url: str = "postgresql+asyncpg://ici:ici@localhost:5432/ici"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Observability
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "ici-backend"
    log_level: str = "INFO"
    log_json: bool = False

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100

    @field_validator("database_url")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        if not (v.startswith("postgresql") or v.startswith("sqlite+aiosqlite")):
            raise ValueError("Only PostgreSQL or SQLite+aiosqlite is supported")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
