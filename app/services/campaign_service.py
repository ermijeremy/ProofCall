"""Campaign creation and beneficiary sampling orchestration."""

from secrets import SystemRandom
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignWorker
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.employers import EmployerRepository
from app.schemas.campaign import CampaignCreate

random_source = SystemRandom()


def sample_workers(db: Session, company_id: str, sample_size: int) -> list[str]:
    beneficiaries = [item for item in BeneficiaryRepository(db).for_company(company_id) if item.is_active]
    requested = len(beneficiaries) if sample_size == 0 else sample_size
    if requested > len(beneficiaries):
        raise ValueError(
            f"Requested sample of {requested}, but only {len(beneficiaries)} active beneficiaries exist"
        )
    return [item.worker_id for item in random_source.sample(beneficiaries, requested)]


def create_campaign(db: Session, data: CampaignCreate) -> Campaign:
    if EmployerRepository(db).get(data.company_id) is None:
        raise ValueError(f"Company not found: {data.company_id}")

    worker_ids = sample_workers(db, data.company_id, data.sample_size)
    campaign = Campaign(
        campaign_id=f"campaign_{uuid4().hex}",
        programme_name=data.programme_name,
        company_id=data.company_id,
        strategy=data.strategy,
        sample_size=len(worker_ids),
        language=data.language,
        criteria=data.criteria,
    )
    sampled_workers = [
        CampaignWorker(campaign_id=campaign.campaign_id, worker_id=worker_id)
        for worker_id in worker_ids
    ]
    return CampaignRepository(db).save_sample(campaign, sampled_workers)


def get_campaign_workers(db: Session, campaign_id: str) -> list[CampaignWorker]:
    if CampaignRepository(db).get(campaign_id) is None:
        raise ValueError(f"Campaign not found: {campaign_id}")
    return CampaignRepository(db).workers(campaign_id)
