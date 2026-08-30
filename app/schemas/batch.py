"""Request bodies for the company thread."""

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class BatchCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    language: str = Field(default="am", min_length=2, max_length=20)


class BatchMessageCreate(BaseModel):
    text: str = Field(min_length=1)


class ThreadMessageCreate(BaseModel):
    text: str = Field(min_length=1)


class FixtureReplayCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
