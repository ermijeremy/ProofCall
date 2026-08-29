"""Employer and beneficiary import orchestration."""

import csv
import io
import json
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.employers import EmployerRepository
from app.schemas.beneficiary import BeneficiaryCreate
from app.schemas.employer import EmployerCreate


def import_employer(db: Session, data: EmployerCreate):
    return EmployerRepository(db).upsert(data.model_dump())


def import_beneficiary(db: Session, data: BeneficiaryCreate):
    return BeneficiaryRepository(db).upsert(data.model_dump())


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _csv_rows(content: bytes) -> Iterable[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file must include a header row")
    return reader


def import_employers_csv(db: Session, content: bytes) -> dict[str, Any]:
    records: list[EmployerCreate] = []
    for row_number, row in enumerate(_csv_rows(content), start=2):
        try:
            records.append(
                EmployerCreate(
                    company_id=row.get("company_id", "").strip(),
                    name=row.get("name", "").strip(),
                    reported_good_jobs=int(row.get("reported_good_jobs") or 0),
                    average_salary=_optional_int(row.get("average_salary")),
                    training_participation=_optional_int(row.get("training_participation")),
                    worker_count=_optional_int(row.get("worker_count")),
                )
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ValueError(f"Invalid employer CSV row {row_number}: {exc}") from exc

    saved = [import_employer(db, record) for record in records]
    return {"imported": len(saved), "company_ids": [item.company_id for item in saved]}


def import_beneficiaries_csv(db: Session, content: bytes) -> dict[str, Any]:
    records: list[BeneficiaryCreate] = []
    for row_number, row in enumerate(_csv_rows(content), start=2):
        raw_claims = row.get("employer_claims", "").strip()
        try:
            claims = json.loads(raw_claims) if raw_claims else {}
            if not isinstance(claims, dict):
                raise ValueError("employer_claims must be a JSON object")
            records.append(
                BeneficiaryCreate(
                    worker_id=row.get("worker_id", "").strip(),
                    company_id=row.get("company_id", "").strip(),
                    phone_number=row.get("phone_number", "").strip(),
                    preferred_language=(row.get("preferred_language") or "am").strip(),
                    employer_claims=claims,
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"Invalid beneficiary CSV row {row_number}: {exc}") from exc

    saved = [import_beneficiary(db, record) for record in records]
    return {"imported": len(saved), "worker_ids": [item.worker_id for item in saved]}
