"""One chat thread per company: a list of people, rounds of questions, and calls.

A company has one conversation. Inside it, each set of questions is a **round**
with its own selection, calls, and answers, and the list of people is the
company's — uploaded once and reused every round. That is the whole shape, and it
is what makes "call the ones we haven't reached yet" answerable in a second round
rather than a second thread.

Three rules shape the file, in order of how much damage breaking them does:

* **Nothing dials without an explicit confirmation, or a time the administrator
  agreed to.** Twenty interviews are twenty real telephones ringing and a call
  cannot be recalled. The selection is drawn, persisted, and echoed back by name
  first.
* **Code counts, the model narrates.** Every figure the administrator reads is
  computed in :mod:`app.intelligence.analysis` from stored answers. The model is
  handed the counts; it is never asked to produce one.
* **The model reads every message; code performs every action.** There is no
  keyword matching and no status branch tree left. :mod:`app.intelligence.agent`
  chooses one tool per turn and the executors below carry it out, validating what
  they were handed before touching anything.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.phone import to_e164
from app.integrations.teleexpert_client import TeleExpertError
from app.intelligence import agent, analysis, categorize as categorize_module, questions as questions_module
from app.intelligence.batch_engine import BatchIntelligence, build_batch_intelligence
from app.intelligence.providers.base import ProviderError
from app.models.batch import BatchMessage, BatchTarget, InterviewBatch, WorkerAnswers
from app.models.employer import Employer
from app.repositories.batches import (
    BatchMessageRepository,
    BatchRepository,
    BatchTargetRepository,
    WorkerAnswersRepository,
)
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.employers import EmployerRepository
from app.schemas.call import CallCreate
from app.services import schedule_rules
from app.services.call_service import submit_teleexpert_call
from app.services.import_service import _csv_rows

logger = logging.getLogger(__name__)

#: The company every legacy row belongs to. New companies get their own id; this
#: one exists because the demo database and the clause path already reference it.
DEFAULT_COMPANY_ID = "INTERNAL"
DEFAULT_COMPANY_NAME = "Internal workforce"

DRAFT = "draft"
READY = "ready"
SELECTED = "selected"
SCHEDULED = "scheduled"
CALLING = "calling"
COMPLETE = "complete"

#: What the front page says a thread is waiting on. Plain words, because the
#: administrator is being told what to do next, not shown an internal state name.
WAITING_ON = {
    DRAFT: "Waiting for your questions",
    READY: "Waiting for you to choose who to call",
    SELECTED: "Waiting for you to confirm the calls",
    SCHEDULED: "Calls are scheduled",
    CALLING: "Interviews are running",
    COMPLETE: "Answers are in",
}

TERMINAL_TARGET_STATUSES = frozenset({"completed", "failed"})

CONTACT_HEADERS = ("contact", "phone", "phone_number", "telephone", "number")
NAME_HEADERS = ("name", "full_name", "employee", "employee_name")

# -- thread plumbing ------------------------------------------------------- #


def _say(
    db: Session,
    company_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
    role: str = "system",
    batch_id: str | None = None,
) -> BatchMessage:
    """Append one turn to the company's thread and return it."""

    message = BatchMessage(
        message_id=f"msg_{uuid4().hex}",
        company_id=company_id,
        batch_id=batch_id,
        role=role,
        text=text,
        payload=payload or {},
    )
    return BatchMessageRepository(db).add(message)


OPENING_MESSAGE = (
    "Send me a CSV of the people to interview — one column of names, one of phone "
    "numbers — and the questions you want them asked. Nobody is called until you say so."
)


def create_company(db: Session, name: str | None = None) -> Employer:
    """Start a thread for a new company."""

    company = EmployerRepository(db).upsert(
        {
            "company_id": f"co_{uuid4().hex[:12]}",
            "name": (name or "New company").strip()[:255] or "New company",
        }
    )
    _say(db, company.company_id, OPENING_MESSAGE)
    return company


def ensure_company(db: Session, company_id: str = DEFAULT_COMPANY_ID) -> Employer:
    """The company row, created with its opening turn if it is not there yet."""

    repository = EmployerRepository(db)
    company = repository.get(company_id)
    if company is not None:
        return company
    company = repository.upsert({"company_id": company_id, "name": DEFAULT_COMPANY_NAME})
    _say(db, company_id, OPENING_MESSAGE)
    return company


def current_round(db: Session, company_id: str) -> InterviewBatch | None:
    """The round the thread is working on, or ``None`` before the first one."""

    return BatchRepository(db).newest_for_company(company_id)


def open_round(db: Session, company: Employer, title: str | None = None) -> InterviewBatch:
    """Start a round. Called when questions are set, never by a button."""

    existing = len(BatchRepository(db).for_company(company.company_id))
    batch = InterviewBatch(
        batch_id=f"batch_{uuid4().hex[:12]}",
        company_id=company.company_id,
        title=(title or f"Round {existing + 1}")[:255],
        language=_company_language(db, company),
        questions=[],
        categories={},
        status=DRAFT,
        retries=schedule_rules.DEFAULT_RETRIES,
    )
    return BatchRepository(db).add(batch)


