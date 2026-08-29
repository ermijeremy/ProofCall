"""Campaign creation and sampling endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.campaigns import CampaignRepository
from app.schemas.campaign import CampaignCreate, CampaignDetail, CampaignRead, CampaignWorkerRead
from app.services.campaign_service import create_campaign, get_campaign_workers

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignRead)
def create(data: CampaignCreate, db: Session = Depends(get_db)) -> CampaignRead:
    try:
        return create_campaign(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[CampaignRead])
def list_campaigns(db: Session = Depends(get_db)) -> list[CampaignRead]:
    return CampaignRepository(db).list()


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)) -> CampaignDetail:
    campaign = CampaignRepository(db).get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return CampaignDetail.model_validate({**campaign.__dict__, "workers": get_campaign_workers(db, campaign_id)})


@router.get("/{campaign_id}/workers", response_model=list[CampaignWorkerRead])
def list_sampled_workers(campaign_id: str, db: Session = Depends(get_db)) -> list[CampaignWorkerRead]:
    try:
        return get_campaign_workers(db, campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
