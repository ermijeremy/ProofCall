"""Employer persistence operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employer import Employer
from app.repositories.base import Repository


class EmployerRepository(Repository[Employer]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Employer)

    def all_by_name(self) -> list[Employer]:
        """Every company, alphabetically. The front page is a list of these."""

        statement = select(Employer).order_by(Employer.name)
        return list(self.db.scalars(statement).all())

    def upsert(self, values: dict) -> Employer:
        employer = self.get(values["company_id"])
        if employer is None:
            employer = Employer(**values)
            self.db.add(employer)
        else:
            for key, value in values.items():
                setattr(employer, key, value)
        self.db.commit()
        self.db.refresh(employer)
        return employer