def _company_language(db: Session, company: Employer) -> str:
    """The language the last round used, so a new round inherits it."""

    latest = current_round(db, company.company_id)
    return latest.language if latest is not None else "am"


# -- the list of people ---------------------------------------------------- #


def normalize_phone(raw: str) -> str:
    """A phone number reduced to what identifies the person behind it.

    Punctuation, spacing and the trunk prefix all vary between exports of the same
    list, so ``+251 911 000 001``, ``0911000001`` and ``911000001`` are one person.
    This is the key the import deduplicates on, which is what removed the need for
    per-round roster membership: the same number is the same employee, so a second
    upload updates the row instead of creating a second Abebe nobody can tell
    apart.

    It is deliberately the same function that decides what gets dialed
    (:func:`app.core.phone.to_e164`). Two different ideas of what makes a number
    the same number would mean a list that looks deduplicated and a telephone that
    rings twice.
    """

    return to_e164(raw)


def _header_value(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for key, value in row.items():
        if key and key.strip().lower().replace(" ", "_") in candidates:
            if value and value.strip():
                return value.strip()
    return ""


def import_company_csv(db: Session, company: Employer, content: bytes) -> dict[str, Any]:
    """Read a name/contact CSV into this company's list of people.

    Reuses ``_csv_rows`` so the UTF-8 BOM and the missing-header case behave the
    same as the existing imports. A row missing a name or a contact is reported
    rather than dropped silently, and a number already on file updates that person
    rather than adding a second copy of them.
    """

    rows = list(_csv_rows(content))
    headers = {(name or "").strip().lower().replace(" ", "_") for name in (rows[0].keys() if rows else [])}
    if rows and not headers & set(NAME_HEADERS):
        raise ValueError("the file needs a column of names")
    if rows and not headers & set(CONTACT_HEADERS):
        raise ValueError("the file needs a column of phone numbers")

    repository = BeneficiaryRepository(db)
    existing = {
        normalize_phone(person.phone_number): person
        for person in repository.for_company(company.company_id)
    }

    added: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    language = _company_language(db, company)

    for row_number, row in enumerate(rows, start=2):
        name = _header_value(row, NAME_HEADERS)
        contact = _header_value(row, CONTACT_HEADERS)
        if not name or not contact:
            skipped.append(f"row {row_number} has no name or no number")
            continue
        key = normalize_phone(contact)
        if not key:
            skipped.append(f"row {row_number}: {name} has no usable number")
            continue
        if key in seen:
            skipped.append(f"row {row_number}: {name} repeats {contact}")
            continue
        seen.add(key)

        found = existing.get(key)
        worker_id = found.worker_id if found else f"w_{uuid4().hex[:10]}"
        person = repository.upsert(
            {
                "worker_id": worker_id,
                "name": name,
                "company_id": company.company_id,
                # Stored in the form that can be dialed, not as typed: a number
                # the API would reject is not a contact detail, it is a call that
                # fails at three in the afternoon for no visible reason.
                "phone_number": key,
                "preferred_language": found.preferred_language if found else language,
                "employer_claims": found.employer_claims if found else {},
                "is_active": True,
            }
        )
        existing[key] = person
        entry = {"worker_id": worker_id, "name": name, "phone_number": key}
        (updated if found else added).append(entry)

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "total": len(repository.for_company(company.company_id)),
    }


def already_called(db: Session, company_id: str) -> set[str]:
    """Everyone this company has ever telephoned, across every round.

    Company-wide rather than per round, because "the ones we haven't reached yet"
    means exactly that in a thread that has run three rounds.
    """

    reached: set[str] = set()
    target_repository = BatchTargetRepository(db)
    for batch in BatchRepository(db).for_company(company_id):
        for target in target_repository.for_batch(batch.batch_id):
            if target.call_id is not None or target.status in TERMINAL_TARGET_STATUSES:
                reached.add(target.worker_id)
    return reached


def roster(db: Session, company_id: str, batch: InterviewBatch | None = None) -> list[dict[str, Any]]:
    """Everyone on the company's list, with what has happened to them.

    Exclusions and answers are read across every round: somebody excluded as a
    minor in round one must stay excluded in round four, and there is no version
    of that rule that should depend on which round is open.
    """

    answers: dict[str, WorkerAnswers] = {}
    answer_repository = WorkerAnswersRepository(db)
    for round_ in BatchRepository(db).for_company(company_id):
        for record in answer_repository.for_batch(round_.batch_id):
            previous = answers.get(record.worker_id)
            if previous is None or record.excluded or not previous.excluded:
                answers[record.worker_id] = record

    targets = (
        {target.worker_id: target for target in BatchTargetRepository(db).for_batch(batch.batch_id)}
        if batch is not None
        else {}
    )
    reached = already_called(db, company_id)

    people = []
    for person in BeneficiaryRepository(db).for_company(company_id):
        if not person.is_active:
            continue
        record = answers.get(person.worker_id)
        target = targets.get(person.worker_id)
        people.append(
            {
                "worker_id": person.worker_id,
                "name": person.name or person.worker_id,
                "phone_number": person.phone_number,
                "preferred_language": person.preferred_language,
                "selected": target is not None and target.status == "selected",
                "call_status": target.status if target else None,
                "call_id": target.call_id if target else None,
                "ever_called": person.worker_id in reached,
                "answered": record is not None,
                "excluded": bool(record.excluded) if record else False,
                "exclusion_reason": record.exclusion_reason if record else None,
            }
        )
    return sorted(people, key=lambda item: item["name"].casefold())


