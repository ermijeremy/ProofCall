"""Call submission and status schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CallStatus = Literal["queued", "dialing", "retry_wait", "in_progress", "completed", "failed", "cancelled"]


class CallCreate(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    campaign_id: str | None = Field(default=None, max_length=100)
    phone_number: str = Field(min_length=3, max_length=40)
    prompt: str = Field(min_length=1)
    response_format: str = "both"
    retries: int = Field(default=2, ge=0)
    retry_delay_seconds: int = Field(default=30, ge=0)


class CallStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_id: str
    worker_id: str
    status: CallStatus
    transcript: str | None = None
    transcript_turns: list[dict[str, Any]] | None = None
    language: str | None = None
    completed_at: object | None = None
    retries: int = 0
    retry_delay_seconds: int = 0
    audio_url: str | None = None
    failure_reason: str | None = None
