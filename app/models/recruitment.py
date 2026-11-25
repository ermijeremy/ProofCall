"""Recruitment demo records, independent of worker-verification evidence."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecruitmentCampaign(Base):
    __tablename__ = "recruitment_campaigns"

    campaign_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("employers.company_id"), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    assistant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assistant_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CompanyUser(Base):
    __tablename__ = "company_users"
    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("employers.company_id"), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CompanySession(Base):
    __tablename__ = "company_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("company_users.username"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "company_login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RecruitmentLead(Base):
    __tablename__ = "recruitment_leads"

    lead_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("recruitment_campaigns.campaign_id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_source: Mapped[str] = mapped_column(String(500), nullable=False)
    contact_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RecruitmentCall(Base):
    __tablename__ = "recruitment_calls"

    # One UUID identifies the intended call in the browser, database and provider.
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("recruitment_campaigns.campaign_id"), index=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("recruitment_leads.lead_id"), index=True)
    # A database constraint prevents two outstanding intentions for one lead.
    # Keep the claim while acceptance is unknown; release only on terminal state.
    active_lead_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="submitting", index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    campaign_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RecruitmentEvent(Base):
    __tablename__ = "recruitment_events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider_call_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
