"""Processed worker evidence persistence operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.models.evidence import WorkerEvidence
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
