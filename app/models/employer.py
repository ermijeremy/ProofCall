"""Employer report persistence model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Employer(Base):
    __tablename__ = "employers"

    company_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reported_good_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_salary: Mapped[Any] = mapped_column(Integer, nullable=True)
    training_participation: Mapped[Any] = mapped_column(Integer, nullable=True)
    worker_count: Mapped[Any] = mapped_column(Integer, nullable=True)
    job_positions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    gender_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    age_band_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
