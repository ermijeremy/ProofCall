"""Administrator interview scheduling endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.interview import InterviewScheduleBatchRead, InterviewScheduleRead, InterviewScheduleCreate
from app.services.interview_service import cancel_scheduled_interview, run_due_interviews, schedule_interviews

router = APIRouter(prefix="/interviews", tags=["interviews"])


@router.post("/schedule", response_model=InterviewScheduleBatchRead, status_code=status.HTTP_201_CREATED)
def schedule(data: InterviewScheduleCreate, db: Session = Depends(get_db)) -> InterviewScheduleBatchRead:
    try:
        return InterviewScheduleBatchRead(created=schedule_interviews(db, data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{schedule_id}/cancel", response_model=InterviewScheduleRead)
def cancel(schedule_id: str, db: Session = Depends(get_db)) -> InterviewScheduleRead:
    try:
        return cancel_scheduled_interview(db, schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run-due", response_model=list[InterviewScheduleRead])
def run_due(db: Session = Depends(get_db)) -> list[InterviewScheduleRead]:
    """Run due schedules once; production deployments can call this from a worker."""
    try:
        return run_due_interviews(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
