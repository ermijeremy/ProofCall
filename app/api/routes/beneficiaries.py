"""Beneficiary import and retrieval endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.beneficiaries import BeneficiaryRepository
from app.schemas.beneficiary import BeneficiaryCreate, BeneficiaryRead
from app.services.import_service import import_beneficiaries_csv, import_beneficiary

router = APIRouter(prefix="/beneficiaries", tags=["beneficiaries"])


@router.post("", response_model=BeneficiaryRead)
def create_beneficiary(data: BeneficiaryCreate, db: Session = Depends(get_db)) -> BeneficiaryRead:
    return import_beneficiary(db, data)


@router.get("", response_model=list[BeneficiaryRead])
def list_beneficiaries(db: Session = Depends(get_db)) -> list[BeneficiaryRead]:
    return BeneficiaryRepository(db).list()


@router.post("/import-csv")
async def import_beneficiaries(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    try:
        return import_beneficiaries_csv(db, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
