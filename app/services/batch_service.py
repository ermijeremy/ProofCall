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

import hashlib
import json
import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.integrations.teleexpert_client import TeleExpertError
from app.intelligence import agent, analysis, categorize as categorize_module, questions as questions_module
from app.intelligence.batch_engine import BatchIntelligence, build_batch_intelligence
from app.intelligence.providers.base import ProviderError
from app.models.batch import BatchMessage, BatchTarget, CallAttempt, InterviewBatch, WorkerAnswerRecord, WorkerAnswers
from app.models.employer import Employer
from app.repositories.batches import (
    BatchMessageRepository,
    BatchRepository,
    BatchTargetRepository,
    WorkerAnswerRecordRepository,
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

CONTACT_HEADERS = ("contact", "phone", "phone_number", "phone_e164", "telephone", "number")
NAME_HEADERS = ("name", "first_name", "full_name", "employee", "employee_name")
GENDER_HEADERS = ("gender", "sex")
AGE_BAND_HEADERS = ("age_band", "ageband")
COHORT_HEADERS = ("training_cohort_id", "cohort", "cohort_id")
TRAINING_END_HEADERS = ("training_end_date", "training_end")
PLACEMENT_STATUS_HEADERS = ("placement_status_per_besingularity", "placement_status")
PLACEMENT_DATE_HEADERS = ("placement_date",)
FOLLOWUP_HEADERS = ("consent_to_followup_contact", "followup_consent")
BENEFICIARY_HEADERS = ("beneficiary_id", "worker_id", "id")
NOTES_HEADERS = ("notes", "note")
ALLOWED_LANGUAGES = {"am", "en", "other"}
ALLOWED_GENDERS = {"F", "M", ""}
ALLOWED_PLACEMENT = {"placed_job", "gig", "not_placed", "unknown", "lost_contact", ""}
_PHONE_E164 = re.compile(r"^\+2519\d{8}$")

_NOT_DIGITS = re.compile(r"[^0-9+]")


def _good_job_annotation(extraction: dict[str, Any], company: Employer | None = None) -> tuple[str, dict[str, Any]]:
    """Apply the deterministic Callwise KPI rules to extracted answers."""
    from app.intelligence.callwise_rules import evaluate
    result = evaluate(extraction.get("answers", {}), age_assessment=extraction.get("age_assessment", "UNKNOWN"),
                      safeguarding=bool(extraction.get("safeguarding_flag")),
                      minimum_wage_etb=getattr(company, "minimum_wage_etb", None))
    return result["overall_verdict"], result["clauses"]


def _persist_call_response(
    *,
    call_id: str,
    batch_id: str,
    worker_id: str,
    transcript: str | None,
    transcript_turns: list[dict[str, Any]],
    language: str | None,
    audio_url: str | None,
    extraction: dict[str, Any],
    annotation: str,
    kpi_clauses: dict[str, Any],
) -> Path:
    """Write the completed-call handoff before inserting its DB projection.

    The filename contains the provider call ID and UTC save timestamp, making
    completed calls easy to sort and find. Duplicate webhooks reuse the first
    artifact for that call rather than creating another file.
    This is an operational/debug artifact, not a public export.
    """

    safe_call_id = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id)
    directory = Path(settings.callwise_response_dir)
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob(f"{safe_call_id}_*.json"))
    if existing:
        path = existing[0]
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{safe_call_id}_{timestamp}.json"
    payload = {
        "schema_version": "callwise-response-v1",
        "saved_at": datetime.now(UTC).isoformat(),
        "call_id": call_id,
        "batch_id": batch_id,
        "worker_id": worker_id,
        "language": language,
        "transcript": transcript,
        "transcript_turns": transcript_turns,
        "audio_url": audio_url,
        "local_audio_path": None,
        "privacy_redacted": not extraction.get("consent", False),
        "extraction": extraction,
        "good_job_annotation": annotation,
        "good_job": {
            "value": annotation == "CONFIRMED_GOOD_JOB",
            "status": annotation.lower(),
            "counted": annotation == "CONFIRMED_GOOD_JOB" and not extraction.get("excluded", False),
            "source": "deterministic_callwise_rules",
        },
        "kpi_clauses": kpi_clauses,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    logger.info("Callwise response JSON saved call_id=%s path=%s", call_id, path)
    return path


def attach_local_audio_artifact(call_id: str, audio_path: Path) -> Path | None:
    """Record the downloaded audio beside the existing immutable call JSON."""

    safe_call_id = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id)
    directory = Path(settings.callwise_response_dir)
    artifacts = sorted(directory.glob(f"{safe_call_id}_*.json"))
    if not artifacts:
        logger.warning("Cannot attach local audio; response JSON is missing call_id=%s", call_id)
        return None
    path = artifacts[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["local_audio_path"] = str(audio_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


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


def create_batch(
    db: Session,
    title: str = "Callwise validation round",
    language: str = "am",
) -> InterviewBatch:
    """Create a Callwise round for the default internal company.

    This is the programmatic entry point used by fixtures and import jobs. The
    production UI uses the company-scoped thread, but both paths create the same
    fixed questionnaire and the same persisted entities.
    """

    company = ensure_company(db)
    batch = open_round(db, company, title=title)
    if language and language != batch.language:
        BatchRepository(db).update(batch, {"language": language})
    _say(
        db,
        company.company_id,
        "Upload the employee CSV. The fixed Callwise KPI questions are already configured; nobody is called until you confirm.",
        payload={"kind": "batch_ready", "batch_id": batch.batch_id},
        batch_id=batch.batch_id,
    )
    return batch


def open_round(db: Session, company: Employer, title: str | None = None) -> InterviewBatch:
    """Start a round with the fixed Callwise KPI questionnaire."""

    existing = len(BatchRepository(db).for_company(company.company_id))
    batch = InterviewBatch(
        batch_id=f"batch_{uuid4().hex[:12]}",
        company_id=company.company_id,
        title=(title or f"Round {existing + 1}")[:255],
        language=_company_language(db, company),
        questions=questions_module.fixed_question_set(),
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

    Punctuation and spacing vary between exports of the same list, so
    ``+251 911 000 001`` and ``+251911000001`` are one person. This is the key the
    import deduplicates on, which is what removed the need for per-round roster
    membership: the same number is the same employee, so a second upload updates
    the row instead of creating a second Abebe nobody can tell apart.
    """

    cleaned = _NOT_DIGITS.sub("", raw or "")
    if cleaned.startswith("+"):
        return "+" + cleaned[1:].lstrip("+")
    return cleaned


def _header_value(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for key, value in row.items():
        if key and key.strip().lower().replace(" ", "_") in candidates:
            if value and value.strip():
                return value.strip()
    return ""


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value.strip() else None
    except ValueError:
        return None


def _parse_yes(value: str) -> bool:
    return value.strip().lower() in {"yes", "y", "true", "1"}


def _parse_followup(value: str) -> tuple[bool, date | None, str | None]:
    """Parse ``yes;YYYY-MM-DD;form`` without treating incomplete consent as yes."""
    parts = [part.strip() for part in (value or "").split(";")]
    if len(parts) < 2 or parts[0].lower() != "yes":
        return False, None, None
    recorded = _parse_date(parts[1])
    source = parts[2].lower() if len(parts) > 2 else None
    if recorded is None or source not in {"written", "verbal", "registration_form"}:
        return False, recorded, source
    return True, recorded, source


def import_company_csv(db: Session, company: Employer, content: bytes) -> dict[str, Any]:
    """Read a name/contact CSV into this company's list of people.

    Reuses ``_csv_rows`` so the UTF-8 BOM and the missing-header case behave the
    same as the existing imports. A row missing a name or a contact is reported
    rather than dropped silently, and a number already on file updates that person
    rather than adding a second copy of them.
    """

    rows = list(_csv_rows(content))
    headers = {(name or "").strip().lower().replace(" ", "_") for name in (rows[0].keys() if rows else [])}
    modern = bool(headers & set(BENEFICIARY_HEADERS))
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
        beneficiary_id = _header_value(row, BENEFICIARY_HEADERS)
        if modern and not beneficiary_id:
            skipped.append(f"row {row_number} has no beneficiary_id")
            continue
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

        if modern and not _PHONE_E164.fullmatch(contact.strip()):
            skipped.append(f"row {row_number}: {name} has an invalid phone_e164; use +2519XXXXXXXX")
            continue
        preferred = _header_value(row, ("preferred_language", "language")) or language
        gender = _header_value(row, GENDER_HEADERS).upper()
        placement_status = _header_value(row, PLACEMENT_STATUS_HEADERS).lower()
        training_end = _parse_date(_header_value(row, TRAINING_END_HEADERS))
        consent_value = _header_value(row, FOLLOWUP_HEADERS)
        consent, consent_date, consent_source = _parse_followup(consent_value) if modern else (_parse_yes(consent_value), None, None)
        if modern and preferred not in ALLOWED_LANGUAGES:
            skipped.append(f"row {row_number}: unsupported preferred_language {preferred!r}")
            continue
        if modern and gender not in ALLOWED_GENDERS:
            skipped.append(f"row {row_number}: gender must be F or M")
            continue
        if modern and placement_status not in ALLOWED_PLACEMENT:
            skipped.append(f"row {row_number}: invalid placement status")
            continue
        if modern and (training_end is None or not date(2026, 3, 1) <= training_end <= date(2026, 5, 31)):
            skipped.append(f"row {row_number}: training_end_date must be between 2026-03-01 and 2026-05-31")
            continue
        if modern and not consent:
            skipped.append(f"row {row_number}: no complete follow-up consent")
            continue

        found = existing.get(key)
        worker_id = beneficiary_id if modern else (found.worker_id if found else f"w_{uuid4().hex[:10]}")
        gender = _header_value(row, GENDER_HEADERS)
        age_band = _header_value(row, AGE_BAND_HEADERS)
        cohort_id = _header_value(row, COHORT_HEADERS)
        training_end = training_end or _parse_date(_header_value(row, TRAINING_END_HEADERS))
        placement_status = placement_status or _header_value(row, PLACEMENT_STATUS_HEADERS)
        placement_date = _parse_date(_header_value(row, PLACEMENT_DATE_HEADERS))
        followup = consent
        person = repository.upsert(
            {
                "worker_id": worker_id,
                "name": name,
                "company_id": company.company_id,
                "phone_number": contact,
                "preferred_language": preferred,
                "gender": gender or (found.gender if found else None),
                "age_band": age_band or (found.age_band if found else None),
                "training_cohort_id": cohort_id or (found.training_cohort_id if found else None),
                "training_end_date": training_end or (found.training_end_date if found else None),
                "placement_status": placement_status or (found.placement_status if found else None),
                "placement_date": placement_date or (found.placement_date if found else None),
                "consent_to_followup_contact": followup or (found.consent_to_followup_contact if found else False),
                "consent_recorded_at": consent_date or (found.consent_recorded_at if found else None),
                "consent_source": consent_source or (found.consent_source if found else None),
                "notes": _header_value(row, NOTES_HEADERS) or (found.notes if found else None),
                "employer_claims": found.employer_claims if found else {},
                "is_active": True,
            }
        )
        existing[key] = person
        entry = {"worker_id": worker_id, "name": name, "phone_number": contact}
        (updated if found else added).append(entry)

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "modern_contract": modern,
        "eligible": len(added) + len(updated),
        "total": len(repository.for_company(company.company_id)),
    }


def add_company_people(
    db: Session, company: Employer, people: list[dict[str, Any]]
) -> dict[str, Any]:
    """Add a small roster update supplied directly in the chat.

    Phone number is the stable identity within a company, matching CSV import
    behavior. Existing numbers are updated rather than duplicated.
    """

    repository = BeneficiaryRepository(db)
    existing = {
        normalize_phone(person.phone_number): person
        for person in repository.for_company(company.company_id)
    }
    added: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []
    skipped: list[str] = []
    language = _company_language(db, company)

    for item in people:
        name = str(item.get("name") or "").strip()
        contact = str(item.get("phone_number") or item.get("contact") or "").strip()
        key = normalize_phone(contact)
        if not name or not key:
            skipped.append("an entry was missing a name or usable phone number")
            continue
        found = existing.get(key)
        worker_id = found.worker_id if found else f"w_{uuid4().hex[:10]}"
        person = repository.upsert(
            {
                "worker_id": worker_id,
                "name": name,
                "company_id": company.company_id,
                "phone_number": contact,
                "preferred_language": found.preferred_language if found else language,
                "employer_claims": found.employer_claims if found else {},
                "is_active": True,
            }
        )
        existing[key] = person
        entry = {"worker_id": worker_id, "name": name, "phone_number": contact}
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


def roster(db: Session, company_id: str | InterviewBatch, batch: InterviewBatch | None = None) -> list[dict[str, Any]]:
    """Everyone on the company's list, with what has happened to them.

    Exclusions and answers are read across every round: somebody excluded as a
    minor in round one must stay excluded in round four, and there is no version
    of that rule that should depend on which round is open.
    """

    if isinstance(company_id, InterviewBatch):
        batch = company_id
        company_id = batch.company_id

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
        historically_excluded = bool(record.excluded) if record else False
        # A controlled local validation override lets the team reuse a single
        # test contact after an old fixture was incorrectly classified. It is
        # opt-in by worker ID and does not disable safeguarding for any new
        # interview result or for production workers.
        excluded = historically_excluded and person.worker_id not in settings.callwise_test_worker_ids
        people.append(
            {
                "worker_id": person.worker_id,
                "name": person.name or person.worker_id,
                "phone_number": person.phone_number,
                "preferred_language": person.preferred_language,
                "gender": person.gender,
                "age_band": person.age_band,
                "training_cohort_id": person.training_cohort_id,
                "placement_status": person.placement_status,
                "consent_to_followup_contact": person.consent_to_followup_contact,
                "selected": target is not None and target.status == "selected",
                "call_status": target.status if target else None,
                "call_id": target.call_id if target else None,
                "ever_called": person.worker_id in reached,
                "answered": record is not None,
                "excluded": excluded,
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
            "disposition": record.disposition,
            "attempts": record.attempts,
            "good_job_annotation": record.good_job_annotation,
            "kpi_clauses": record.kpi_clauses or {},
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
        if batch is not None and status == CALLING:
            targets = BatchTargetRepository(db).for_batch(batch.batch_id)
            if targets and not any(target.status == "dialing" for target in targets) and any(
                target.status == "retry_wait" for target in targets
            ):
                status = SELECTED
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
    # A webhook can move the provider call to retry_wait after the round was
    # marked calling. Derive the display state from the target lifecycle so an
    # already-failed call is never shown as still in progress.
    display_status = batch.status if batch is not None else DRAFT
    if (
        batch is not None
        and display_status == CALLING
        and targets
        and not any(target.status == "dialing" for target in targets)
        and any(target.status == "retry_wait" for target in targets)
    ):
        display_status = SELECTED
    return {
        "company_id": company.company_id,
        "name": company.name,
        "timezone": company.timezone,
        "clock_convention": company.clock_convention,
        "settings": {
            "minimum_wage_etb": company.minimum_wage_etb,
            "small_cell_threshold": company.small_cell_threshold,
        },
        "language": batch.language if batch is not None else "am",
        "status": display_status,
        "waiting_on": WAITING_ON.get(display_status, ""),
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


def batch_detail(db: Session, batch_id: str) -> dict[str, Any]:
    """Return a batch-shaped view for internal API consumers."""

    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    payload = company_thread(db, batch.company_id)
    payload["batch_id"] = batch.batch_id
    payload["title"] = batch.title
    payload["roster"] = payload.get("people", [])
    return payload


def debug_snapshot(db: Session, batch_id: str) -> dict[str, Any]:
    """Expose the deterministic stored pipeline state for local validation."""

    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    return {
        "batch": batch_detail(db, batch_id),
        "records": records_for(db, batch),
        "summary": batch_summary(db, batch),
    }


def replay_fixture(db: Session, batch_id: str, worker_id: str, fixture: str) -> WorkerAnswers:
    """Replay a local transcript fixture through the same completion path."""

    from pathlib import Path

    batch = BatchRepository(db).get(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    path = Path("tests/fixtures/transcripts") / fixture
    if path.suffix != ".json" or not path.exists():
        raise ValueError("fixture not found")
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    turns = payload.get("turns") if isinstance(payload, dict) else None
    from app.services.teleexpert_service import transcript_from_turns

    transcript = transcript_from_turns(turns) if isinstance(turns, list) else str(payload.get("transcript", ""))
    target = next((item for item in BatchTargetRepository(db).for_batch(batch_id) if item.worker_id == worker_id), None)
    if target is None:
        target = BatchTarget(batch_id=batch_id, worker_id=worker_id, call_id=f"fixture_{uuid4().hex}", status="dialing")
        db.add(target)
        db.commit()
    return process_batch_call(db, target.call_id, transcript, payload.get("language") if isinstance(payload, dict) else None)


# -- questions ------------------------------------------------------------- #


def set_questions(
    db: Session,
    batch: InterviewBatch,
    typed: list[str],
    intelligence: BatchIntelligence | None = None,
) -> list[dict[str, Any]]:
    """Return the fixed questionnaire; administrator text cannot replace it.

    ``typed`` remains in the signature temporarily so existing chat actions do
    not crash while the UI is migrated. It is intentionally ignored: allowing
    free-form questions would change the KPI schema and produce incomparable
    batches.
    """

    question_set = questions_module.fixed_question_set()
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
                    # Application-level retries are scheduled at least 24h
                    # apart; TeleExpert must not perform hidden rapid retries.
                    retries=0,
                    answer_timeout_seconds=settings.teleexpert_answer_timeout_seconds,
                ),
                idempotency_key=f"{batch.batch_id}:{person.worker_id}",
            )
        except (TeleExpertError, ValueError) as exc:
            logger.exception("Dialing failed for %s", person.worker_id)
            # No provider call exists in this branch. Keep the target retryable
            # and retain the reason in the batch target so the UI can explain
            # what happened instead of leaving the whole round stuck in
            # ``calling`` forever.
            target_repository.update(
                target,
                {"status": "failed", "failure_reason": str(exc)[:1000]},
            )
            failed.append(person.name or person.worker_id)
            continue
        if call.status in {"failed", "cancelled"}:
            # A provider response can be HTTP-successful while still returning
            # a terminal rejection. Do not report that as a placed call.
            target_repository.update(
                target,
                {
                    "call_id": call.call_id,
                    "status": "failed",
                    "failure_reason": getattr(call, "failure_reason", None),
                },
            )
            failed.append(person.name or person.worker_id)
            continue
        # TeleExpert accepts asynchronously (normally with ``queued``). The
        # local target uses ``dialing`` for every accepted in-flight call; the
        # provider's exact state remains on TeleExpertCall.status.
        target_repository.update(target, {"call_id": call.call_id, "status": "dialing", "attempts": (target.attempts or 0) + 1})
        db.add(CallAttempt(
            attempt_id=f"attempt_{uuid4().hex}", batch_id=batch.batch_id,
            worker_id=person.worker_id, call_id=call.call_id,
            attempt_number=target.attempts, provider_state=call.status,
        ))
        db.commit()
        dialed.append(person.name or person.worker_id)

    # ``calling`` means at least one provider call is genuinely in flight.
    # If every submission failed before a call was created, leave the round in
    # ``selected`` so the administrator can retry without re-uploading the
    # roster. The failed target is re-armed below; its failure reason remains
    # in the call/batch history where applicable.
    if dialed:
        next_status = CALLING
    else:
        next_status = SELECTED if failed else READY
        if failed:
            for target in target_repository.for_batch(batch.batch_id):
                if target.status == "failed":
                    target_repository.update(
                        target,
                        {
                            # A failed provider call is retained in
                            # TeleExpertCall/CallAttempt history. Clearing the
                            # target link lets the next confirmed selection
                            # create a fresh provider call for the same worker.
                            "call_id": None,
                            "status": "selected",
                            "failure_reason": target.failure_reason,
                        },
                    )
    BatchRepository(db).update(
        batch,
        {"status": next_status, "scheduled_at": None, "started_at": batch.started_at or datetime.utcnow()},
    )
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
    """True when a round's call is still waiting for its answers to be extracted."""

    target = BatchTargetRepository(db).by_call(call_id)
    return target is not None and target.status not in TERMINAL_TARGET_STATUSES


def _disposition_for(extraction: dict[str, Any]) -> str:
    """Distinguish a genuinely partial conversation from a full completion."""
    if extraction.get("excluded"):
        return "stopped" if extraction.get("interview_stopped") else "excluded"
    answers = extraction.get("answers") or {}
    states = [item.get("state") for item in answers.values() if isinstance(item, dict)]
    if extraction.get("interview_stopped") or "NOT_ASKED" in states:
        return "partial"
    return "completed"


def _finish_attempt(db: Session, call_id: str, *, disposition: str, reason: str | None = None) -> None:
    attempt = db.scalars(select(CallAttempt).where(CallAttempt.call_id == call_id).order_by(CallAttempt.attempt_number.desc())).first()
    if attempt is not None:
        attempt.ended_at = datetime.utcnow()
        attempt.disposition = disposition
        attempt.failure_reason = reason
        db.commit()


def process_batch_call(
    db: Session,
    call_id: str,
    transcript: str,
    language: str | None = None,
    audio_url: str | None = None,
    transcript_turns: list[dict[str, Any]] | None = None,
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
    existing_history = WorkerAnswerRecordRepository(db).by_call(call_id)
    if existing_history is not None:
        current = WorkerAnswersRepository(db).get((batch.batch_id, target.worker_id))
        if current is not None:
            return current
    extraction = engine.extract_answers(transcript, target.worker_id, batch.questions or [])
    company = db.get(Employer, batch.company_id)
    annotation, kpi_clauses = _good_job_annotation(extraction, company)
    disposition = _disposition_for(extraction)

    # The consent script promises deletion of the whole call when the
    # respondent declines or voids recording.  Keep only the minimum
    # operational/audit fields needed to explain exclusion; never persist the
    # raw transcript, speaker turns, or provider audio URL in that case.
    consented = bool(extraction.get("consent"))
    retained_transcript = transcript if consented else None
    retained_turns = (transcript_turns or []) if consented else []
    retained_audio_url = audio_url if consented else None
    stored_extraction = dict(extraction)
    stored_extraction["transcript"] = retained_transcript
    _persist_call_response(
        call_id=call_id,
        batch_id=batch.batch_id,
        worker_id=target.worker_id,
        transcript=retained_transcript,
        transcript_turns=retained_turns,
        language=extraction["language"] or language,
        audio_url=retained_audio_url,
        extraction=stored_extraction,
        annotation=annotation,
        kpi_clauses=kpi_clauses,
    )
    logger.info(
        "Callwise analysis completed call_id=%s worker_id=%s disposition=%s excluded=%s "
        "age_assessment=%s answers=%s kpi=%s",
        call_id,
        target.worker_id,
        disposition,
        extraction.get("excluded"),
        extraction.get("age_assessment"),
        json.dumps(extraction.get("answers") or {}, ensure_ascii=False, default=str),
        json.dumps(kpi_clauses or {}, ensure_ascii=False, default=str),
    )
    record = WorkerAnswersRepository(db).upsert(
        {
            "batch_id": batch.batch_id,
            "worker_id": target.worker_id,
            "call_id": call_id,
            "answers": extraction["answers"],
            "consent": extraction["consent"],
            "consent_json": extraction.get("consent_details", {}),
            "language": extraction["language"] or language,
            "transcript": retained_transcript,
            "transcript_turns": retained_turns,
            "audio_url": retained_audio_url,
            "disposition": disposition,
            "attempts": target.attempts or 1,
            "good_job_annotation": annotation,
            "kpi_clauses": kpi_clauses,
            "excluded": extraction["excluded"],
            "exclusion_reason": extraction["exclusion_reason"],
        }
    )
    # Preserve every completed call. The projection above is used for current
    # counts; this append-only row prevents a later interview from replacing
    # the earlier transcript or answer set.
    history = WorkerAnswerRecord(
        answer_record_id=f"answer_{uuid4().hex}",
        batch_id=batch.batch_id,
        worker_id=target.worker_id,
        call_id=call_id,
        answers=extraction["answers"],
        consent=extraction["consent"],
        consent_json=extraction.get("consent_details", {}),
        language=extraction["language"] or language,
        transcript=retained_transcript,
        transcript_turns=retained_turns,
        audio_url=retained_audio_url,
        disposition=disposition,
        attempts=target.attempts or 1,
        good_job_annotation=annotation,
        kpi_clauses=kpi_clauses,
        excluded=extraction["excluded"],
        exclusion_reason=extraction["exclusion_reason"],
    )
    db.add(history)
    try:
        db.commit()
    except IntegrityError:
        # TeleExpert may retry a webhook, and two delivery workers can race
        # before either sees the existing immutable record. Keep the first
        # record and treat the second delivery as an idempotent success.
        db.rollback()
        existing = WorkerAnswerRecordRepository(db).by_call(call_id)
        if existing is not None:
            current = WorkerAnswersRepository(db).get((batch.batch_id, target.worker_id))
            if current is not None:
                logger.info("Ignored duplicate completed call call_id=%s", call_id)
                return current
        raise
    _finish_attempt(db, call_id, disposition=disposition, reason=record.exclusion_reason)
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
    batch = BatchRepository(db).get(target.batch_id)
    if batch is None:
        return
    attempts = target.attempts or 1
    # ``retries`` is the number of retries after the initial attempt. Therefore
    # retries=0 means no retry, retries=1 permits attempt 2, and so on.
    if attempts <= max(0, batch.retries):
        next_attempt = datetime.utcnow().replace(microsecond=0)
        from datetime import timedelta

        target_repository.update(
            target,
            {
                "status": "retry_wait",
                "next_attempt_at": next_attempt + timedelta(hours=24),
                "failure_reason": reason or "Call did not connect",
            },
        )
        _finish_attempt(db, call_id, disposition="no_answer", reason=reason or "Call did not connect")
        person = BeneficiaryRepository(db).get(target.worker_id)
        who = person.name if person and person.name else target.worker_id
        _say(
            db,
            batch.company_id,
            f"The call to {who} did not connect. I will retry after 24 hours (attempt {attempts + 1} of {batch.retries}).",
            {"kind": "retry_scheduled", "worker_id": target.worker_id, "next_attempt_at": target.next_attempt_at.isoformat()},
            batch_id=batch.batch_id,
        )
        # The provider call is terminal even though this worker has a future
        # retry. The round must not remain visually stuck in ``calling``; the
        # admin may explicitly start a fresh round immediately if needed.
        BatchRepository(db).update(batch, {"status": SELECTED})
        return

    target_repository.update(target, {"status": "failed", "failure_reason": reason})
    _finish_attempt(db, call_id, disposition="no_answer", reason=reason or "Call did not connect")
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


def run_due_retries(db: Session, intelligence: BatchIntelligence | None = None) -> list[str]:
    """Place the next application-level attempt after the 24-hour wait."""

    retried: list[str] = []
    people_cache: dict[str, Any] = {}
    for target in BatchTargetRepository(db).due_retries(datetime.utcnow()):
        batch = BatchRepository(db).get(target.batch_id)
        if batch is None:
            continue
        person = people_cache.get(target.worker_id) or BeneficiaryRepository(db).get(target.worker_id)
        if person is None:
            continue
        people_cache[target.worker_id] = person
        engine = intelligence or build_batch_intelligence()
        prompt = engine.interview_prompt(person, batch.questions or [], language=batch.language, programme_name="beSingularity Callwise validation")
        try:
            call = submit_teleexpert_call(
                db,
                CallCreate(
                    worker_id=person.worker_id,
                    phone_number=person.phone_number,
                    prompt=prompt,
                    retries=0,
                    answer_timeout_seconds=settings.teleexpert_answer_timeout_seconds,
                ),
                idempotency_key=f"{batch.batch_id}:{person.worker_id}:attempt:{(target.attempts or 0) + 1}",
            )
        except (TeleExpertError, ValueError):
            logger.exception("Retry dialing failed for %s", person.worker_id)
            continue
        target_repository = BatchTargetRepository(db)
        target_repository.update(target, {"call_id": call.call_id, "status": "dialing", "attempts": (target.attempts or 0) + 1, "next_attempt_at": None})
        db.add(CallAttempt(
            attempt_id=f"attempt_{uuid4().hex}", batch_id=batch.batch_id,
            worker_id=person.worker_id, call_id=call.call_id,
            attempt_number=target.attempts, provider_state=call.status,
        ))
        db.commit()
        retried.append(target.worker_id)
    return retried


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

    BatchRepository(db).update(
        batch,
        {"categories": categories, "status": COMPLETE, "completed_at": datetime.utcnow()},
    )
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
            # A failed call is terminal operationally, but it is not an answer.
            # Keep it out of ``returned`` so the model cannot tell the admin that
            # an interview came back when TeleExpert only reported a failure.
            "returned": len([target for target in targets if target.status == "completed"]),
            "failed": len([target for target in targets if target.status == "failed"]),
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
    batch = current_round(db, company.company_id)
    # A round that has already selected or called people is finished being edited.
    # Retyping questions then means a new round over the same list of people,
    # which is the whole reason the thread outlives the round.
    if batch is None or batch.status not in {DRAFT, READY}:
        batch = open_round(db, company)

    question_set = set_questions(db, batch, [], engine)

    people = roster(db, company.company_id, batch)
    BatchRepository(db).update(batch, {"status": READY if people else DRAFT})
    return Outcome(
        {
            "ok": True,
            "questions": [item["text"] for item in question_set],
            "count": len(question_set),
            "age_asked_first": True,
            "fixed_questionnaire": True,
            "people_on_list": len(people),
        },
        card={"kind": "questions", "questions": question_set},
        batch_id=batch.batch_id,
    )


def _tool_add_people(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    people = arguments.get("people") or []
    if not isinstance(people, list) or not people:
        return Outcome({"ok": False, "error": "no employees were provided"})
    result = add_company_people(db, company, people)
    changed = result["added"] + result["updated"]
    return Outcome(
        {
            "ok": bool(changed),
            "added": len(result["added"]),
            "updated": len(result["updated"]),
            "skipped": result["skipped"],
            "total": result["total"],
        },
        card={"kind": "roster", "people": changed, "skipped": result["skipped"], "total": result["total"]},
    )


def _tool_select_people(
    db: Session, company: Employer, arguments: dict[str, Any], engine: BatchIntelligence
) -> Outcome:
    batch = current_round(db, company.company_id)
    if batch is None or not batch.questions:
        return Outcome({"ok": False, "error": "no questions have been set yet"})
    # A completed round is immutable history. Selecting somebody again starts a
    # fresh round, even when the question set is unchanged, so the old call ID
    # can never suppress a real retry.
    if batch.status == COMPLETE:
        previous_questions = list(batch.questions)
        batch = open_round(db, company)
        BatchRepository(db).update(batch, {"questions": previous_questions, "status": READY})
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
    "add_people": _tool_add_people,
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
    {"add_people", "set_questions", "select_people", "schedule_calls", "dial_now", "cancel_selection", "set_language"}
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
    if batch is None:
        batch = open_round(db, company)
    # Keep an audit fingerprint and row counts, but never embed the source CSV
    # itself in the database. This supports the documented retention policy.
    BatchRepository(db).update(
        batch,
        {
            "source_checksum": hashlib.sha256(content).hexdigest(),
            "source_row_count": len(list(_csv_rows(content))),
            "eligible_row_count": result["eligible"],
        },
    )
    lines.append("The fixed Callwise KPI questionnaire is ready. Tell me who to call.")

    if batch.status == DRAFT:
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

    # Older internal callers passed a batch id. Resolve it here while keeping
    # the public product contract company-scoped (one chat thread per company).
    batch_argument = BatchRepository(db).get(company_id)
    if batch_argument is not None:
        company_id = batch_argument.company_id
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
    "add_company_people",
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
    "attach_local_audio_artifact",
    "batch_summary",
    "batch_result_pending",
    "batch_detail",
    "create_batch",
    "debug_snapshot",
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
    "replay_fixture",
    "roster",
    "run_due_rounds",
    "run_due_retries",
    "select_targets",
    "set_questions",
]
