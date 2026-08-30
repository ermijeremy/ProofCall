"""The company thread: create, upload a list of people, talk, poll.

Responses are plain dictionaries rather than declared response models. A thread
carries questions the administrator invented ten seconds ago, categories the model
derived for that round alone, and counts keyed on those categories — none of it has
a fixed shape to declare, and inventing one per round would buy nothing.

Every request here is one of three things: start a thread, hand it a file, or hand
it a sentence. There is no endpoint for choosing people, scheduling, or dialing,
because all three are things the administrator says rather than buttons they press.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.batch import CompanyCreate, ThreadMessageCreate
from app.services.batch_service import (
    company_listing,
    company_thread,
    create_company,
    handle_message,
    handle_upload,
)

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
