"""Environment-backed application settings."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _database_url() -> str:
    configured = os.getenv("CALLPROOF_DATABASE_URL")
    if configured:
        # Vercel functions cannot write to the project bundle. Preserve an
        # explicitly configured remote URL, but relocate relative SQLite URLs
        # to the writable temporary filesystem.
        if os.getenv("VERCEL") and configured.startswith("sqlite") and not configured.startswith("sqlite:////tmp/"):
            return "sqlite:////tmp/callproof.db"
        return configured
    return "sqlite:////tmp/callproof.db" if os.getenv("VERCEL") else "sqlite:///./callproof.db"


class Settings(BaseModel):
    app_name: str = "CallProof Backend"
    app_version: str = "0.1.0"
    database_url: str = Field(default_factory=_database_url)
    teleexpert_base_url: str = Field(default_factory=lambda: os.getenv("TELEXPERT_BASE_URL", ""))
    teleexpert_api_key: str = Field(default_factory=lambda: os.getenv("TELEXPERT_API_KEY", ""), repr=False)
    teleexpert_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("TELEXPERT_TIMEOUT_SECONDS", "30"))
    )
    teleexpert_webhook_url: str = Field(default_factory=lambda: os.getenv("TELEXPERT_WEBHOOK_URL", ""))
    teleexpert_webhook_secret: str = Field(
        default_factory=lambda: os.getenv("TELEXPERT_WEBHOOK_SECRET", ""), repr=False
    )
    teleexpert_answer_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("TELEXPERT_ANSWER_TIMEOUT_SECONDS", "45"))
    )
    scheduler_enabled: bool = Field(
        default_factory=lambda: os.getenv("CALLPROOF_SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    )
    scheduler_interval_seconds: int = Field(
        default_factory=lambda: max(1, int(os.getenv("CALLPROOF_SCHEDULER_INTERVAL_SECONDS", "10")))
    )


settings = Settings()
