"""Beneficiary contact and employer-claim persistence model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Beneficiary(Base):
    __tablename__ = "beneficiaries"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    company_id: Mapped[str] = mapped_column(ForeignKey("employers.company_id"), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(20), default="am", nullable=False)
    gender: Mapped[Any] = mapped_column(String(20), nullable=True)
    age_band: Mapped[Any] = mapped_column(String(20), nullable=True)
    training_cohort_id: Mapped[Any] = mapped_column(String(100), nullable=True, index=True)
    training_end_date: Mapped[Any] = mapped_column(Date, nullable=True)
    placement_status: Mapped[Any] = mapped_column(String(30), nullable=True)
    placement_date: Mapped[Any] = mapped_column(Date, nullable=True)
    consent_to_followup_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_recorded_at: Mapped[Any] = mapped_column(Date, nullable=True)
    consent_source: Mapped[Any] = mapped_column(String(30), nullable=True)
    notes: Mapped[Any] = mapped_column(Text, nullable=True)
    employer_claims: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
