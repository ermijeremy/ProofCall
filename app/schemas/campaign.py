"""Campaign request and response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CampaignCreate(BaseModel):
    programme_name: str = Field(min_length=1, max_length=255)
    company_id: str = Field(min_length=1, max_length=100)
    strategy: str = Field(default="sampled_verification", max_length=50)
    sample_size: int = Field(default=0, ge=0)
    language: str = Field(default="am", max_length=20)
    criteria: dict[str, Any] = Field(default_factory=dict)


class CampaignRead(CampaignCreate):
    model_config = ConfigDict(from_attributes=True)

    campaign_id: str
    status: str
    created_at: datetime


class CampaignWorkerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    campaign_id: str
    worker_id: str
    call_id: str | None = None
    status: str
    sampled_at: datetime


class CampaignDetail(CampaignRead):
    workers: list[CampaignWorkerRead] = Field(default_factory=list)
