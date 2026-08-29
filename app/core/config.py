"""Environment-backed application settings."""

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "CallProof Backend"
    app_version: str = "0.1.0"
    database_url: str = "sqlite:///./callproof.db"
    teleexpert_base_url: str = ""
    teleexpert_api_key: str = Field(default="", repr=False)
    teleexpert_timeout_seconds: int = 30


settings = Settings()

