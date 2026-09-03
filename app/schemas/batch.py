"""Request bodies for the company thread."""

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class CompanySettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    clock_convention: str | None = Field(default=None, pattern="^(24h|ethiopian)$")
    minimum_wage_etb: int | None = Field(default=None, ge=0)
    small_cell_threshold: int | None = Field(default=None, ge=0, le=1000)


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
