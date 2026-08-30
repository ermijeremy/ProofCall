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
    #: IANA zone, e.g. ``Africa/Addis_Ababa``. Empty until the model has asked.
    #:
    #: A company sits in one place, so this belongs here rather than on a round:
    #: asked once per thread and never again. The model has no clock of its own, so
    #: "tomorrow at 3" cannot be resolved to an instant without it, and guessing
    #: telephones twenty people at the wrong hour.
    timezone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: ``24h`` or ``ethiopian``. Empty until known.
    #:
    #: Not pedantry: the numbers are ``+251`` and the interviews are in Amharic,
    #: where "three o'clock" spoken colloquially is 9:00 AM. One field is cheaper
    #: than a class of scheduling bug nobody would think to look for.
    clock_convention: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
