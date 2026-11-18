"""Beneficiary import and retrieval endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.uploads import BoundedUploadRoute, read_csv_upload
from app.db.session import get_db
from app.repositories.beneficiaries import BeneficiaryRepository
from app.schemas.beneficiary import BeneficiaryCreate, BeneficiaryRead
from app.services.import_service import import_beneficiaries_csv, import_beneficiary

router = APIRouter(prefix="/beneficiaries", tags=["beneficiaries"], route_class=BoundedUploadRoute)


@router.post("", response_model=BeneficiaryRead)
def create_beneficiary(data: BeneficiaryCreate, db: Session = Depends(get_db)) -> BeneficiaryRead:
    return import_beneficiary(db, data)


@router.get("", response_model=list[BeneficiaryRead])
def list_beneficiaries(db: Session = Depends(get_db)) -> list[BeneficiaryRead]:
    return BeneficiaryRepository(db).list()


@router.post("/import-csv")
async def import_beneficiaries(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    content = await read_csv_upload(file)
    try:
        return import_beneficiaries_csv(db, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
