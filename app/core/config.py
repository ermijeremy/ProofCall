"""Environment-backed application settings."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


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
    # Used to turn a number written the way a local spreadsheet writes it —
    # "0933325080" or "933325080" — into the E.164 form the telephony API insists
    # on. Ethiopia by default, because that is where the interviews are.
    default_country_code: str = Field(
        default_factory=lambda: os.getenv("CALLPROOF_DEFAULT_COUNTRY_CODE", "251").strip().lstrip("+")
    )
    scheduler_enabled: bool = Field(
        default_factory=lambda: os.getenv("CALLPROOF_SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    )
    scheduler_interval_seconds: int = Field(
        default_factory=lambda: max(1, int(os.getenv("CALLPROOF_SCHEDULER_INTERVAL_SECONDS", "10")))
    )


settings = Settings()
