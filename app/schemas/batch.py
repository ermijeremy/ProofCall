"""Request bodies for the company thread."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


class CompanyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class CompanySettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    clock_convention: str | None = Field(default=None, pattern="^(24h|ethiopian)$")
    minimum_wage_etb: int | None = Field(default=None, ge=0)
    small_cell_threshold: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("timezone", "clock_convention", "small_cell_threshold", mode="before")
    @classmethod
    def required_settings_cannot_be_null(cls, value):
        # Omitted fields retain their current values; explicit null is not a
        # reset instruction for the corresponding NOT NULL database columns.
        if value is None:
            raise ValueError("This setting cannot be null")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid IANA timezone") from exc
        return value


class BatchCreate(BaseModel):
    """Compatibility request model for internal callers during the refactor."""

    title: str = Field(default="Callwise validation round", max_length=255)
    language: str = Field(default="am", min_length=2, max_length=20)


class BatchMessageCreate(BaseModel):
    text: str = Field(min_length=1)


class ThreadMessageCreate(BaseModel):
    text: str = Field(min_length=1)


class FixtureReplayCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    fixture: str = Field(min_length=1)


class RoundSelection(BaseModel):
    worker_ids: list[str] = Field(min_length=1)


class RoundDial(BaseModel):
    retries: int = Field(default=1, ge=0, le=5)
