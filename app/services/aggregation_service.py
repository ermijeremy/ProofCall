"""Company and programme-level aggregation."""

from typing import Any

from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.repositories.campaigns import CampaignRepository
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.repositories.employers import EmployerRepository
from app.repositories.evidence import EvidenceRepository


def _counts(results: list[Any]) -> dict[str, int]:
    return {
        "worker_confirmed": sum(r.overall_verdict in {"CONFIRMED_GOOD_JOB", "MET"} for r in results),
        "not_confirmed": sum(r.overall_verdict == "NOT_CONFIRMED" for r in results),
        "unclear": sum(r.overall_verdict == "UNCLEAR" for r in results),
        "material_contradictions": sum(
            any(item.get("material", False) for item in (r.contradictions or [])) for r in results
        ),
        "stopped": sum(r.overall_verdict == "STOPPED" for r in results),
        "safeguarding_flags": sum(bool(r.safeguarding_flag) for r in results),
    }


def aggregate_company(db: Session, company_id: str) -> dict[str, Any]:
    employer = EmployerRepository(db).get(company_id)
    if employer is None:
        raise ValueError(f"Company not found: {company_id}")
    beneficiaries = BeneficiaryRepository(db).for_company(company_id)
    evidence = EvidenceRepository(db).for_company(company_id)
    calls = [call for call in CallRepository(db).list() if call.worker_id in {w.worker_id for w in beneficiaries}]
    counts = _counts(evidence)
    completed = sum(call.status == "completed" for call in calls)
    return {
        "company_id": company_id,
        "company_name": employer.name,
        "employer_claimed": employer.reported_good_jobs,
        **counts,
        "completed_interviews": completed,
        "response_rate": round((completed / max(1, len(beneficiaries))) * 100, 2),
        "verification_gap": employer.reported_good_jobs - counts["worker_confirmed"],
        "contradictions": [
            {"worker_id": result.worker_id, "contradictions": result.contradictions}
            for result in evidence
            if result.contradictions
        ],
    }


def aggregate_programme(db: Session) -> dict[str, Any]:
    employers = EmployerRepository(db).list()
    beneficiaries = BeneficiaryRepository(db).list()
    evidence = EvidenceRepository(db).list()
    calls = CallRepository(db).list()
    campaigns = CampaignRepository(db).list()
    counts = _counts(evidence)
    completed = sum(call.status == "completed" for call in calls)
    return {
        "employer_claimed": sum(item.reported_good_jobs for item in employers),
        **counts,
        "completed_interviews": completed,
        "response_rate": round((completed / max(1, len(beneficiaries))) * 100, 2),
        "companies": len(employers),
        "beneficiaries": len(beneficiaries),
        "criteria": campaigns[0].criteria if campaigns else {},
        "language": campaigns[0].language if campaigns else "am",
    }


def worker_evidence_detail(db: Session, worker_id: str) -> dict[str, Any]:
    worker = BeneficiaryRepository(db).get(worker_id)
    evidence = EvidenceRepository(db).get(worker_id)
    if worker is None or evidence is None:
        raise ValueError(f"Worker evidence not found: {worker_id}")
    calls = CallRepository(db).for_worker(worker_id)
    call = next((item for item in calls if item.call_id == evidence.call_id), calls[-1] if calls else None)
    return {
        "worker_id": worker_id,
        "company_id": worker.company_id,
        "preferred_language": worker.preferred_language,
        "call_id": evidence.call_id,
        "call_status": call.status if call else "completed",
        "transcript": call.transcript if call else None,
        "audio_url": call.audio_url if call else None,
        "consent": evidence.consent,
        "safeguarding_flag": evidence.safeguarding_flag,
        "clauses": evidence.clauses,
        "contradictions": evidence.contradictions,
        "overall_verdict": evidence.overall_verdict,
    }


def live_calls(db: Session) -> list[dict[str, Any]]:
    calls = CallRepository(db).list()
    workers = {worker.worker_id: worker for worker in BeneficiaryRepository(db).list()}
    return [
        {
            "worker_id": call.worker_id,
            "call_id": call.call_id,
            "phone_number": workers[call.worker_id].phone_number if call.worker_id in workers else None,
            "status": call.status,
            "created_at": call.created_at,
            "failure_reason": call.failure_reason,
        }
        for call in calls
    ]