# -- records and counts ---------------------------------------------------- #


def records_for(db: Session, batch: InterviewBatch) -> list[dict[str, Any]]:
    """Stored answers in the shape the analysis and categorization modules take."""

    names = {
        person.worker_id: (person.name or person.worker_id)
        for person in BeneficiaryRepository(db).for_company(batch.company_id)
    }
    return [
        {
            "worker_id": record.worker_id,
            "name": names.get(record.worker_id, record.worker_id),
            "answers": dict(record.answers or {}),
            "consent": bool(record.consent),
            "excluded": bool(record.excluded),
            "exclusion_reason": record.exclusion_reason,
        }
        for record in WorkerAnswersRepository(db).for_batch(batch.batch_id)
    ]


def batch_summary(db: Session, batch: InterviewBatch) -> dict[str, Any]:
    """The counts for one round. Computed here, never by a model."""

    return analysis.summarize(batch.questions or [], records_for(db, batch), batch.categories or {})


def company_listing(db: Session) -> list[dict[str, Any]]:
    """One row per company for the front page.

    Deliberately shallow: the front page needs a name, a size, a last-active time,
    and what the thread is waiting on. Assembling every roster and every count to
    render it would read the whole database for a list of links.
    """

    message_repository = BatchMessageRepository(db)
    beneficiary_repository = BeneficiaryRepository(db)
    rows: list[dict[str, Any]] = []
    for company in EmployerRepository(db).all_by_name():
        messages = message_repository.for_company(company.company_id)
        batch = current_round(db, company.company_id)
        status = batch.status if batch is not None else DRAFT
        last = messages[-1] if messages else None
        rows.append(
            {
                "company_id": company.company_id,
                "name": company.name,
                "people": len([p for p in beneficiary_repository.for_company(company.company_id) if p.is_active]),
                "rounds": len(BatchRepository(db).for_company(company.company_id)),
                "status": status,
                "waiting_on": WAITING_ON.get(status, ""),
                "last_active": (last.created_at if last else company.created_at).isoformat(),
                "turns": len(messages),
            }
        )
    return sorted(rows, key=lambda row: row["last_active"], reverse=True)


def company_thread(db: Session, company_id: str) -> dict[str, Any]:
    """Everything the thread page and the JSON API need, in one read."""

    company = EmployerRepository(db).get(company_id)
    if company is None:
        raise ValueError(f"No such company: {company_id}")

    batch = current_round(db, company_id)
    people = roster(db, company_id, batch)
    targets = BatchTargetRepository(db).for_batch(batch.batch_id) if batch is not None else []
    return {
        "company_id": company.company_id,
        "name": company.name,
        "timezone": company.timezone,
        "clock_convention": company.clock_convention,
        "language": batch.language if batch is not None else "am",
        "status": batch.status if batch is not None else DRAFT,
        "waiting_on": WAITING_ON.get(batch.status if batch is not None else DRAFT, ""),
        "people": people,
        "rounds": len(BatchRepository(db).for_company(company_id)),
        "round": None
        if batch is None
        else {
            "batch_id": batch.batch_id,
            "title": batch.title,
            "status": batch.status,
            "questions": batch.questions or [],
            "scheduled_at": batch.scheduled_at.isoformat() if batch.scheduled_at else None,
            "retries": batch.retries,
            "selected": len([t for t in targets if t.status == "selected"]),
            "dialed": len([t for t in targets if t.call_id is not None]),
            "returned": len([t for t in targets if t.status in TERMINAL_TARGET_STATUSES]),
            "summary": batch_summary(db, batch),
        },
        "messages": [
            {
                "message_id": message.message_id,
                "role": message.role,
                "text": message.text,
                "payload": message.payload or {},
                "created_at": message.created_at.isoformat(),
            }
            for message in BatchMessageRepository(db).for_company(company_id)
        ],
    }


# -- questions ------------------------------------------------------------- #


def set_questions(
    db: Session,
    batch: InterviewBatch,
    typed: list[str],
    intelligence: BatchIntelligence | None = None,
) -> list[dict[str, Any]]:
    """Record the questions for a round, with age asked first regardless.

    Turning "what is ur wage?" into a sentence somebody can be asked down a
    telephone is a language job, so the model rewrites each question while code
    holds the count and the order fixed. What the administrator typed is kept
    beside the rewrite, so the thread can show both and nothing is changed behind
    their back.
    """

    parsed = [text.strip() for text in typed if text and text.strip()]
    if not parsed:
        return []
    refined = intelligence.refine_questions(parsed) if intelligence is not None else parsed
    question_set = questions_module.build_question_set(refined)
    BatchRepository(db).update(batch, {"questions": question_set})
    return question_set


# -- selection ------------------------------------------------------------- #


