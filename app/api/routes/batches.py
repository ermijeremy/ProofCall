"""The interview-batch thread: create, upload, talk, poll.

Responses are plain dictionaries rather than declared response models. The thread
carries a question set the admin invented ten seconds ago, categories the model
derived for this batch alone, and counts keyed on those categories — none of that
has a fixed shape to declare, and inventing one per batch would buy nothing.
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.batches import BatchRepository
from app.schemas.batch import BatchCreate, BatchMessageCreate, FixtureReplayCreate
from app.services.batch_service import (
    batch_detail,
    create_batch,
    handle_message,
    handle_upload,
    debug_snapshot,
    replay_fixture,
)
from app.services.reporting_service import csv_bytes, report, worker_csv_bytes, worker_report, xlsx_bytes

router = APIRouter(prefix="/batches", tags=["batches"])


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
def create(data: BatchCreate, db: Session = Depends(get_db)) -> dict:
    batch = create_batch(db, title=data.title, language=data.language)
    return {"batch_id": batch.batch_id, "title": batch.title, "status": batch.status}


@router.get("")
def list_batches(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "batch_id": batch.batch_id,
            "title": batch.title,
            "status": batch.status,
            "language": batch.language,
            "questions": len(batch.questions or []),
            "created_at": batch.created_at.isoformat(),
        }
        for batch in BatchRepository(db).newest_first()
    ]


@router.get("/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return batch_detail(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{batch_id}/report")
def get_report(batch_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return report(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{batch_id}/debug")
def debug(batch_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return debug_snapshot(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{batch_id}/export")
def export_report(
    batch_id: str,
    format: str = Query(default="json", pattern="^(json|csv|xlsx)$"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        data = report(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"callproof-{batch_id}-report"
    if format == "csv":
        return Response(csv_bytes(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    if format == "xlsx":
        return Response(xlsx_bytes(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'})
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.get("/{batch_id}/workers/{worker_id}/export")
def export_worker(
    batch_id: str,
    worker_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        data = worker_report(db, batch_id, worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"callproof-{batch_id}-{worker_id}"
    if format == "csv":
        return Response(worker_csv_bytes(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.post("/{batch_id}/upload")
async def upload_roster(
    batch_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    try:
        replies = handle_upload(db, batch_id, await file.read(), file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"messages": _messages(replies), "batch": batch_detail(db, batch_id)}


@router.post("/{batch_id}/messages")
def post_message(batch_id: str, data: BatchMessageCreate, db: Session = Depends(get_db)) -> dict:
    try:
        replies = handle_message(db, batch_id, data.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"messages": _messages(replies), "batch": batch_detail(db, batch_id)}


@router.post("/{batch_id}/replay-fixture")
def replay(batch_id: str, data: FixtureReplayCreate, db: Session = Depends(get_db)) -> dict:
    try:
        replay_fixture(db, batch_id, data.worker_id, data.fixture)
        return {"status": "processed", "batch": batch_detail(db, batch_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
