"""Environment-backed application settings."""

import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "CallProof Backend"
    app_version: str = "0.1.0"
    database_url: str = Field(default_factory=lambda: os.getenv("CALLPROOF_DATABASE_URL", "sqlite:///./callproof.db"))
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


settings = Settings()