def select_targets(
    db: Session,
    batch: InterviewBatch,
    arguments: dict[str, Any],
) -> tuple[list[BatchTarget], dict[str, Any]]:
    """Resolve ``select_people`` arguments into a persisted, unconfirmed selection.

    The model named a shape; the draw happens in
    :func:`app.intelligence.selection.resolve`. Anything it could not resolve comes
    back in the resolution so the thread can say so, instead of quietly calling a
    smaller group than was asked for.
    """

    from app.intelligence import selection as selection_module

    spec = selection_module.normalize_spec(arguments)
    resolution = selection_module.resolve(
        spec,
        roster(db, batch.company_id, batch),
        already_called=already_called(db, batch.company_id),
    )
    if resolution["error"] or not resolution["selected"]:
        return [], resolution

    worker_ids = [person["worker_id"] for person in resolution["selected"]]
    targets = BatchTargetRepository(db).replace_unconfirmed(batch.batch_id, worker_ids)
    BatchRepository(db).update(batch, {"status": SELECTED, "scheduled_at": None})
    return targets, resolution


# -- dialing --------------------------------------------------------------- #


def dial_selection(
    db: Session,
    batch: InterviewBatch,
    intelligence: BatchIntelligence,
) -> dict[str, Any]:
    """Place one call per selected target. Only ever reached after a confirmation."""

    target_repository = BatchTargetRepository(db)
    people = {
        person.worker_id: person
        for person in BeneficiaryRepository(db).for_company(batch.company_id)
    }
    dialed: list[str] = []
    failed: list[str] = []

    for target in target_repository.for_batch(batch.batch_id):
        if target.call_id is not None or target.status != "selected":
            continue
        person = people.get(target.worker_id)
        if person is None:
            target_repository.update(target, {"status": "failed"})
            failed.append(target.worker_id)
            continue
        prompt = intelligence.interview_prompt(
            person, batch.questions or [], language=batch.language, programme_name=batch.title
        )
        try:
            call = submit_teleexpert_call(
                db,
                CallCreate(
                    worker_id=person.worker_id,
                    phone_number=person.phone_number,
                    prompt=prompt,
                    answer_timeout_seconds=settings.teleexpert_answer_timeout_seconds,
                ),
                idempotency_key=f"{batch.batch_id}:{person.worker_id}",
            )
        except (TeleExpertError, ValueError):
            logger.exception("Dialing failed for %s", person.worker_id)
            target_repository.update(target, {"status": "failed"})
            failed.append(person.name or person.worker_id)
            continue
        target_repository.update(target, {"call_id": call.call_id, "status": "dialing"})
        dialed.append(person.name or person.worker_id)

    BatchRepository(db).update(batch, {"status": CALLING, "scheduled_at": None})
    return {"dialed": dialed, "failed": failed}


def run_due_rounds(db: Session, intelligence: BatchIntelligence | None = None) -> list[str]:
    """Dial every scheduled round whose time has come, and say so in its thread.

    Hung off the poll that :class:`~app.services.scheduler_service.InterviewScheduler`
    already runs. One round failing must not stop the next, so each is wrapped:
    a provider that cannot build an interview prompt is this round's problem.
    """

    fired: list[str] = []
    for batch in BatchRepository(db).due(datetime.utcnow(), SCHEDULED):
        try:
            engine = intelligence or build_batch_intelligence()
            result = dial_selection(db, batch, engine)
        except Exception:  # noqa: BLE001 - one round must not take the poll down
            logger.exception("Scheduled round %s could not be dialed", batch.batch_id)
            BatchRepository(db).update(batch, {"status": SELECTED, "scheduled_at": None})
            _say(
                db,
                batch.company_id,
                "The scheduled calls could not be placed. Nobody was called, and the "
                "selection is still here — tell me to try again.",
                {"kind": "schedule_failed"},
                batch_id=batch.batch_id,
            )
            continue
        lines = [f"It is time, so I am calling {len(result['dialed'])} now."]
        if result["failed"]:
            lines.append("I could not get a call out to " + ", ".join(result["failed"]) + ".")
        _say(
            db,
            batch.company_id,
            " ".join(lines),
            {"kind": "dialing", **result},
            batch_id=batch.batch_id,
        )
        fired.append(batch.batch_id)
    return fired


# -- results --------------------------------------------------------------- #


def is_batch_call(db: Session, call_id: str) -> bool:
    """True when this call belongs to a round, so the clause path must not run it."""

    return BatchTargetRepository(db).by_call(call_id) is not None


def batch_result_pending(db: Session, call_id: str) -> bool:
    """True when a round's call has no answers stored against it yet.

    Asked of the answers table rather than of the target's status. A call that was
    wrongly recorded as failed — because the provider reported a status this code
    did not recognise, say — has a terminal target and no answers, and reading the
    status alone made that call permanently unprocessable: its transcript arrived
    a minute later and was discarded as already finished.
    """

    target = BatchTargetRepository(db).by_call(call_id)
    if target is None:
        return False
    return WorkerAnswersRepository(db).get((target.batch_id, target.worker_id)) is None


