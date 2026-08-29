"""Administrator interview selection and scheduling."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.interview import InterviewSchedule
from app.contracts.integration import ProgrammeContext
from app.intelligence.interface import IntelligenceEngine
from app.intelligence.registry import get_engine
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.interviews import InterviewScheduleRepository
from app.schemas.call import CallCreate
from app.schemas.interview import InterviewScheduleCreate
from app.services.call_service import submit_teleexpert_call


def schedule_interviews(db: Session, data: InterviewScheduleCreate) -> list[InterviewSchedule]:
    worker_ids = list(dict.fromkeys(data.worker_ids))
    beneficiaries = BeneficiaryRepository(db)
    campaign = CampaignRepository(db).get(data.campaign_id) if data.campaign_id else None
    if data.campaign_id and campaign is None:
        raise ValueError(f"Campaign not found: {data.campaign_id}")

    created = []
    for worker_id in worker_ids:
        worker = beneficiaries.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker not found: {worker_id}")
        if not worker.is_active:
            raise ValueError(f"Worker is inactive: {worker_id}")

        # Language is deliberately resolved by the system, never supplied by the admin.
        language = worker.preferred_language or (campaign.language if campaign else "am")
        created.append(
            InterviewSchedule(
                schedule_id=f"schedule_{uuid4().hex}",
                worker_id=worker.worker_id,
                campaign_id=data.campaign_id,
                scheduled_at=data.scheduled_at,
                language=language,
                retries=data.retries,
                retry_delay_seconds=data.retry_delay_seconds,
                answer_timeout_seconds=data.answer_timeout_seconds,
            )
        )

    db.add_all(created)
    db.commit()
    for item in created:
        db.refresh(item)
    return created


def cancel_scheduled_interview(db: Session, schedule_id: str) -> InterviewSchedule:
    schedule = InterviewScheduleRepository(db).get(schedule_id)
    if schedule is None:
        raise ValueError(f"Schedule not found: {schedule_id}")
    if schedule.status != "scheduled":
        raise ValueError(f"Only scheduled interviews can be cancelled: {schedule_id}")
    schedule.status = "cancelled"
    db.commit()
    db.refresh(schedule)
    return schedule


def run_due_interviews(
    db: Session,
    engine: IntelligenceEngine | None = None,
    now: datetime | None = None,
) -> list[InterviewSchedule]:
    """Submit due schedules and persist their resulting TeleExpert call IDs.

    A schedule is marked ``processing`` and committed before the external call
    so a second scheduler process cannot submit the same interview at once.
    """
    active_engine = engine or get_engine()
    schedules = InterviewScheduleRepository(db).due(now or datetime.utcnow())
    processed: list[InterviewSchedule] = []
    workers = BeneficiaryRepository(db)
    campaigns = CampaignRepository(db)

    for schedule in schedules:
        schedule.status = "processing"
        db.commit()
        db.refresh(schedule)
        try:
            worker = workers.get(schedule.worker_id)
            if worker is None:
                raise ValueError(f"Worker not found: {schedule.worker_id}")
            campaign = campaigns.get(schedule.campaign_id) if schedule.campaign_id else None
            programme = ProgrammeContext(
                programme_name=campaign.programme_name if campaign else "CallProof verification",
                language=schedule.language,
                criteria=campaign.criteria if campaign else {},
            )
            prompt = active_engine.generate_interview_prompt(worker, programme)
            call = submit_teleexpert_call(
                db,
                CallCreate(
                    worker_id=worker.worker_id,
                    campaign_id=schedule.campaign_id,
                    phone_number=worker.phone_number,
                    prompt=prompt,
                    retries=schedule.retries,
                    retry_delay_seconds=schedule.retry_delay_seconds,
                    answer_timeout_seconds=schedule.answer_timeout_seconds,
                ),
                idempotency_key=f"callproof-interview-{schedule.schedule_id}",
            )
            schedule.status = "submitted"
            schedule.call_id = call.call_id
            db.commit()
            db.refresh(schedule)
            processed.append(schedule)
        except Exception as exc:
            schedule.status = "failed"
            schedule.failure_reason = str(exc)[:1000]
            db.commit()
            db.refresh(schedule)
            processed.append(schedule)
    return processed
