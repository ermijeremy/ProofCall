"""Interview-batch persistence: the question set, its thread, and its answers.

A batch is one admin session: a roster imported from a CSV, a list of questions
typed into the thread, the people selected to be called, and the answers that
came back.

These tables sit beside :mod:`app.models.evidence` rather than replacing it. The
clause-based path stores a closed set of statuses in ``worker_evidence``; a batch
stores whatever the admin asked about, with a category the model derived for that
batch, which no closed literal can describe in advance.
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
    #: Ordered ``[{"index": int, "text": str, "slug": str}]``. Empty until the
    #: admin types the questions.
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    #: Pass-two output: ``{slug: {"categories": [...], "assignments": {...}}}``.
    categories: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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
    #: Set by code from a model-supplied label. An excluded record is counted nowhere.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exclusion_reason: Mapped[Any] = mapped_column(Text, nullable=True)
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
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exclusion_reason: Mapped[Any] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
