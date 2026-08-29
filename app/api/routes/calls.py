"""TeleExpert submission, status, and completion endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.integrations.teleexpert_client import TeleExpertError
from app.schemas.call import CallCreate, CallStatusRead
from app.services.call_service import cancel_call, submit_teleexpert_call, sync_call_status

router = APIRouter(prefix="/teleexpert", tags=["calls"])


@router.post("/calls", response_model=CallStatusRead, status_code=status.HTTP_202_ACCEPTED)
def submit_call(data: CallCreate, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return submit_teleexpert_call(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/calls/{call_id}", response_model=CallStatusRead)
def get_call(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return sync_call_status(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/calls/{call_id}/sync", response_model=CallStatusRead)
def sync_call(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return sync_call_status(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/calls/{call_id}/cancel", response_model=CallStatusRead)
def cancel(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return cancel_call(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
