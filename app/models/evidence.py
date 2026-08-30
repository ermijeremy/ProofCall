"""Structured evidence returned by Member A for a completed call."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerEvidence(Base):
    __tablename__ = "worker_evidence"

    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("teleexpert_calls.call_id"), nullable=False, index=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    safeguarding_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clauses: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    contradictions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    overall_verdict: Mapped[str] = mapped_column(String(30), default="UNCLEAR", nullable=False, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class WorkerEvidenceRecord(Base):
    """Immutable evidence result for one completed call.

    ``WorkerEvidence`` remains the latest-result projection used by the
    dashboard aggregation. This table is the audit history: every distinct
    completed call gets its own row and is never replaced by a later call.
    """

    __tablename__ = "worker_evidence_records"

    evidence_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("teleexpert_calls.call_id"), nullable=False, unique=True, index=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    safeguarding_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clauses: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    contradictions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    overall_verdict: Mapped[str] = mapped_column(String(30), default="UNCLEAR", nullable=False, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
