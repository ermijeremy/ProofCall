"""One admin thread: a roster, a question set, a selection, calls, and a chat.

This is the batch path end to end. It reuses the machinery that already works —
``_csv_rows`` for the upload, ``submit_teleexpert_call`` for dialing, the
provider layer for every model call — and adds the parts a question set needs
that a fixed clause set did not.

Two rules shape the whole file:

* **Nothing dials without an explicit confirmation.** Twenty interviews are
  twenty real telephones ringing, and a call cannot be recalled. The selection is
  drawn, persisted, and echoed back by name; only a clear yes places the calls.
* **Code counts, the model narrates.** Every figure the admin reads is computed
  in :mod:`app.intelligence.analysis` from stored answers. The model is given the
  counts; it is never asked to produce them.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.teleexpert_client import TeleExpertError
from app.intelligence import analysis, categorize as categorize_module, questions as questions_module
from app.intelligence.batch_engine import BatchIntelligence, build_batch_intelligence
from app.models.batch import BatchMessage, BatchTarget, InterviewBatch, WorkerAnswers
from app.repositories.batches import (
    BatchMessageRepository,
    BatchRepository,
    BatchTargetRepository,
    WorkerAnswersRepository,
)
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.employers import EmployerRepository
from app.schemas.call import CallCreate
from app.services.call_service import submit_teleexpert_call
from app.services.import_service import _csv_rows

logger = logging.getLogger(__name__)

#: The platform is an internal tool for one company, so a batch does not ask
#: which employer it belongs to. The row exists because beneficiaries reference it.
DEFAULT_COMPANY_ID = "INTERNAL"
DEFAULT_COMPANY_NAME = "Internal workforce"

DRAFT = "draft"
READY = "ready"
SELECTED = "selected"
CALLING = "calling"
COMPLETE = "complete"

TERMINAL_TARGET_STATUSES = frozenset({"completed", "failed"})

#: Only a clear affirmative dials. Anything else is treated as a new instruction
#: or a question, because guessing here places calls nobody asked for.
CONFIRMATIONS = frozenset(
    {
        "yes", "y", "yeah", "yep", "ok", "okay", "confirm", "confirmed", "go",
        "go ahead", "do it", "proceed", "call", "call them", "call them now",
        "yes please", "start", "start calling", "አዎ", "እሺ",
    }
)
DECLINES = frozenset(
    {"no", "n", "nope", "cancel", "stop", "abort", "not yet", "wait", "hold on", "አይ"}
)

CONTACT_HEADERS = ("contact", "phone", "phone_number", "telephone", "number")
NAME_HEADERS = ("name", "full_name", "employee", "employee_name")


# -- thread plumbing ------------------------------------------------------- #


def _say(
    db: Session,
    batch_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
    role: str = "system",
) -> BatchMessage:
    """Append one turn to the thread and return it."""

    message = BatchMessage(
        message_id=f"msg_{uuid4().hex}",
        batch_id=batch_id,
        role=role,
        text=text,
        payload=payload or {},
    )
    return BatchMessageRepository(db).add(message)


def _ensure_company(db: Session, company_id: str) -> None:
    repository = EmployerRepository(db)
    if repository.get(company_id) is None:
        repository.upsert({"company_id": company_id, "name": DEFAULT_COMPANY_NAME})


def create_batch(
    db: Session,
    title: str | None = None,
    language: str = "am",
    company_id: str = DEFAULT_COMPANY_ID,
) -> InterviewBatch:
    """Open a new thread. Nothing is asked for up front except a name."""

    _ensure_company(db, company_id)
    batch = InterviewBatch(
        batch_id=f"batch_{uuid4().hex[:12]}",
        company_id=company_id,
        title=(title or "New interview round").strip()[:255],
        language=language,
        questions=[],
        categories={},
        status=DRAFT,
    )
    saved = BatchRepository(db).add(batch)
    _say(
        db,
        saved.batch_id,
        "Send me two things and I will run the interviews: a CSV of employees with "
        "a name column and a contact column, and the questions you want them asked. "
        "Nobody is called until you confirm.",
    )
    return saved


# -- the roster ------------------------------------------------------------ #


def _header_value(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for key, value in row.items():
        if key and key.strip().lower().replace(" ", "_") in candidates:
            if value and value.strip():
                return value.strip()
    return ""


def import_batch_csv(db: Session, batch: InterviewBatch, content: bytes) -> dict[str, Any]:
    """Read a name/contact CSV into beneficiaries for this batch's company.

    Reuses ``_csv_rows`` so the UTF-8 BOM and the missing-header case behave the
    same as the existing imports. A row missing a name or a contact is reported
    rather than dropped silently, and a contact already on file is reused rather
    than duplicated — the same person must not be called twice for one round.
    """

    rows = list(_csv_rows(content))
    headers = {(name or "").strip().lower().replace(" ", "_") for name in (rows[0].keys() if rows else [])}
    if rows and not headers & set(NAME_HEADERS):
        raise ValueError("CSV file must include a name column")
    if rows and not headers & set(CONTACT_HEADERS):
        raise ValueError("CSV file must include a contact column")

    _ensure_company(db, batch.company_id)
    repository = BeneficiaryRepository(db)
    existing = {
        item.phone_number: item
        for item in repository.for_company(batch.company_id)
    }

    imported: list[dict[str, str]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        name = _header_value(row, NAME_HEADERS)
        contact = _header_value(row, CONTACT_HEADERS)
        if not name or not contact:
            skipped.append(f"row {row_number}: needs both a name and a contact")
            continue
        if contact in seen:
            skipped.append(f"row {row_number}: {name} repeats contact {contact}")
            continue
        seen.add(contact)

        found = existing.get(contact)
        worker_id = found.worker_id if found else f"w_{uuid4().hex[:10]}"
        repository.upsert(
            {
                "worker_id": worker_id,
                "name": name,
                "company_id": batch.company_id,
                "phone_number": contact,
                "preferred_language": batch.language,
                "employer_claims": {},
            }
        )
        imported.append({"worker_id": worker_id, "name": name, "phone_number": contact})

    # The batch remembers its own list. Uploading twice into one thread adds to it
    # rather than replacing it, and never lets the same person in twice.
    recorded = list(batch.roster_worker_ids or [])
    for entry in imported:
        if entry["worker_id"] not in recorded:
            recorded.append(entry["worker_id"])
    BatchRepository(db).update(batch, {"roster_worker_ids": recorded})

    return {"imported": len(imported), "workers": imported, "skipped": skipped}


def roster_people(db: Session, batch: InterviewBatch) -> list[Any]:
    """The beneficiary rows belonging to this batch, in the order they were imported.

    Scoped to ``batch.roster_worker_ids`` rather than to the company. A company
    accumulates every employee ever uploaded, so a company-wide roster made every
    new batch inherit every earlier batch's people — which is how two CSVs each
    holding an "Abebe Kebede" turned "call Abebe" into an unresolvable name.

    A batch with no recorded roster predates the column, and falls back to the
    company list so an existing thread still works.
    """

    repository = BeneficiaryRepository(db)
    ids = list(batch.roster_worker_ids or [])
    if not ids:
        return [person for person in repository.for_company(batch.company_id) if person.is_active]

    by_id = {person.worker_id: person for person in repository.for_company(batch.company_id)}
    return [by_id[worker_id] for worker_id in ids if worker_id in by_id and by_id[worker_id].is_active]


def roster(db: Session, batch: InterviewBatch) -> list[dict[str, Any]]:
    """Everyone who could be called for this batch, with what happened to them."""

    answers = {record.worker_id: record for record in WorkerAnswersRepository(db).for_batch(batch.batch_id)}
    targets = {target.worker_id: target for target in BatchTargetRepository(db).for_batch(batch.batch_id)}
    people = []
    for person in roster_people(db, batch):
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
                "selected": target is not None,
                "call_status": target.status if target else None,
                "call_id": target.call_id if target else None,
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
        for person in roster_people(db, batch)
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
    """The counts. Computed here, never by a model."""

    return analysis.summarize(batch.questions or [], records_for(db, batch), batch.categories or {})


def batch_listing(db: Session) -> list[dict[str, Any]]:
    """One row per batch for the listing page, newest first.

    Deliberately not the full detail: the listing needs four numbers per batch,
    and assembling every roster and every count to show them would read the whole
    database to render one page.
    """

    target_repository = BatchTargetRepository(db)
    rows: list[dict[str, Any]] = []
    for batch in BatchRepository(db).newest_first():
        targets = target_repository.for_batch(batch.batch_id)
        rows.append(
            {
                "batch_id": batch.batch_id,
                "title": batch.title,
                "status": batch.status,
                "language": batch.language,
                "questions": len(batch.questions or []),
                "roster": len(roster_people(db, batch)),
                "selected": len(targets),
                "returned": len([t for t in targets if t.status in TERMINAL_TARGET_STATUSES]),
                "created_at": batch.created_at.isoformat(),
            }
        )
    return rows


def batch_detail(db: Session, batch_id: str) -> dict[str, Any]:
    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    targets = BatchTargetRepository(db).for_batch(batch_id)
    return {
        "batch_id": batch.batch_id,
        "title": batch.title,
        "status": batch.status,
        "language": batch.language,
        "questions": batch.questions or [],
        "categories": batch.categories or {},
        "created_at": batch.created_at.isoformat(),
        "roster": roster(db, batch),
        "targets": [
            {
                "worker_id": target.worker_id,
                "call_id": target.call_id,
                "status": target.status,
            }
            for target in targets
        ],
        "progress": {
            "selected": len(targets),
            "returned": len([target for target in targets if target.status in TERMINAL_TARGET_STATUSES]),
            "waiting": len([target for target in targets if target.status not in TERMINAL_TARGET_STATUSES]),
        },
        "summary": batch_summary(db, batch),
        "messages": [
            {
                "message_id": message.message_id,
                "role": message.role,
                "text": message.text,
                "payload": message.payload or {},
                "created_at": message.created_at.isoformat(),
            }
            for message in BatchMessageRepository(db).for_batch(batch_id)
        ],
    }


# -- small deterministic readers ------------------------------------------- #


def _normalized(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!?")


def is_confirmation(text: str) -> bool:
    return _normalized(text) in CONFIRMATIONS


def is_decline(text: str) -> bool:
    return _normalized(text) in DECLINES


def language_request(text: str) -> str | None:
    """Read an explicit language change out of a message.

    Deliberately narrow: it fires only when the admin names a language *and*
    signals they mean the interview language, so "do they speak English at work?"
    is never mistaken for a setting.
    """

    lowered = _normalized(text)
    if not any(word in lowered for word in ("language", "speak", "interview", "call in", "ask in")):
        return None
    for code, name in questions_module.LANGUAGE_NAMES.items():
        if name.lower() in lowered or f" {code}" == lowered[-3:]:
            return code
    return None


def _question_message(text: str) -> bool:
    stripped = _normalized(text)
    return stripped.startswith("questions:") or stripped.startswith("question:")


# -- questions ------------------------------------------------------------- #


def set_questions(
    db: Session,
    batch: InterviewBatch,
    text: str,
    intelligence: BatchIntelligence | None = None,
) -> list[dict[str, Any]]:
    """Capture the admin's questions, with age asked first regardless.

    Splitting the typed message is a code job — numbering, bullets, one per line.
    Turning "what is ur wage?" into a sentence somebody can be asked down a
    telephone is a language job, so the model rewrites each question while code
    holds the count and the order fixed. What the admin typed is kept beside the
    rewrite, so the thread can show both and nothing is changed behind their back.

    Without ``intelligence`` the questions are used exactly as typed, which is
    what happens when no key is configured.
    """

    body = text.split(":", 1)[1] if _question_message(text) else text
    parsed = questions_module.parse_questions(body)
    if not parsed:
        return []
    refined = intelligence.refine_questions(parsed) if intelligence is not None else parsed
    question_set = questions_module.build_question_set(refined)
    BatchRepository(db).update(batch, {"questions": question_set})
    return question_set


def _question_card(batch: InterviewBatch) -> dict[str, Any]:
    return {"kind": "questions", "questions": batch.questions or []}


# -- selection ------------------------------------------------------------- #


def already_called(db: Session, batch: InterviewBatch) -> set[str]:
    return {
        target.worker_id
        for target in BatchTargetRepository(db).for_batch(batch.batch_id)
        if target.call_id is not None or target.status in TERMINAL_TARGET_STATUSES
    }


def select_targets(
    db: Session,
    batch: InterviewBatch,
    instruction: str,
    intelligence: BatchIntelligence,
) -> tuple[list[BatchTarget], dict[str, Any]]:
    """Resolve one instruction into a persisted, unconfirmed selection.

    The model returns a specification; the draw is done here. Anything the
    specification could not resolve comes back in ``resolution`` so the thread can
    ask, instead of quietly calling a smaller group than the admin asked for.
    """

    from app.intelligence import selection as selection_module

    spec = intelligence.parse_selection(instruction)
    resolution = selection_module.resolve(
        spec,
        roster(db, batch),
        already_called=already_called(db, batch),
    )
    if resolution["error"] or not resolution["selected"]:
        return [], resolution

    worker_ids = [person["worker_id"] for person in resolution["selected"]]
    targets = BatchTargetRepository(db).replace_unconfirmed(batch.batch_id, worker_ids)
    BatchRepository(db).update(batch, {"status": SELECTED})
    return targets, resolution


def _selection_text(resolution: dict[str, Any], names: list[str]) -> str:
    lines = [f"{len(names)} to call: " + ", ".join(names) + "."]
    if resolution.get("unmatched"):
        lines.append(
            "No match on the roster for: " + ", ".join(resolution["unmatched"]) + "."
        )
    if resolution.get("ambiguous"):
        for name, matches in resolution["ambiguous"].items():
            lines.append(f"More than one person matches {name}: {_describe_matches(matches)}. Tell me which.")
    if resolution.get("skipped_already_called"):
        lines.append(f"{len(resolution['skipped_already_called'])} already called and left out.")
    lines.append("Reply yes to place the calls, or tell me a different selection.")
    return " ".join(lines)


def _describe_matches(matches: list[dict[str, Any]]) -> str:
    """Name the people behind an ambiguous match, with a phone number to tell them apart.

    Two employees can share a name; they cannot share a number. Printing worker
    ids here asked the admin to choose between ``w_98b96a75b8`` and
    ``w_f551d1cb2a``, which is not a choice anybody can make.
    """

    parts = []
    for match in matches:
        if isinstance(match, dict):
            phone = match.get("phone_number")
            label = match.get("name") or match.get("worker_id") or "unknown"
            parts.append(f"{label} ({phone})" if phone else str(label))
        else:
            parts.append(str(match))
    return ", ".join(parts)


def _cannot_select_text(resolution: dict[str, Any]) -> str:
    error = resolution.get("error")
    if error == "ambiguous":
        detail = "; ".join(
            f"{name} matches {_describe_matches(matches)}"
            for name, matches in (resolution.get("ambiguous") or {}).items()
        )
        return (
            f"More than one person on the roster matches what you typed, so I have selected "
            f"nobody rather than guess. {detail}. Tell me which one, or give the full name and number."
        )
    if error == "unclear":
        return (
            "I could not tell who you meant. Say it as everyone, a number, a share "
            "such as half, specific names, or the ones not yet called."
        )
    if error == "too_many_requested":
        return (
            f"You asked for {resolution.get('requested')} but only "
            f"{resolution.get('available')} are available to call."
        )
    if error == "nothing_matched":
        unmatched = ", ".join(resolution.get("unmatched") or []) or "those names"
        return f"Nobody on the roster matches {unmatched}, so I have selected nobody."
    return "There is nobody left to call for this round."


# -- dialing --------------------------------------------------------------- #


def dial_selection(
    db: Session,
    batch: InterviewBatch,
    intelligence: BatchIntelligence,
) -> dict[str, Any]:
    """Place one call per selected target. Only ever called after a confirmation."""

    target_repository = BatchTargetRepository(db)
    people = {person.worker_id: person for person in roster_people(db, batch)}
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

    BatchRepository(db).update(batch, {"status": CALLING})
    return {"dialed": dialed, "failed": failed}


# -- results --------------------------------------------------------------- #


def is_batch_call(db: Session, call_id: str) -> bool:
    """True when this call belongs to a batch, so the clause path must not run it."""

    return BatchTargetRepository(db).by_call(call_id) is not None


def batch_result_pending(db: Session, call_id: str) -> bool:
    """True when a batch call is still waiting for its answers to be extracted."""

    target = BatchTargetRepository(db).by_call(call_id)
    return target is not None and target.status not in TERMINAL_TARGET_STATUSES


def process_batch_call(
    db: Session,
    call_id: str,
    transcript: str,
    language: str | None = None,
    intelligence: BatchIntelligence | None = None,
) -> WorkerAnswers:
    """Read one finished interview into answers, then finish the batch if it can."""

    target_repository = BatchTargetRepository(db)
    target = target_repository.by_call(call_id)
    if target is None:
        raise ValueError(f"Call does not belong to a batch: {call_id}")
    batch = BatchRepository(db).get(target.batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {target.batch_id}")

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
    who = (person.name if person and person.name else target.worker_id)
    if record.excluded:
        _say(
            db,
            batch.batch_id,
            f"{who} is excluded and counted nowhere. {record.exclusion_reason}",
            {"kind": "excluded", "worker_id": target.worker_id},
        )
    else:
        _say(
            db,
            batch.batch_id,
            f"Answers came back from {who}.",
            {"kind": "answers", "worker_id": target.worker_id},
        )
    finalize_if_complete(db, batch, engine)
    return record


def mark_call_failed(db: Session, call_id: str, reason: str | None = None) -> None:
    """Record a call that will never produce answers, so the batch can finish."""

    target_repository = BatchTargetRepository(db)
    target = target_repository.by_call(call_id)
    if target is None or target.status in TERMINAL_TARGET_STATUSES:
        return
    target_repository.update(target, {"status": "failed"})
    batch = BatchRepository(db).get(target.batch_id)
    if batch is None:
        return
    person = BeneficiaryRepository(db).get(target.worker_id)
    who = (person.name if person and person.name else target.worker_id)
    _say(
        db,
        batch.batch_id,
        f"The call to {who} did not produce an interview. {reason or ''}".strip(),
        {"kind": "call_failed", "worker_id": target.worker_id},
    )
    finalize_if_complete(db, batch, None)


def finalize_if_complete(
    db: Session,
    batch: InterviewBatch,
    intelligence: BatchIntelligence | None = None,
) -> bool:
    """Run pass two once every call has landed, and post the counts.

    Categorization happens once, across the whole batch, so the buckets are the
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
        batch.batch_id,
        f"All interviews are back. {analysis.headline(summary)} Ask me anything about them.",
        {"kind": "summary", "summary": summary},
    )
    return True


