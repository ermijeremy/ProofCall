"""Interview scheduling request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InterviewScheduleCreate(BaseModel):
    worker_ids: list[str] = Field(min_length=1)
    scheduled_at: datetime
    campaign_id: str | None = Field(default=None, max_length=100)
    retries: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: int = Field(default=30, ge=0)


class InterviewScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schedule_id: str
    worker_id: str
    campaign_id: str | None = None
    scheduled_at: datetime
    language: str
    status: str
    call_id: str | None = None
    retries: int
    retry_delay_seconds: int
    failure_reason: str | None = None
    created_at: datetime


class InterviewScheduleBatchRead(BaseModel):
    created: list[InterviewScheduleRead]
