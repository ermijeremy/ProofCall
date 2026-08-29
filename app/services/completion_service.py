"""Completed-call handoff from TeleExpert to Member A and persistence."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.contracts.integration import CompletedCallResult, WorkerEvidenceResult
from app.intelligence.interface import IntelligenceEngine
from app.intelligence.registry import get_engine
from app.models.call import TeleExpertCall
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.repositories.employers import EmployerRepository
from app.repositories.evidence import EvidenceRepository


def store_completed_result(db: Session, result: CompletedCallResult):
    call = CallRepository(db).get(result.call_id)
    if call is None:
        raise ValueError(f"Call not found: {result.call_id}")
    if call.worker_id != result.worker_id:
        raise ValueError("Completed result worker does not match the call worker")

    CallRepository(db).update(
        call,
        {
            "status": "completed",
            "transcript": result.transcript,
            "transcript_turns": result.transcript_turns,
            "language": result.language,
            "audio_url": result.audio_url,
            "completed_at": call.completed_at or datetime.utcnow(),
        },
    )
    return EvidenceRepository(db).upsert(
        {
            "worker_id": result.worker_id,
            "call_id": result.call_id,
            "consent": result.consent,
            "safeguarding_flag": result.safeguarding_flag,
            "clauses": {name: clause.model_dump() for name, clause in result.clauses.items()},
            "contradictions": [item.model_dump() for item in result.contradictions],
            "overall_verdict": result.overall_verdict,
        }
    )


def process_completed_call(
    db: Session,
    call_id: str,
    transcript: str,
    audio_url: str | None = None,
    transcript_turns: list[dict] | None = None,
    language: str | None = None,
    engine: IntelligenceEngine | None = None,
) -> CompletedCallResult:
    call = CallRepository(db).get(call_id)
    if call is None:
        raise ValueError(f"Call not found: {call_id}")

    beneficiary = BeneficiaryRepository(db).get(call.worker_id)
    if beneficiary is None:
        raise ValueError(f"Worker not found: {call.worker_id}")
    employer = EmployerRepository(db).get(beneficiary.company_id)
    if employer is None:
        raise ValueError(f"Company not found: {beneficiary.company_id}")
    active_engine = engine or get_engine()

    extracted = active_engine.extract_evidence(transcript, call.worker_id)
    evaluated: WorkerEvidenceResult = active_engine.evaluate_clauses(extracted)
    evaluated = active_engine.compare_with_employer(evaluated, beneficiary)
    result = CompletedCallResult(
        worker_id=call.worker_id,
        call_id=call.call_id,
        transcript=transcript,
        transcript_turns=transcript_turns or [],
        language=language,
        audio_url=audio_url,
        consent=evaluated.consent,
        clauses=evaluated.clauses,
        contradictions=evaluated.contradictions,
        safeguarding_flag=evaluated.safeguarding_flag,
        overall_verdict=evaluated.overall_verdict,
    )
    store_completed_result(db, result)
    return result
