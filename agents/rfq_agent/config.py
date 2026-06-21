from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RFQ_",
        env_file=".env",
        case_sensitive=False,
    )

    # LLM
    anthropic_api_key: SecretStr = Field(..., alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3

    # Agent loop
    max_iterations: int = 20
    agent_loop_timeout_s: int = 300
    enable_human_in_loop: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://ici:ici@localhost:5432/ici"

    # Redis / Events
    redis_url: str = "redis://localhost:6379/1"
    event_channel_prefix: str = "rfq_agent"

    # Email – SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_name: str = "ICI Procurement"
    smtp_from_email: str = "procurement@example.com"
    smtp_use_tls: bool = True

    # Email – IMAP (response monitoring)
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: SecretStr = SecretStr("")
    imap_poll_interval_s: int = 60
    imap_folder: str = "INBOX"

    # Optional SendGrid
    sendgrid_api_key: SecretStr | None = None
    email_backend: Literal["smtp", "sendgrid"] = "smtp"

    # Playwright / Scraping
    playwright_headless: bool = True
    playwright_timeout_ms: int = 30_000
    scraper_max_concurrent: int = 3
    scraper_user_agent: str = (
        "Mozilla/5.0 (compatible; ICI-Procurement-Bot/1.0; "
        "+https://example.com/bot)"
    )

    # Safety / Rate limiting
    max_emails_per_hour: int = 20
    max_emails_per_supplier_per_day: int = 2
    min_email_interval_s: int = 30
    allowed_domains_only: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    blacklisted_domains: list[str] = Field(default_factory=list)

    # NLP / Parsing
    spacy_model: str = "en_core_web_sm"
    currency_default: str = "EUR"


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()  # type: ignore[call-arg]
