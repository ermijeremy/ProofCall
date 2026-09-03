"""Callwise validation-batch persistence.

A batch is one admin review round: a roster imported from CSV, the fixed KPI
questionnaire, selected workers, calls, and immutable answer snapshots. The
question set is stored for audit/export, but it is never authored by the chat
model or replaced by free-form administrator text.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InterviewBatch(Base):
    """One admin thread: a roster, a question set, and its results."""

    __tablename__ = "interview_batches"

    batch_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="Untitled batch", nullable=False)
    #: Ordered fixed Callwise KPI questionnaire.
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    #: Pass-two output: ``{slug: {"categories": [...], "assignments": {...}}}``.
    categories: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Deprecated roster compatibility field. New selection is persisted in
    #: ``batch_targets``; retained only while existing databases are migrated.
    roster_worker_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="am", nullable=False)
    #: draft -> ready -> selected -> (scheduled) -> calling -> complete
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)
    #: When the selected calls should be placed, in UTC. Null means "when the admin
    #: confirms". Stored in UTC and rendered back in the company's zone, because a
    #: naive local timestamp is ambiguous for exactly the two hours a year it
    #: matters most.
    scheduled_at: Mapped[Any] = mapped_column(DateTime, nullable=True, index=True)
    #: How many times to retry a call that does not connect. Defaults to the same
    #: two that :class:`~app.models.interview.InterviewSchedule` uses, and is said
    #: out loud in the thread whenever the admin did not choose it.
    retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    questionnaire_version: Mapped[str] = mapped_column(String(30), default="callwise-v1", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(30), default="callwise-prompt-v1", nullable=False)
    record_schema_version: Mapped[str] = mapped_column(String(30), default="callwise-record-v1", nullable=False)
    maximum_duration_seconds: Mapped[int] = mapped_column(Integer, default=360, nullable=False)
    retry_delay_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    source_checksum: Mapped[Any] = mapped_column(String(64), nullable=True)
    source_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_target_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spare_target_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BatchTarget(Base):
    """A worker drawn into one batch, and the call placed for them.

    Written before dialing, which is what makes a random draw auditable: it
    answers "why these ten?" after the fact. ``SystemRandom`` cannot be seeded,
    so the drawn list is the only record there is.
    """

    __tablename__ = "batch_targets"

    batch_id: Mapped[str] = mapped_column(ForeignKey("interview_batches.batch_id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), primary_key=True)
    call_id: Mapped[Any] = mapped_column(String(100), nullable=True, index=True)
    #: selected -> dialing -> completed | failed
    status: Mapped[str] = mapped_column(String(30), default="selected", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[Any] = mapped_column(DateTime, nullable=True, index=True)
    failure_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    is_primary_sample: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_spare: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BatchMessage(Base):
    """One turn in the thread, persisted so a reload restores the conversation.

    Scoped to the *company*, not to a round. There is one thread per company and
    many rounds inside it, so a message asking "how many haven't we reached yet?"
    spans every round and belongs to none of them.
    """

    __tablename__ = "batch_messages"

    message_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="")
    #: The round this turn concerns, when it concerns one. Null for anything said
    #: before the first question set exists, and for questions about the thread
    #: as a whole.
    batch_id: Mapped[Any] = mapped_column(
        ForeignKey("interview_batches.batch_id"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Structured card data for the message, rendered instead of plain text.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class WorkerAnswers(Base):
    """What one person answered, per question, for one batch."""

    __tablename__ = "worker_answers"

    batch_id: Mapped[str] = mapped_column(ForeignKey("interview_batches.batch_id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), primary_key=True)
    call_id: Mapped[Any] = mapped_column(String(100), nullable=True, index=True)
    #: ``{slug: {"value", "state", "confidence", "evidence", "category"}}``.
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language: Mapped[Any] = mapped_column(String(20), nullable=True)
    transcript: Mapped[Any] = mapped_column(Text, nullable=True)
    transcript_turns: Mapped[Any] = mapped_column(JSON, nullable=True)
    audio_url: Mapped[Any] = mapped_column(Text, nullable=True)
    disposition: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    good_job_annotation: Mapped[str] = mapped_column(String(30), default="UNCLEAR", nullable=False)
    kpi_clauses: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: The section-6 Callwise record for this call: nine clauses with the turn each
    #: rests on, the employment and training blocks, and the model's summary. Built
    #: once, when the interview is parsed, because it cites turn numbers in a
    #: transcript that a declined call deletes.
    callwise_record: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Set by code from a model-supplied label. An excluded record is counted nowhere.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exclusion_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    consent_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class WorkerAnswerRecord(Base):
    """Immutable answer snapshot for every completed batch call.

    ``WorkerAnswers`` is deliberately kept as the latest-result projection used
    for batch counts. This table preserves earlier calls when an administrator
    interviews the same worker again in the same batch.
    """

    __tablename__ = "worker_answer_records"

    answer_record_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("interview_batches.batch_id"), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("teleexpert_calls.call_id"), nullable=False, unique=True, index=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language: Mapped[Any] = mapped_column(String(20), nullable=True)
    transcript: Mapped[Any] = mapped_column(Text, nullable=True)
    transcript_turns: Mapped[Any] = mapped_column(JSON, nullable=True)
    audio_url: Mapped[Any] = mapped_column(Text, nullable=True)
    disposition: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    good_job_annotation: Mapped[str] = mapped_column(String(30), default="UNCLEAR", nullable=False)
    kpi_clauses: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: The section-6 Callwise record for this call, as built when it was parsed.
    callwise_record: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exclusion_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    consent_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class CallAttempt(Base):
    """Immutable operational history for every TeleExpert phone attempt."""

    __tablename__ = "call_attempts"

    attempt_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("interview_batches.batch_id"), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("beneficiaries.worker_id"), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("teleexpert_calls.call_id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_state: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    disposition: Mapped[Any] = mapped_column(String(30), nullable=True)
    failure_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Any] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
