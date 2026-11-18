"""Employer report import and retrieval endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.uploads import BoundedUploadRoute, read_csv_upload
from app.db.session import get_db
from app.repositories.employers import EmployerRepository
from app.schemas.employer import EmployerCreate, EmployerRead
from app.services.import_service import import_employer, import_employers_csv

router = APIRouter(prefix="/employers", tags=["employers"], route_class=BoundedUploadRoute)


@router.post("", response_model=EmployerRead)
def create_employer(data: EmployerCreate, db: Session = Depends(get_db)) -> EmployerRead:
    return import_employer(db, data)


@router.get("", response_model=list[EmployerRead])
def list_employers(db: Session = Depends(get_db)) -> list[EmployerRead]:
    return EmployerRepository(db).list()


@router.post("/import-csv")
async def import_employers(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    content = await read_csv_upload(file)
    try:
        return import_employers_csv(db, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
