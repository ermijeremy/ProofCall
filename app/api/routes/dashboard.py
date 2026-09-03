"""Dashboard page and aggregate-data endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.aggregation_service import aggregate_company, aggregate_programme, live_calls, worker_evidence_detail
from app.services.batch_service import company_listing

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("", include_in_schema=False)
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    """Render the current Callwise company listing at the familiar URL."""

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"companies": company_listing(db)},
    )


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return aggregate_programme(db)


@router.get("/live-calls")
def calls(db: Session = Depends(get_db)) -> list[dict]:
    return live_calls(db)


@router.get("/programme", include_in_schema=False)
def programme_page(request: Request, db: Session = Depends(get_db)):
    """The clause-path report. Kept reachable because that path still runs."""

    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={"summary": aggregate_programme(db)},
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
def worker_page(
    request: Request,
    worker_id: str,
    call_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        worker = worker_evidence_detail(db, worker_id, call_id)
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
