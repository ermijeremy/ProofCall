"""Interview-batch persistence operations."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.batch import BatchMessage, BatchTarget, InterviewBatch, WorkerAnswerRecord, WorkerAnswers
from app.repositories.base import Repository


class BatchRepository(Repository[InterviewBatch]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, InterviewBatch)

    def newest_first(self) -> list[InterviewBatch]:
        statement = select(InterviewBatch).order_by(InterviewBatch.created_at.desc())
        return list(self.db.scalars(statement).all())

    def for_company(self, company_id: str) -> list[InterviewBatch]:
        statement = (
            select(InterviewBatch)
            .where(InterviewBatch.company_id == company_id)
            .order_by(InterviewBatch.created_at.desc())
        )
        return list(self.db.scalars(statement).all())

    def newest_for_company(self, company_id: str) -> InterviewBatch | None:
        """The round the thread is currently working on, if there is one."""

        rounds = self.for_company(company_id)
        return rounds[0] if rounds else None

    def due(self, moment: datetime, status: str) -> list[InterviewBatch]:
        """Scheduled rounds whose time has come, oldest first.

        Ordered oldest first so a backlog after a restart goes out in the order it
        was promised rather than newest-first.
        """

        statement = (
            select(InterviewBatch)
            .where(
                InterviewBatch.status == status,
                InterviewBatch.scheduled_at.is_not(None),
                InterviewBatch.scheduled_at <= moment,
            )
            .order_by(InterviewBatch.scheduled_at)
        )
        return list(self.db.scalars(statement).all())

    def update(self, batch: InterviewBatch, values: dict) -> InterviewBatch:
        for key, value in values.items():
            setattr(batch, key, value)
        self.db.commit()
        self.db.refresh(batch)
        return batch


class BatchTargetRepository(Repository[BatchTarget]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, BatchTarget)

    def for_batch(self, batch_id: str) -> list[BatchTarget]:
        statement = select(BatchTarget).where(BatchTarget.batch_id == batch_id)
        return list(self.db.scalars(statement).all())

    def by_call(self, call_id: str) -> BatchTarget | None:
        statement = select(BatchTarget).where(BatchTarget.call_id == call_id)
        return self.db.scalars(statement).first()

    def replace_unconfirmed(self, batch_id: str, worker_ids: list[str]) -> list[BatchTarget]:
        """Store a freshly drawn selection, keeping anyone already dialed.

        A second selection instruction before confirmation replaces the draw; a
        target that already has a call is left alone, because that call happened.
        """

        for target in self.for_batch(batch_id):
            if target.call_id is None and target.status == "selected":
                self.db.delete(target)
        self.db.flush()
        existing = {target.worker_id for target in self.for_batch(batch_id)}
        drawn = [
            BatchTarget(batch_id=batch_id, worker_id=worker_id)
            for worker_id in worker_ids
            if worker_id not in existing
        ]
        self.db.add_all(drawn)
        self.db.commit()
        return self.for_batch(batch_id)

    def update(self, target: BatchTarget, values: dict) -> BatchTarget:
        for key, value in values.items():
            setattr(target, key, value)
        self.db.commit()
        self.db.refresh(target)
        return target


class BatchMessageRepository(Repository[BatchMessage]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, BatchMessage)

    def for_company(self, company_id: str) -> list[BatchMessage]:
        """The whole thread for one company, oldest turn first.

        The thread is company-scoped rather than round-scoped: one company has one
        conversation, and a question like "how many have we not reached yet?" spans
        every round in it.
        """

        statement = (
            select(BatchMessage)
            .where(BatchMessage.company_id == company_id)
            .order_by(BatchMessage.created_at, BatchMessage.message_id)
        )
        return list(self.db.scalars(statement).all())

    def for_batch(self, batch_id: str) -> list[BatchMessage]:
        statement = (
            select(BatchMessage)
            .where(BatchMessage.batch_id == batch_id)
            .order_by(BatchMessage.created_at, BatchMessage.message_id)
        )
        return list(self.db.scalars(statement).all())


class WorkerAnswersRepository(Repository[WorkerAnswers]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, WorkerAnswers)

    def for_batch(self, batch_id: str) -> list[WorkerAnswers]:
        statement = select(WorkerAnswers).where(WorkerAnswers.batch_id == batch_id)
        return list(self.db.scalars(statement).all())

    def upsert(self, values: dict) -> WorkerAnswers:
        record = self.get((values["batch_id"], values["worker_id"]))
        if record is None:
            record = WorkerAnswers(**values)
            self.db.add(record)
        else:
            for key, value in values.items():
                setattr(record, key, value)
        self.db.commit()
        self.db.refresh(record)
        return record


class WorkerAnswerRecordRepository(Repository[WorkerAnswerRecord]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, WorkerAnswerRecord)

    def for_batch(self, batch_id: str) -> list[WorkerAnswerRecord]:
        statement = select(WorkerAnswerRecord).where(WorkerAnswerRecord.batch_id == batch_id).order_by(WorkerAnswerRecord.created_at)
        return list(self.db.scalars(statement).all())

    def by_call(self, call_id: str) -> WorkerAnswerRecord | None:
        statement = select(WorkerAnswerRecord).where(WorkerAnswerRecord.call_id == call_id)
        return self.db.scalars(statement).first()


__all__ = [
    "BatchMessageRepository",
    "BatchRepository",
    "BatchTargetRepository",
    "WorkerAnswersRepository",
    "WorkerAnswerRecordRepository",
]