# -- the single input box -------------------------------------------------- #


def handle_upload(
    db: Session,
    batch_id: str,
    content: bytes,
    filename: str | None = None,
) -> list[BatchMessage]:
    """Parse an uploaded CSV, echo who is on it, and never call anybody yet."""

    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")

    _say(db, batch_id, f"Uploaded {filename or 'employees.csv'}", role="admin")
    try:
        result = import_batch_csv(db, batch, content)
    except ValueError as exc:
        return [_say(db, batch_id, f"I could not read that CSV. {exc}")]

    lines = [f"{result['imported']} employee(s) on the roster."]
    if result["skipped"]:
        lines.append(f"{len(result['skipped'])} row(s) skipped: " + "; ".join(result["skipped"]) + ".")
    lines.append(
        "Now send me the questions to ask."
        if not batch.questions
        else "Questions are already set. Tell me who to call."
    )
    replies = [
        _say(
            db,
            batch_id,
            " ".join(lines),
            {"kind": "roster", "workers": result["workers"], "skipped": result["skipped"]},
        )
    ]
    if batch.questions and batch.status == DRAFT:
        BatchRepository(db).update(batch, {"status": READY})
    return replies


def handle_message(
    db: Session,
    batch_id: str,
    text: str,
    intelligence: BatchIntelligence | None = None,
) -> list[BatchMessage]:
    """The one endpoint the floating input posts to.

    What a message means depends on where the thread is: questions while the batch
    is a draft, a selection once it is ready, a confirmation once people are
    selected, and a question about the results once the answers are in. One box,
    four meanings, decided by phase rather than by making the admin pick a mode.
    """

    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    if not text or not text.strip():
        raise ValueError("Message text is required")

    _say(db, batch_id, text.strip(), role="admin")
    engine = intelligence or build_batch_intelligence()
    people = [person for person in roster(db, batch)]

    language = language_request(text)
    if language and batch.status in {DRAFT, READY, SELECTED}:
        BatchRepository(db).update(batch, {"language": language})
        repository = BeneficiaryRepository(db)
        for person in people:
            stored = repository.get(person["worker_id"])
            if stored is not None:
                stored.preferred_language = language
        db.commit()
        name = questions_module.LANGUAGE_NAMES.get(language, language)
        return [_say(db, batch_id, f"Interviews will be conducted in {name}.")]

    if batch.status == DRAFT or (batch.status == READY and _question_message(text)):
        question_set = set_questions(db, batch, text, intelligence)
        if not question_set:
            return [
                _say(
                    db,
                    batch_id,
                    "I did not find any questions in that. Send them one per line, or numbered.",
                )
            ]
        # The whole set is reported, age included. Counting only the admin's own
        # questions reads as if one of them went missing when age is folded into
        # the slot that is always asked first.
        recorded = f"{len(question_set)} question(s) will be asked, starting with age."
        rewritten = sum(
            1
            for question in question_set
            if question.get("original") and question["original"] != question["text"]
        )
        if rewritten:
            # Said out loud, because the respondent hears the rewrite and not what
            # was typed. An admin who disagrees can retype the question.
            recorded += f" I rewrote {rewritten} of them to be read aloud — check the wording below."
        note = (
            f"{recorded} Tell me who to call: everyone, a number, a share such as half, or specific names."
            if people
            else f"{recorded} Upload the employee CSV next."
        )
        BatchRepository(db).update(batch, {"status": READY if people else DRAFT})
        return [_say(db, batch_id, note, _question_card(batch))]

    if batch.status in {READY, SELECTED}:
        if batch.status == SELECTED and is_confirmation(text):
            result = dial_selection(db, batch, engine)
            lines = [f"Calling {len(result['dialed'])} now: " + ", ".join(result["dialed"]) + "."]
            if result["failed"]:
                lines.append("Could not place a call for: " + ", ".join(result["failed"]) + ".")
            lines.append("Answers will appear here as each interview finishes.")
            return [_say(db, batch_id, " ".join(lines), {"kind": "dialing", **result})]

        if batch.status == SELECTED and is_decline(text):
            BatchTargetRepository(db).replace_unconfirmed(batch.batch_id, [])
            BatchRepository(db).update(batch, {"status": READY})
            return [_say(db, batch_id, "Cancelled, nobody was called. Tell me who to call instead.")]

        if not people:
            return [_say(db, batch_id, "There is nobody on the roster yet. Upload the employee CSV first.")]

        targets, resolution = select_targets(db, batch, text, engine)
        if not targets:
            return [_say(db, batch_id, _cannot_select_text(resolution), {"kind": "selection", **resolution})]
        names = {person["worker_id"]: person["name"] for person in people}
        selected = [names.get(target.worker_id, target.worker_id) for target in targets if target.status == "selected"]
        return [
            _say(
                db,
                batch_id,
                _selection_text(resolution, selected),
                {"kind": "selection", "selected": selected, **resolution},
            )
        ]

    if batch.status == CALLING:
        finished = finalize_if_complete(db, batch, engine)
        if not finished:
            targets = BatchTargetRepository(db).for_batch(batch.batch_id)
            back = len([target for target in targets if target.status in TERMINAL_TARGET_STATUSES])
            return [
                _say(
                    db,
                    batch_id,
                    f"{back} of {len(targets)} interviews are back. I will post the analysis when the rest land.",
                    {"kind": "progress", "returned": back, "selected": len(targets)},
                )
            ]

    summary = batch_summary(db, BatchRepository(db).get(batch_id))
    history = [
        {"role": message.role, "text": message.text}
        for message in BatchMessageRepository(db).for_batch(batch_id)
    ]
    reply = engine.answer(text, summary, history)
    return [_say(db, batch_id, reply, {"kind": "analysis"})]


__all__ = [
    "CALLING",
    "COMPLETE",
    "DEFAULT_COMPANY_ID",
    "DRAFT",
    "READY",
    "SELECTED",
    "batch_detail",
    "batch_listing",
    "batch_result_pending",
    "batch_summary",
    "create_batch",
    "dial_selection",
    "finalize_if_complete",
    "handle_message",
    "handle_upload",
    "import_batch_csv",
    "is_batch_call",
    "mark_call_failed",
    "process_batch_call",
    "records_for",
    "roster",
    "roster_people",
    "select_targets",
    "set_questions",
]
