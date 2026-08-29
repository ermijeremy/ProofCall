"""Beneficiary persistence operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.repositories.base import Repository


class BeneficiaryRepository(Repository[Beneficiary]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Beneficiary)

    def for_company(self, company_id: str) -> list[Beneficiary]:
        statement = select(Beneficiary).where(Beneficiary.company_id == company_id)
        return list(self.db.scalars(statement).all())

    def upsert(self, values: dict) -> Beneficiary:
        beneficiary = self.get(values["worker_id"])
        if beneficiary is None:
            beneficiary = Beneficiary(**values)
            self.db.add(beneficiary)
        else:
            for key, value in values.items():
                setattr(beneficiary, key, value)
        self.db.commit()
        self.db.refresh(beneficiary)
        return beneficiary
