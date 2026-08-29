"""Completed-call processing and Member A evidence handoff endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.contracts.integration import CompletedCallResult
from app.db.session import get_db
from app.intelligence.registry import get_engine
from app.schemas.evidence import TranscriptCompletion
from app.services.completion_service import process_completed_call, store_completed_result

router = APIRouter(prefix="/calls", tags=["evidence"])


@router.post("/process-completed", response_model=CompletedCallResult)
def process_member_a_result(result: CompletedCallResult, db: Session = Depends(get_db)) -> CompletedCallResult:
    try:
        store_completed_result(db, result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{call_id}/process", response_model=CompletedCallResult)
def process_transcript(
    call_id: str,
    data: TranscriptCompletion,
    db: Session = Depends(get_db),
) -> CompletedCallResult:
    try:
        return process_completed_call(
            db,
            call_id,
            data.transcript,
            data.audio_url,
            data.transcript_turns,
            data.language,
            get_engine(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
