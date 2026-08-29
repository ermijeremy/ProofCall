"""Seed the deterministic 20-worker hackathon demonstration dataset."""

import json
from pathlib import Path

from app.contracts.integration import CompletedCallResult
from app.db.session import SessionLocal, init_db
from app.models.employer import Employer
from app.schemas.beneficiary import BeneficiaryCreate
from app.schemas.call import CallCreate
from app.schemas.campaign import CampaignCreate
from app.services.call_service import submit_teleexpert_call
from app.services.campaign_service import create_campaign
from app.services.completion_service import store_completed_result
from app.services.import_service import import_beneficiary


def seed() -> None:
    init_db()
    workers = json.loads((Path(__file__).parents[1] / "data" / "demo_workers.json").read_text())
    db = SessionLocal()
    try:
        db.merge(
            Employer(
                company_id="ABC",
                name="ABC Construction",
                reported_good_jobs=20,
                average_salary=8500,
                training_participation=18,
                worker_count=20,
            )
        )
        db.commit()
        for worker in workers:
            import_beneficiary(db, BeneficiaryCreate(**worker))

        campaign = create_campaign(
            db,
            CampaignCreate(
                programme_name="Decent Jobs Cohort A",
                company_id="ABC",
                sample_size=20,
                language="am",
                criteria={
                    "minimum_age": 15,
                    "minimum_weekly_hours": 20,
                    "minimum_duration_months": 6,
                },
            ),
        )
        for index, worker in enumerate(workers[:19]):
            call = submit_teleexpert_call(
                db,
                CallCreate(
                    worker_id=worker["worker_id"],
                    campaign_id=campaign.campaign_id,
                    phone_number=worker["phone_number"],
                    prompt="Synthetic decent-work verification interview.",
                ),
            )
            if index < 14:
                verdict = "CONFIRMED_GOOD_JOB"
            elif index < 16:
                verdict = "NOT_CONFIRMED"
            elif index < 18:
                verdict = "UNCLEAR"
            else:
                verdict = "STOPPED"
            store_completed_result(
                db,
                CompletedCallResult(
                    worker_id=worker["worker_id"],
                    call_id=call.call_id,
                    transcript="Synthetic demonstration transcript.",
                    transcript_turns=[{"speaker": "worker", "text": "Synthetic fixture"}],
                    language="am",
                    consent=True,
                    clauses={
                        "age": {
                            "value": 14 if verdict == "STOPPED" else 19,
                            "status": "STOPPED" if verdict == "STOPPED" else "MET",
                            "confidence": "HIGH",
                            "evidence": "Synthetic fixture",
                        }
                    },
                    contradictions=(
                        [{
                            "type": "employment_status",
                            "description": "Employer and worker disagree",
                            "material": True,
                        }]
                        if verdict == "NOT_CONFIRMED" else []
                    ),
                    safeguarding_flag=verdict == "STOPPED",
                    overall_verdict=verdict,
                ),
            )
        print(f"Seeded {len(workers)} workers and 19 completed demonstration calls.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
