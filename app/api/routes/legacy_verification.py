"""Compatibility paths retained from the original backend API surface."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.aggregation_service import aggregate_company, aggregate_programme, worker_evidence_detail

router = APIRouter(tags=["verification"])


@router.get("/programmes/summary")
def programme_summary(db: Session = Depends(get_db)) -> dict:
    return aggregate_programme(db)


@router.get("/companies/{company_id}/verification")
def company_verification(company_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return aggregate_company(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workers/{worker_id}/evidence")
def evidence(worker_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return worker_evidence_detail(db, worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
