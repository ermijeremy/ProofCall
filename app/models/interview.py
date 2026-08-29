"""Persisted interview requests created by an administrator."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InterviewSchedule(Base):
    __tablename__ = "interview_schedules"

    schedule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), nullable=False, index=True)
    campaign_id: Mapped[Any] = mapped_column(ForeignKey("campaigns.campaign_id"), nullable=True, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", nullable=False, index=True)
    call_id: Mapped[Any] = mapped_column(ForeignKey("teleexpert_calls.call_id"), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    answer_timeout_seconds: Mapped[int] = mapped_column(Integer, default=45, nullable=False)
    failure_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
