"""Worker, company, and programme verification endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.aggregation_service import aggregate_company, aggregate_programme, worker_evidence_detail

router = APIRouter(prefix="/verification", tags=["verification"])


@router.get("/programme")
def programme_summary(db: Session = Depends(get_db)) -> dict:
    return aggregate_programme(db)


@router.get("/companies/{company_id}")
def company_verification(company_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return aggregate_company(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workers/{worker_id}")
def evidence(worker_id: str, call_id: str | None = Query(default=None), db: Session = Depends(get_db)) -> dict:
    try:
        return worker_evidence_detail(db, worker_id, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
