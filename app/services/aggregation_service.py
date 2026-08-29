"""Company and programme-level aggregation."""

from typing import Any

from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.repositories.campaigns import CampaignRepository
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.repositories.employers import EmployerRepository
from app.repositories.evidence import EvidenceRepository

CRITERIA = (
    "age", "working_hours", "employment_duration", "salary",
    "training_participation", "forced_labour", "discrimination",
    "freedom_of_association",
)


def _criterion_breakdown(results: list[Any]) -> dict[str, dict[str, int]]:
    statuses = ("MET", "NOT_MET", "UNCLEAR", "REFUSED", "NOT_ASKED", "STOPPED")
    breakdown = {criterion: {status: 0 for status in statuses} for criterion in CRITERIA}
    for result in results:
        for name, clause in (result.clauses or {}).items():
            breakdown.setdefault(name, {status: 0 for status in statuses})
            status = clause.get("status", "UNCLEAR")
            breakdown[name].setdefault(status, 0)
            breakdown[name][status] += 1
    return breakdown


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


def _company_workers(
    beneficiaries: list[Beneficiary],
    evidence: list[Any],
    calls: list[Any],
) -> list[dict[str, Any]]:
    evidence_by_worker = {result.worker_id: result for result in evidence}
    calls_by_worker: dict[str, list[Any]] = {}
    for call in calls:
        calls_by_worker.setdefault(call.worker_id, []).append(call)

    workers = []
    for worker in beneficiaries:
        worker_calls = sorted(
            calls_by_worker.get(worker.worker_id, []),
            key=lambda item: item.created_at or 0,
            reverse=True,
        )
        last_call = worker_calls[0] if worker_calls else None
        result = evidence_by_worker.get(worker.worker_id)
        workers.append(
            {
                "worker_id": worker.worker_id,
                "phone_number": worker.phone_number,
                "preferred_language": worker.preferred_language,
                "employer_claims": worker.employer_claims or {},
                "call_status": last_call.status if last_call else "not_scheduled",
                "last_call_at": (last_call.completed_at or last_call.updated_at or last_call.created_at) if last_call else None,
                "overall_verdict": result.overall_verdict if result else None,
                "safeguarding_flag": bool(result.safeguarding_flag) if result else False,
                "evidence_available": result is not None,
            }
        )
    return workers


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
        "criteria_breakdown": _criterion_breakdown(evidence),
        "employer_fields": {
            "job_positions": employer.job_positions,
            "gender_breakdown": employer.gender_breakdown,
            "age_band_breakdown": employer.age_band_breakdown,
            "average_salary": employer.average_salary,
            "training_participation": employer.training_participation,
        },
        "workers": _company_workers(beneficiaries, evidence, calls),
    }


def aggregate_programme(db: Session) -> dict[str, Any]:
    employers = EmployerRepository(db).list()
    beneficiaries = BeneficiaryRepository(db).list()
    evidence = EvidenceRepository(db).list()
    calls = CallRepository(db).list()
    campaigns = CampaignRepository(db).list()
    counts = _counts(evidence)
    completed = sum(call.status == "completed" for call in calls)
    company_overviews = [aggregate_company(db, employer.company_id) for employer in employers]
    recent_calls = live_calls(db)[-8:]
    return {
        "employer_claimed": sum(item.reported_good_jobs for item in employers),
        **counts,
        "completed_interviews": completed,
        "response_rate": round((completed / max(1, len(beneficiaries))) * 100, 2),
        "companies": len(employers),
        "beneficiaries": len(beneficiaries),
        "criteria": campaigns[0].criteria if campaigns else {},
        "language": campaigns[0].language if campaigns else "am",
        "company_overviews": company_overviews,
        "recent_calls": recent_calls,
        "criteria_breakdown": _criterion_breakdown(evidence),
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
            "completed_at": call.completed_at,
            "retries": call.retries,
            "retry_delay_seconds": call.retry_delay_seconds,
            "language": call.language or (workers[call.worker_id].preferred_language if call.worker_id in workers else None),
            "failure_reason": call.failure_reason,
        }
        for call in calls
    ]
