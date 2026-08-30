"""Request bodies for the interview-batch thread."""

from pydantic import BaseModel, Field


class BatchCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    language: str = Field(default="am", max_length=20)


class BatchMessageCreate(BaseModel):
    text: str = Field(min_length=1)
