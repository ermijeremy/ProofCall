"""TeleExpert call request, status, and returned-media persistence model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TeleExpertCall(Base):
    __tablename__ = "teleexpert_calls"

    call_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), nullable=False, index=True)
    campaign_id: Mapped[Any] = mapped_column(ForeignKey("campaigns.campaign_id"), nullable=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response_format: Mapped[str] = mapped_column(String(20), default="both", nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False, index=True)
    transcript: Mapped[Any] = mapped_column(Text, nullable=True)
    transcript_turns: Mapped[Any] = mapped_column(JSON, nullable=True)
    language: Mapped[Any] = mapped_column(String(20), nullable=True)
    audio_url: Mapped[Any] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
