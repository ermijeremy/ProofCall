"""Employer import and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EmployerCreate(BaseModel):
    company_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    reported_good_jobs: int = Field(default=0, ge=0)
    average_salary: int | None = Field(default=None, ge=0)
    training_participation: int | None = Field(default=None, ge=0)
    worker_count: int | None = Field(default=None, ge=0)


class EmployerRead(EmployerCreate):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
