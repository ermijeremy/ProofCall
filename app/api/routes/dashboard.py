"""Dashboard page and aggregate-data endpoints."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.aggregation_service import aggregate_company, aggregate_programme, live_calls, worker_evidence_detail

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return aggregate_programme(db)


@router.get("/live-calls")
def calls(db: Session = Depends(get_db)) -> list[dict]:
    return live_calls(db)


@router.get("", include_in_schema=False)
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={"summary": aggregate_programme(db)},
    )


@router.get("/setup", include_in_schema=False)
def setup_page(request: Request):
    demo_path = Path(__file__).resolve().parents[3] / "data" / "demo_workers.json"
    demo_workers = json.loads(demo_path.read_text(encoding="utf-8")) if demo_path.exists() else []
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"demo_workers": demo_workers},
    )


@router.get("/companies/{company_id}", include_in_schema=False)
def company_page(request: Request, company_id: str, db: Session = Depends(get_db)):
    try:
        company = aggregate_company(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="company.html",
        context={"company": company},
    )


@router.get("/workers/{worker_id}", include_in_schema=False)
def worker_page(request: Request, worker_id: str, db: Session = Depends(get_db)):
    try:
        worker = worker_evidence_detail(db, worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="worker.html",
        context={"worker": worker},
    )


@router.get("/calls", include_in_schema=False)
def live_calls_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="live_calls.html",
        context={"calls": live_calls(db)},
    )
