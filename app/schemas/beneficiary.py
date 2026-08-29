"""Beneficiary import and response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BeneficiaryCreate(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    company_id: str = Field(min_length=1, max_length=100)
    phone_number: str = Field(min_length=3, max_length=40)
    preferred_language: str = Field(default="am", max_length=20)
    employer_claims: dict[str, Any] = Field(default_factory=dict)


class BeneficiaryRead(BeneficiaryCreate):
    model_config = ConfigDict(from_attributes=True)

    is_active: bool
    created_at: datetime
