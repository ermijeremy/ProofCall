"""TeleExpert call persistence operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.call import TeleExpertCall
from app.repositories.base import Repository


class CallRepository(Repository[TeleExpertCall]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, TeleExpertCall)

    def for_worker(self, worker_id: str) -> list[TeleExpertCall]:
        statement = select(TeleExpertCall).where(TeleExpertCall.worker_id == worker_id)
        return list(self.db.scalars(statement).all())

    def with_status(self, status: str) -> list[TeleExpertCall]:
        statement = select(TeleExpertCall).where(TeleExpertCall.status == status)
        return list(self.db.scalars(statement).all())

    def update(self, call: TeleExpertCall, values: dict) -> TeleExpertCall:
        for key, value in values.items():
            setattr(call, key, value)
        self.db.commit()
        self.db.refresh(call)
        return call