def process_batch_call(
    db: Session,
    call_id: str,
    transcript: str,
    language: str | None = None,
    intelligence: BatchIntelligence | None = None,
) -> WorkerAnswers:
    """Read one finished interview into answers, then finish the round if it can."""

    target_repository = BatchTargetRepository(db)
    target = target_repository.by_call(call_id)
    if target is None:
        raise ValueError(f"Call does not belong to a round: {call_id}")
    batch = BatchRepository(db).get(target.batch_id)
    if batch is None:
        raise ValueError(f"Round not found: {target.batch_id}")

    engine = intelligence or build_batch_intelligence()
    extraction = engine.extract_answers(transcript, target.worker_id, batch.questions or [])
    record = WorkerAnswersRepository(db).upsert(
        {
            "batch_id": batch.batch_id,
            "worker_id": target.worker_id,
            "call_id": call_id,
            "answers": extraction["answers"],
            "consent": extraction["consent"],
            "language": extraction["language"] or language,
            "transcript": transcript,
            "excluded": extraction["excluded"],
            "exclusion_reason": extraction["exclusion_reason"],
        }
    )
    target_repository.update(target, {"status": "completed"})

    person = BeneficiaryRepository(db).get(target.worker_id)
    who = person.name if person and person.name else target.worker_id
    if record.excluded:
        _say(
            db,
            batch.company_id,
            f"{who} is excluded and counted nowhere. {record.exclusion_reason}",
            {"kind": "excluded", "worker_id": target.worker_id},
            batch_id=batch.batch_id,
        )
    else:
        _say(
            db,
            batch.company_id,
            f"{who} answered.",
            {"kind": "answers", "worker_id": target.worker_id},
            batch_id=batch.batch_id,
        )
    finalize_if_complete(db, batch, engine)
    return record


def mark_call_failed(db: Session, call_id: str, reason: str | None = None) -> None:
    """Record a call that will never produce answers, so the round can finish."""

    target_repository = BatchTargetRepository(db)
    target = target_repository.by_call(call_id)
    if target is None or target.status in TERMINAL_TARGET_STATUSES:
        return
    target_repository.update(target, {"status": "failed"})
    batch = BatchRepository(db).get(target.batch_id)
    if batch is None:
        return
    person = BeneficiaryRepository(db).get(target.worker_id)
    who = person.name if person and person.name else target.worker_id
    _say(
        db,
        batch.company_id,
        f"The call to {who} did not go through. {reason or ''}".strip(),
        {"kind": "call_failed", "worker_id": target.worker_id},
        batch_id=batch.batch_id,
    )
    finalize_if_complete(db, batch, None)


def finalize_if_complete(
    db: Session,
    batch: InterviewBatch,
    intelligence: BatchIntelligence | None = None,
) -> bool:
    """Run pass two once every call has landed, and post the counts.

    Categorization happens once, across the whole round, so the buckets are the
    same for everybody and can therefore be counted. Excluded records are not
    sent: they are counted nowhere, so they must not shape the categories either.
    """

    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    if not targets or any(target.status not in TERMINAL_TARGET_STATUSES for target in targets):
        return False

    records = [record for record in records_for(db, batch) if not record["excluded"]]
    if records:
        engine = intelligence or build_batch_intelligence()
        categories = engine.categorize(batch.questions or [], records)
        categorize_module.apply_categories(records, categories)
        repository = WorkerAnswersRepository(db)
        for record in records:
            stored = repository.get((batch.batch_id, record["worker_id"]))
            if stored is not None:
                repository.upsert(
                    {
                        "batch_id": batch.batch_id,
                        "worker_id": record["worker_id"],
                        "answers": record["answers"],
                    }
                )
    else:
        categories = {}

    BatchRepository(db).update(batch, {"categories": categories, "status": COMPLETE})
    summary = batch_summary(db, batch)
    _say(
        db,
        batch.company_id,
        f"Every interview is back. {analysis.headline(summary)} Ask me anything about them.",
        {"kind": "summary", "summary": summary},
        batch_id=batch.batch_id,
    )
    return True


# -- the context the model reads ------------------------------------------- #


def build_context(db: Session, company: Employer) -> dict[str, Any]:
    """Everything the router is shown before it chooses an action.

    The roster is in here in full, with worker ids, which is what removes name
    matching from the code entirely: the model reads the list and returns ids. The
    current local time is in here because the model has no clock, and every count
    is in here already computed because the model must never work one out.
    """

    batch = current_round(db, company.company_id)
    people = roster(db, company.company_id, batch)
    notes: list[str] = []
    round_context: dict[str, Any] | None = None

    if batch is not None:
        targets = BatchTargetRepository(db).for_batch(batch.batch_id)
        selected = [target.worker_id for target in targets if target.status == "selected"]
        names = {person["worker_id"]: person["name"] for person in people}
        scheduled_local = None
        if batch.scheduled_at is not None:
            scheduled_local = schedule_rules.describe(
                batch.scheduled_at.replace(tzinfo=UTC),
                company.timezone or "UTC",
                batch.retries,
                defaulted=False,
            )
        round_context = {
            "batch_id": batch.batch_id,
            "status": batch.status,
            "questions": batch.questions or [],
            "selected": [names.get(worker_id, worker_id) for worker_id in selected],
            "scheduled_local": scheduled_local,
            "retries": batch.retries,
            "dialed": len([target for target in targets if target.call_id is not None]),
            "returned": len([t for t in targets if t.status in TERMINAL_TARGET_STATUSES]),
        }

    if not people:
        notes.append("No CSV has been uploaded yet, so there is nobody to call.")
    if batch is None or not batch.questions:
        notes.append("No questions have been set yet.")
    if not company.timezone:
        notes.append(
            "This company's timezone is unknown. Ask for it before scheduling anything."
        )
    if batch is not None and batch.status == SELECTED:
        notes.append(
            f"The default retry count is {schedule_rules.DEFAULT_RETRIES}; say it out loud "
            "if the administrator does not choose one."
        )

    return {
        "company": {
            "company_id": company.company_id,
            "name": company.name,
            "timezone": company.timezone,
            "clock_convention": company.clock_convention,
            "language": batch.language if batch is not None else "am",
        },
        "now_local": schedule_rules.describe_now(company.timezone),
        "roster": people,
        "round": round_context,
        "counts": (batch_summary(db, batch) if batch is not None and batch.questions else None),
        "notes": notes,
    }


