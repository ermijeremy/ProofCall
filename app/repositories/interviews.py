"""Interview schedule persistence operations."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.interview import InterviewSchedule
from app.repositories.base import Repository


class InterviewScheduleRepository(Repository[InterviewSchedule]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, InterviewSchedule)

    def for_worker(self, worker_id: str) -> list[InterviewSchedule]:
        statement = select(InterviewSchedule).where(InterviewSchedule.worker_id == worker_id).order_by(InterviewSchedule.scheduled_at)
        return list(self.db.scalars(statement).all())

    def due(self, now: datetime) -> list[InterviewSchedule]:
        statement = select(InterviewSchedule).where(
            InterviewSchedule.status == "scheduled",
            InterviewSchedule.scheduled_at <= now,
        ).order_by(InterviewSchedule.scheduled_at)
        return list(self.db.scalars(statement).all())

    def for_status(self, status: str) -> list[InterviewSchedule]:
        statement = select(InterviewSchedule).where(InterviewSchedule.status == status).order_by(InterviewSchedule.scheduled_at)
        return list(self.db.scalars(statement).all())
