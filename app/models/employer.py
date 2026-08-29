"""Employer report persistence model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Employer(Base):
    __tablename__ = "employers"

    company_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reported_good_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_participation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