# -- the tools, as code --------------------------------------------------- #


class Outcome:
    """What one tool did: a result for the model, a card, and whether to stop.

    Acting tools do not write their own chat message. They hand back a result, the
    loop feeds it to the model, and the model writes the sentence the
    administrator reads — which is the point of the rewrite, because the f-strings
    that used to write those sentences are what made the product feel like a form.
    """

    __slots__ = ("result", "card", "stop", "text", "batch_id")

    def __init__(
        self,
        result: dict[str, Any],
        *,
        card: dict[str, Any] | None = None,
        stop: bool = False,
        text: str | None = None,
        batch_id: str | None = None,
    ) -> None:
        self.result = result
        self.card = card
        self.stop = stop
        self.text = text
        self.batch_id = batch_id


def _tool_set_questions(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    typed = arguments.get("questions") or []
    if not typed:
        return Outcome({"ok": False, "error": "no questions were given"})

    batch = current_round(db, company.company_id)
    # A round that has already selected or called people is finished being edited.
    # Retyping questions then means a new round over the same list of people,
    # which is the whole reason the thread outlives the round.
    if batch is None or batch.status not in {DRAFT, READY}:
        batch = open_round(db, company)

    question_set = set_questions(db, batch, typed, engine)
    if not question_set:
        return Outcome({"ok": False, "error": "none of that parsed as a question"})

    people = roster(db, company.company_id, batch)
    BatchRepository(db).update(batch, {"status": READY if people else DRAFT})
    rewritten = [
        {"typed": item["original"], "asked": item["text"]}
        for item in question_set
        if item.get("original") and item["original"] != item["text"]
    ]
    return Outcome(
        {
            "ok": True,
            "questions": [item["text"] for item in question_set],
            "count": len(question_set),
            "age_asked_first": True,
            "rewritten": rewritten,
            "people_on_list": len(people),
        },
        card={"kind": "questions", "questions": question_set},
        batch_id=batch.batch_id,
    )


def _tool_select_people(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    if batch is None or not batch.questions:
        return Outcome({"ok": False, "error": "no questions have been set yet"})
    if batch.status in {CALLING}:
        return Outcome({"ok": False, "error": "calls from the last round are still running"})
    if not roster(db, company.company_id, batch):
        return Outcome({"ok": False, "error": "nobody is on the list yet"})

    targets, resolution = select_targets(db, batch, arguments)
    if not targets:
        return Outcome(
            {
                "ok": False,
                "error": resolution.get("error"),
                "requested": resolution.get("requested"),
                "available": resolution.get("available"),
                "unknown_ids": resolution.get("unknown_ids") or [],
            }
        )

    names = {person["worker_id"]: person["name"] for person in roster(db, company.company_id, batch)}
    selected = [
        names.get(target.worker_id, target.worker_id)
        for target in targets
        if target.status == "selected"
    ]
    return Outcome(
        {
            "ok": True,
            "count": len(selected),
            "selected": selected,
            "skipped_already_called": len(resolution.get("skipped_already_called") or []),
            "unknown_ids": resolution.get("unknown_ids") or [],
            "default_retries": schedule_rules.DEFAULT_RETRIES,
            "next": "nobody is called until the administrator confirms, or gives a time",
        },
        card={"kind": "selection", "selected": selected, "confirm": True},
        batch_id=batch.batch_id,
    )


def _tool_schedule_calls(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    if batch is None or batch.status not in {SELECTED, SCHEDULED}:
        return Outcome({"ok": False, "error": "nobody is selected, so there is nothing to schedule"})

    zone_name = arguments.get("timezone") or company.timezone
    retries, defaulted = schedule_rules.clean_retries(arguments.get("retries"))
    check = schedule_rules.validate(
        arguments.get("when_iso") or "",
        zone_name or "",
        allow_outside_window=bool(arguments.get("confirmed_outside_window")),
    )
    if not check["ok"]:
        return Outcome(
            {
                "ok": False,
                "error": check["error"],
                "now": schedule_rules.describe_now(company.timezone),
                "calling_window": schedule_rules.window_text(),
                "timezone_used": zone_name or None,
            }
        )

    updates: dict[str, Any] = {}
    if zone_name and zone_name != company.timezone:
        updates["timezone"] = zone_name
    convention = arguments.get("clock_convention")
    if convention in {"24h", "ethiopian"} and convention != company.clock_convention:
        updates["clock_convention"] = convention
    if updates:
        EmployerRepository(db).upsert({"company_id": company.company_id, **updates})

    at_utc = check["at_utc"]
    BatchRepository(db).update(
        batch,
        {"scheduled_at": at_utc.replace(tzinfo=None), "retries": retries, "status": SCHEDULED},
    )
    targets = [t for t in BatchTargetRepository(db).for_batch(batch.batch_id) if t.status == "selected"]
    when = schedule_rules.describe(at_utc, zone_name, retries, defaulted=defaulted)
    return Outcome(
        {
            "ok": True,
            "when": when,
            "retries": retries,
            "retries_were_defaulted": defaulted,
            "people": len(targets),
            "cancellable": "the administrator can cancel this in words until it fires",
        },
        card={
            "kind": "scheduled",
            "when": when,
            "people": len(targets),
            "retries": retries,
            "defaulted": defaulted,
        },
        batch_id=batch.batch_id,
    )


def _tool_ask_schedule(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    missing = [item for item in (arguments.get("missing") or []) if item in {"when", "timezone", "retries"}]
    return Outcome(
        {"ok": True, "asked": missing or ["when"]},
        card={
            "kind": "schedule_picker",
            "missing": missing or ["when"],
            "default_retries": schedule_rules.DEFAULT_RETRIES,
            "timezone": company.timezone,
            "calling_window": schedule_rules.window_text(),
        },
        stop=True,
        text=arguments.get("text") or "When should I place the calls?",
        batch_id=batch.batch_id if batch is not None else None,
    )


def _tool_dial_now(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    if batch is None or batch.status not in {SELECTED, SCHEDULED}:
        return Outcome({"ok": False, "error": "nobody is selected, so there is nobody to call"})

    result = dial_selection(db, batch, engine)
    return Outcome(
        {"ok": True, "dialed": result["dialed"], "failed": result["failed"], "count": len(result["dialed"])},
        card={"kind": "dialing", **result},
        batch_id=batch.batch_id,
    )


def _tool_cancel_selection(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    if batch is None or batch.status not in {SELECTED, SCHEDULED}:
        return Outcome({"ok": False, "error": "there is nothing waiting to be called"})

    cleared = len([t for t in BatchTargetRepository(db).for_batch(batch.batch_id) if t.status == "selected"])
    BatchTargetRepository(db).replace_unconfirmed(batch.batch_id, [])
    BatchRepository(db).update(batch, {"status": READY, "scheduled_at": None})
    return Outcome(
        {"ok": True, "cleared": cleared, "called_anybody": False},
        batch_id=batch.batch_id,
    )


def _tool_set_language(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    code = (arguments.get("code") or "").strip().lower()
    if code not in questions_module.LANGUAGE_NAMES:
        return Outcome(
            {
                "ok": False,
                "error": "that is not a language the interviews support",
                "supported": sorted(questions_module.LANGUAGE_NAMES),
            }
        )

    batch = current_round(db, company.company_id)
    if batch is not None:
        BatchRepository(db).update(batch, {"language": code})
    repository = BeneficiaryRepository(db)
    for person in repository.for_company(company.company_id):
        person.preferred_language = code
    db.commit()
    return Outcome(
        {"ok": True, "language": questions_module.LANGUAGE_NAMES[code], "code": code},
        batch_id=batch.batch_id if batch is not None else None,
    )


def _tool_speak(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    return Outcome(
        {"ok": True},
        stop=True,
        text=arguments.get("text") or "",
        batch_id=batch.batch_id if batch is not None else None,
    )


Executor = Callable[[Session, Employer, dict[str, Any], BatchIntelligence], Outcome]

EXECUTORS: dict[str, Executor] = {
    "set_questions": _tool_set_questions,
    "select_people": _tool_select_people,
    "ask_schedule": _tool_ask_schedule,
    "schedule_calls": _tool_schedule_calls,
    "dial_now": _tool_dial_now,
    "cancel_selection": _tool_cancel_selection,
    "set_language": _tool_set_language,
    "answer": _tool_speak,
    "ask": _tool_speak,
}

#: Tools that change something. Each may fire at most once per message: a model
#: that keeps selecting people would otherwise loop against a paid API, and each
#: repeat would replace the previous draw.
ACTING_TOOLS = frozenset(
    {"set_questions", "select_people", "schedule_calls", "dial_now", "cancel_selection", "set_language"}
)


# -- the single input box -------------------------------------------------- #


def handle_upload(
    db: Session,
    company_id: str,
    content: bytes,
    filename: str | None = None,
) -> list[BatchMessage]:
    """Read an uploaded CSV into the company's list. The one path with no model.

    A file is not a sentence, so there is nothing here for a model to interpret:
    the columns are named, the rows are counted, and both are reported exactly.
    """

    company = ensure_company(db, company_id)
    _say(db, company_id, f"Uploaded {filename or 'people.csv'}", role="admin")
    try:
        result = import_company_csv(db, company, content)
    except ValueError as exc:
        return [_say(db, company_id, f"I could not read that file — {exc}.")]

    batch = current_round(db, company_id)
    lines = [f"{result['total']} on the list."]
    if result["added"] and result["updated"]:
        lines.append(f"{len(result['added'])} new, {len(result['updated'])} already there and updated.")
    elif result["updated"] and not result["added"]:
        lines.append("All of them were already on the list, so I updated their details.")
    if result["skipped"]:
        lines.append(f"{len(result['skipped'])} row(s) left out: " + "; ".join(result["skipped"]) + ".")
    lines.append(
        "Now send me the questions."
        if batch is None or not batch.questions
        else "Tell me who to call."
    )

    if batch is not None and batch.questions and batch.status == DRAFT:
        BatchRepository(db).update(batch, {"status": READY})

    return [
        _say(
            db,
            company_id,
            " ".join(lines),
            {
                "kind": "roster",
                "people": result["added"] + result["updated"],
                "skipped": result["skipped"],
                "total": result["total"],
            },
            batch_id=batch.batch_id if batch is not None else None,
        )
    ]


def _fallback_text(steps: list[dict[str, Any]]) -> str:
    """What to say when the model acted three times and never spoke.

    Deliberately dull and factual. It is a backstop for a confused model, not a
    voice the product has — if this text is ever seen twice the cap is the bug.
    """

    done = [step["name"].replace("_", " ") for step in steps if step["result"].get("ok")]
    if not done:
        return "I could not do that. Tell me again in your own words."
    return "Done: " + ", ".join(done) + "."


def handle_message(
    db: Session,
    company_id: str,
    text: str,
    intelligence: BatchIntelligence | None = None,
) -> list[BatchMessage]:
    """The one input box. The model reads the message and code carries it out.

    Up to :data:`app.intelligence.agent.MAX_STEPS` tool calls per message, each fed
    back to the model, so "call Abebe and schedule it for tomorrow at 3" is one
    message and two actions. Acting tools return facts; the model writes the
    sentence, and the deterministic card is attached to it.
    """

    company = ensure_company(db, company_id)
    if not text or not text.strip():
        raise ValueError("Type something first")

    _say(db, company_id, text.strip(), role="admin")

    try:
        engine = intelligence or build_batch_intelligence()
    except ProviderError as exc:
        logger.exception("No usable model provider")
        return [_say(db, company_id, f"I cannot reach the language model right now. {exc}")]

    history = [
        {"role": message.role, "text": message.text}
        for message in BatchMessageRepository(db).for_company(company_id)
    ]

    steps: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    used: set[str] = set()
    spoken: Outcome | None = None

    for _ in range(agent.MAX_STEPS):
        try:
            call = engine.decide(build_context(db, company), history, steps)
        except ProviderError as exc:
            # Not a canned reply. A transport failure reading as "I could not tell
            # who you meant" is the bug that started this rewrite: the
            # administrator retypes a clear instruction and gets the same answer.
            logger.exception("Router failed for %s", company_id)
            return [
                _say(
                    db,
                    company_id,
                    f"I could not reach the language model, so I have done nothing. {exc}",
                    {"kind": "model_error"},
                )
            ]

        name = call["name"]
        if name in ACTING_TOOLS and name in used:
            steps.append(
                {
                    "name": name,
                    "arguments": call["arguments"],
                    "result": {"ok": False, "error": f"{name} has already run for this message"},
                    "signature": call.get("signature"),
                }
            )
            continue

        outcome = EXECUTORS[name](db, company, call["arguments"], engine)
        if name in ACTING_TOOLS:
            used.add(name)
        if outcome.card:
            cards.append(outcome.card)
        steps.append(
            {
                "name": name,
                "arguments": call["arguments"],
                "result": outcome.result,
                # Carried so the next step can replay this call the way the
                # provider signed it. Never persisted: it is valid only inside
                # this one conversation with the model.
                "signature": call.get("signature"),
            }
        )
        if outcome.stop:
            spoken = outcome
            break

    reply_text = (spoken.text if spoken and spoken.text else "").strip() or _fallback_text(steps)
    payload: dict[str, Any] = {}
    if len(cards) == 1:
        payload = cards[0]
    elif cards:
        payload = {"kind": "cards", "cards": cards}

    batch_id = next(
        (
            outcome_batch
            for outcome_batch in [spoken.batch_id if spoken else None]
            if outcome_batch
        ),
        None,
    ) or (current_round(db, company_id).batch_id if current_round(db, company_id) else None)

    return [_say(db, company_id, reply_text, payload, batch_id=batch_id)]


__all__ = [
    "ACTING_TOOLS",
    "CALLING",
    "COMPLETE",
    "DEFAULT_COMPANY_ID",
    "DEFAULT_COMPANY_NAME",
    "DRAFT",
    "EXECUTORS",
    "READY",
    "SCHEDULED",
    "SELECTED",
    "TERMINAL_TARGET_STATUSES",
    "WAITING_ON",
    "already_called",
    "batch_summary",
    "batch_result_pending",
    "build_context",
    "company_listing",
    "company_thread",
    "create_company",
    "current_round",
    "dial_selection",
    "ensure_company",
    "finalize_if_complete",
    "handle_message",
    "handle_upload",
    "import_company_csv",
    "is_batch_call",
    "mark_call_failed",
    "normalize_phone",
    "open_round",
    "process_batch_call",
    "records_for",
    "roster",
    "run_due_rounds",
    "select_targets",
    "set_questions",
]
