"""Verification campaign and sampled-worker persistence models."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    campaign_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    programme_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("employers.company_id"), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(50), default="sampled_verification", nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="am", nullable=False)
    criteria: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CampaignWorker(Base):
    __tablename__ = "campaign_workers"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.campaign_id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), primary_key=True)
    call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="sampled", nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
