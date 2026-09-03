from __future__ import annotations

from typing import Any

from app.models.employer import Employer
from app.repositories.beneficiaries import BeneficiaryRepository
from app.services import batch_service
from app.services.reporting_service import csv_bytes, report, worker_rows, xlsx_bytes


class OfflineCallwiseEngine:
    name = "offline-test"

    def extract_answers(self, transcript: str, worker_id: str, question_set: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "worker_id": worker_id,
            "consent": True,
            "language": "am",
            "answers": {
                question["slug"]: {
                    "value": "30" if question["slug"] == "age_years" else "stated answer",
                    "state": "STATED",
                    "confidence": "HIGH",
                    "evidence": "30" if question["slug"] == "age_years" else "stated answer",
                }
                for question in question_set
            },
            "excluded": False,
            "exclusion_reason": None,
            "interview_stopped": False,
        }

    def categorize(self, question_set: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            question["slug"]: {
                "categories": ["Reported answer"],
                "assignments": {record["worker_id"]: "Reported answer" for record in records},
            }
            for question in question_set
        }


def test_fixed_questionnaire_completion_is_stored_and_exported(db) -> None:
    company = Employer(company_id="co_callwise", name="beSingularity")
    db.add(company)
    db.commit()
    worker = BeneficiaryRepository(db).upsert(
        {
            "worker_id": "w_callwise",
            "name": "Test Worker",
            "company_id": company.company_id,
            "phone_number": "+251900000001",
            "preferred_language": "am",
            "employer_claims": {},
        }
    )
    batch = batch_service.open_round(db, company)
    targets, resolution = batch_service.select_targets(
        db, batch, {"mode": "explicit", "worker_ids": [worker.worker_id]}
    )
    assert resolution["selected"]
    targets[0].call_id = "callwise-test-call"
    targets[0].status = "dialing"
    targets[0].attempts = 1
    db.commit()

    batch_service.process_batch_call(
        db,
        "callwise-test-call",
        "assistant: consent\ncaller: 30\nassistant: stated answer",
        "am",
        "https://teleexpert.invalid/audio.wav",
        [{"role": "caller", "text": "30", "offset_seconds": 1}],
        OfflineCallwiseEngine(),
    )

    payload = report(db, batch.batch_id)
    assert len(payload["questions"]) == 16
    assert payload["summary"]["interviews_counted"] == 1
    assert payload["progress"]["returned"] == 1
    rows = worker_rows(db, batch.batch_id)
    csv = csv_bytes({**payload, "worker_rows": rows}).decode("utf-8-sig")
    assert "Person,Contact" in csv
    assert "Test Worker,+251900000001" in csv
    workbook = xlsx_bytes({**payload, "worker_rows": rows})
    assert workbook.startswith(b"PK")
