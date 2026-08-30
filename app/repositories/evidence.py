"""Processed worker evidence persistence operations."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.models.evidence import WorkerEvidence, WorkerEvidenceRecord
from app.repositories.base import Repository


class EvidenceRepository(Repository[WorkerEvidence]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, WorkerEvidence)

    def for_company(self, company_id: str) -> list[WorkerEvidence]:
        statement = (
            select(WorkerEvidence)
            .join(Beneficiary, Beneficiary.worker_id == WorkerEvidence.worker_id)
            .where(Beneficiary.company_id == company_id)
        )
        return list(self.db.scalars(statement).all())

    def upsert(self, values: dict) -> WorkerEvidence:
        evidence = self.get(values["worker_id"])
        if evidence is None:
            evidence = WorkerEvidence(**values)
            self.db.add(evidence)
        else:
            for key, value in values.items():
                setattr(evidence, key, value)
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def append_record(self, values: dict) -> WorkerEvidenceRecord:
        """Append one call result, ignoring only an already-recorded call."""
        existing = self.db.scalar(
            select(WorkerEvidenceRecord).where(WorkerEvidenceRecord.call_id == values["call_id"])
        )
        if existing is not None:
            return existing
        record = WorkerEvidenceRecord(evidence_id=uuid4().hex, **values)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def history_for_worker(self, worker_id: str) -> list[WorkerEvidenceRecord]:
        statement = (
            select(WorkerEvidenceRecord)
            .where(WorkerEvidenceRecord.worker_id == worker_id)
            .order_by(WorkerEvidenceRecord.processed_at.desc(), WorkerEvidenceRecord.evidence_id.desc())
        )
        return list(self.db.scalars(statement).all())
