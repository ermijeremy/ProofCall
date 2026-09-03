"""The company thread: create, upload a list of people, talk, poll.

Responses are plain dictionaries rather than declared response models. A thread
carries questions the administrator invented ten seconds ago, categories the model
derived for that round alone, and counts keyed on those categories — none of it has
a fixed shape to declare, and inventing one per round would buy nothing.

Every request here is one of three things: start a thread, hand it a file, or hand
it a sentence. There is no endpoint for choosing people, scheduling, or dialing,
because all three are things the administrator says rather than buttons they press.
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.batch import InterviewBatch
from app.models.employer import Employer
from app.schemas.batch import CompanyCreate, CompanySettingsUpdate, RoundDial, RoundSelection, ThreadMessageCreate
from app.services.batch_service import (
    company_listing,
    company_thread,
    create_company,
    handle_message,
    handle_upload,
    open_round,
    select_targets,
    dial_selection,
    current_round,
    set_questions,
    DRAFT,
    READY,
    COMPLETE,
    SELECTED,
)
from app.intelligence.batch_engine import build_batch_intelligence
from app.repositories.batches import BatchTargetRepository, BatchRepository
from app.repositories.beneficiaries import BeneficiaryRepository
from app.services.reporting_service import csv_bytes, report, worker_rows, xlsx_bytes

router = APIRouter(prefix="/companies", tags=["companies"])


def _messages(items) -> list[dict]:
    return [
        {
            "message_id": message.message_id,
            "role": message.role,
            "text": message.text,
            "payload": message.payload or {},
            "created_at": message.created_at.isoformat(),
        }
        for message in items
    ]


@router.post("")
def create(data: CompanyCreate, db: Session = Depends(get_db)) -> dict:
    company = create_company(db, name=data.name)
    return {"company_id": company.company_id, "name": company.name}


@router.get("")
def list_companies(db: Session = Depends(get_db)) -> list[dict]:
    return company_listing(db)


@router.get("/{company_id}")
def get_company(company_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return company_thread(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{company_id}/settings")
def update_settings(company_id: str, data: CompanySettingsUpdate, db: Session = Depends(get_db)) -> dict:
    company = db.get(Employer, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    db.commit()
    return {
        "company_id": company.company_id,
        "timezone": company.timezone,
        "clock_convention": company.clock_convention,
        "minimum_wage_etb": company.minimum_wage_etb,
        "small_cell_threshold": company.small_cell_threshold,
    }


@router.get("/{company_id}/rounds/{batch_id}/export")
def export_round(
    company_id: str,
    batch_id: str,
    format: str = Query(default="json", pattern="^(json|csv|xlsx)$"),
    db: Session = Depends(get_db),
) -> Response:
    """Export one Callwise round from the company-scoped product API."""
    batch = db.get(InterviewBatch, batch_id)
    if batch is None or batch.company_id != company_id:
        raise HTTPException(status_code=404, detail="Interview round not found")
    try:
        data = report(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"callproof-{batch_id}-report"
    if format == "csv":
        data["worker_rows"] = worker_rows(db, batch_id)
        return Response(csv_bytes(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    if format == "xlsx":
        data["worker_rows"] = worker_rows(db, batch_id)
        return Response(xlsx_bytes(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'})
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.post("/{company_id}/upload")
async def upload_people(
    company_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    replies = handle_upload(db, company_id, await file.read(), file.filename)
    return {"messages": _messages(replies), "thread": company_thread(db, company_id)}


@router.post("/{company_id}/messages")
def post_message(company_id: str, data: ThreadMessageCreate, db: Session = Depends(get_db)) -> dict:
    try:
        replies = handle_message(db, company_id, data.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"messages": _messages(replies), "thread": company_thread(db, company_id)}


@router.post("/{company_id}/rounds/prepare")
def prepare_round(company_id: str, db: Session = Depends(get_db)) -> dict:
    """Prepare the fixed questionnaire without routing through the chat model."""
    company = db.get(Employer, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    batch = current_round(db, company_id)
    if batch is None or batch.status == COMPLETE:
        batch = open_round(db, company)
    set_questions(db, batch, [])
    has_people = bool(BeneficiaryRepository(db).for_company(company_id))
    BatchRepository(db).update(batch, {"status": READY if has_people else DRAFT})
    return {"thread": company_thread(db, company_id)}


@router.post("/{company_id}/rounds/select")
def select_round_workers(company_id: str, data: RoundSelection, db: Session = Depends(get_db)) -> dict:
    """Persist an explicit worker selection from the guided UI."""
    company = db.get(Employer, company_id)
    batch = current_round(db, company_id)
    if company is None or batch is None or not batch.questions:
        raise HTTPException(status_code=400, detail="Prepare the questionnaire first")
    # A worker can be called again, but a batch target is immutable once it has
    # a provider call. Start a fresh round so the new call gets its own target,
    # answer projection, and immutable history row instead of replacing the old
    # call or being skipped by the dialer.
    current_targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    if batch.status == COMPLETE or any(target.call_id for target in current_targets):
        batch = open_round(db, company)
        set_questions(db, batch, [])
        BatchRepository(db).update(batch, {"status": READY})
    targets, resolution = select_targets(db, batch, {"mode": "explicit", "worker_ids": data.worker_ids})
    if not targets:
        raise HTTPException(status_code=400, detail=resolution.get("error") or "No workers selected")
    return {"thread": company_thread(db, company_id), "selected": data.worker_ids}


@router.post("/{company_id}/rounds/dial")
def dial_round(company_id: str, data: RoundDial, db: Session = Depends(get_db)) -> dict:
    """Confirm and place the selected calls; this is the only dialing action."""
    company = db.get(Employer, company_id)
    batch = current_round(db, company_id)
    if company is None or batch is None or batch.status != SELECTED:
        raise HTTPException(status_code=400, detail="Review and select workers first")
    BatchRepository(db).update(batch, {"retries": data.retries})
    result = dial_selection(db, batch, build_batch_intelligence())
    return {"thread": company_thread(db, company_id), "result": result}
