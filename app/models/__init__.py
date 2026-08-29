"""Persistence models."""

from app.models.beneficiary import Beneficiary
from app.models.call import TeleExpertCall
from app.models.campaign import Campaign, CampaignWorker
from app.models.employer import Employer
from app.models.evidence import WorkerEvidence
from app.models.interview import InterviewSchedule

__all__ = [
    "Beneficiary",
    "Campaign",
    "CampaignWorker",
    "Employer",
    "TeleExpertCall",
    "WorkerEvidence",
    "InterviewSchedule",
]
