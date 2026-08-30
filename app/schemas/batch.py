"""Request bodies for the company thread."""

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class ThreadMessageCreate(BaseModel):
    text: str = Field(min_length=1)
