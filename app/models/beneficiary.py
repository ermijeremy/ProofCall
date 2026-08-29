"""Beneficiary contact and employer-claim persistence model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Beneficiary(Base):
    __tablename__ = "beneficiaries"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("employers.company_id"), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(20), default="am", nullable=False)
    employer_claims: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

