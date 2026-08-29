"""Campaign and sampled-worker persistence operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignWorker
from app.repositories.base import Repository


class CampaignRepository(Repository[Campaign]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Campaign)

    def workers(self, campaign_id: str) -> list[CampaignWorker]:
        statement = select(CampaignWorker).where(CampaignWorker.campaign_id == campaign_id)
        return list(self.db.scalars(statement).all())

    def add_worker(self, worker: CampaignWorker) -> CampaignWorker:
        self.db.add(worker)
        self.db.commit()
        self.db.refresh(worker)
        return worker

    def save_sample(self, campaign: Campaign, workers: list[CampaignWorker]) -> Campaign:
        self.db.add(campaign)
        self.db.add_all(workers)
        campaign.status = "sampled"
        self.db.commit()
        self.db.refresh(campaign)
        return campaign
