"""TeleExpert response normalization and webhook event processing."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.integrations.teleexpert_client import TeleExpertClient
from app.models.call import TeleExpertCall
from app.models.webhook import TeleExpertWebhookEvent
from app.repositories.calls import CallRepository
from app.services.completion_service import process_completed_call

logger = logging.getLogger(__name__)

#: The three statuses ``api.md`` calls terminal, and the only ones that end a
#: call. Everything else — documented in-flight states, and any state name the
#: provider adds later — is treated as still in flight by :func:`is_active`.
TERMINAL_CALL_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: Statuses TeleExpert reports while a call is still in flight. ``processing`` is
#: not in ``api.md``'s table but is what the running service actually reports
#: between accepting a call and connecting it, so it is listed here to keep the
#: documented set honest about what has been observed.
ACTIVE_CALL_STATUSES = frozenset(
    {"queued", "dispatching", "dialing", "retry_wait", "in_progress", "processing"}
)


def is_active(status: str | None) -> bool:
    """True while a call may still produce an interview.

    Deliberately the complement of the terminal set rather than a membership test
    against :data:`ACTIVE_CALL_STATUSES`. A status the provider reports that this
    code has never heard of is a call still in progress, not a failed one: reading
    an unknown state as a failure made every call to ``processing`` announce
    itself in the thread as "did not go through" while the telephone was in fact
    ringing, and then stopped polling it, so the real result never arrived.
    """

    return (status or "") not in TERMINAL_CALL_STATUSES

#: How many times a failed event may be drained before it is left alone.
MAX_EVENT_ATTEMPTS = 3


#: What TeleExpert calls each side of the call, mapped to the words the extraction
#: prompt uses. TeleExpert labels its own interviewer "assistant" and the person
#: who was rung "caller"; the extraction prompt asks for the "respondent's" words
#: and warns against quoting the "interviewer", so the labels are translated here
#: rather than leaving the model to guess which side is which.
SPEAKER_LABELS = {
    "assistant": "interviewer",
    "agent": "interviewer",
    "system": "interviewer",
    "caller": "respondent",
    "user": "respondent",
    "callee": "respondent",
}


def speaker_label(turn: dict[str, Any]) -> str:
    raw = str(turn.get("role") or turn.get("speaker") or "unknown").strip().lower()
    return SPEAKER_LABELS.get(raw, raw or "unknown")


def transcript_from_turns(turns: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{speaker_label(turn)}: {turn.get('text', '')}".strip()
        for turn in turns
        if isinstance(turn, dict) and turn.get("text")
    )


def result_values(payload: dict[str, Any], call: TeleExpertCall) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    transcript_value = result.get("transcript", payload.get("transcript"))
    turns = transcript_value.get("turns", []) if isinstance(transcript_value, dict) else []
    transcript = transcript_from_turns(turns) if turns else transcript_value if isinstance(transcript_value, str) else call.transcript
    audio_url = result.get("audio_url", payload.get("audio_url", call.audio_url))
    return {
        "status": payload.get("status", call.status),
        "transcript": transcript,
        "transcript_turns": turns or payload.get("transcript_turns", call.transcript_turns),
        "language": payload.get("language", call.language),
        "audio_url": audio_url,
        "failure_reason": payload.get("error", payload.get("failure_reason", call.failure_reason)),
    }


def update_call_from_status(db: Session, call: TeleExpertCall, payload: dict[str, Any]) -> TeleExpertCall:
    values = result_values(payload, call)
    if values["status"] == "completed" and call.completed_at is None:
        values["completed_at"] = datetime.utcnow()
    return CallRepository(db).update(call, values)


def dispatch_completed_call(db: Session, call_id: str, values: dict[str, Any]) -> None:
    """Send a finished call to whichever path placed it.

    A batch call carries answers to questions an admin typed this morning, so the
    fixed clause extraction would report fourteen facts nobody asked about and
    none of the answers. The batch path is tried first and the clause path stays
    the default, which keeps the existing demo working untouched.

    ``batch_service`` is imported here rather than at module scope because it
    imports ``call_service``, which imports this module.
    """

    from app.services import batch_service

    if batch_service.is_batch_call(db, call_id):
        batch_service.process_batch_call(db, call_id, values["transcript"], values["language"])
        return
    process_completed_call(
        db,
        call_id,
        values["transcript"],
        values["audio_url"],
        values["transcript_turns"] or [],
        values["language"],
    )


def dispatch_failed_call(db: Session, call_id: str, reason: str | None = None) -> None:
    """Record a call that ended without an interview.

    A batch waits for every target to reach a terminal state before it runs pass
    two, so an unanswered call has to be recorded or the batch never finishes.
    """

    from app.services import batch_service

    if batch_service.is_batch_call(db, call_id):
        batch_service.mark_call_failed(db, call_id, reason)


def process_webhook_event(event_id: str) -> None:
    """Process a durably stored event outside the webhook request."""
    with SessionLocal() as db:
        event = db.get(TeleExpertWebhookEvent, event_id)
        if event is None or event.status == "processed":
            return
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        try:
            call = CallRepository(db).get(event.call_id)
            if call is None:
                raise ValueError(f"Call not found: {event.call_id}")
            call_payload = event.payload["data"]["call"]
            client = TeleExpertClient()
            updated = update_call_from_status(db, call, call_payload)
            # Read once, before the branch: the failure path needs the reason out
            # of the same payload, and reading it only inside the completed branch
            # left it unbound for every call that ended without an interview.
            values = result_values(call_payload, updated)
            if updated.status == "completed":
                if values["audio_url"]:
                    values["audio_url"] = client.absolute_url(values["audio_url"])
                if not values["transcript_turns"] and not values["transcript"]:
                    transcript_payload = client.get_transcript(updated.call_id)
                    values["transcript_turns"] = transcript_payload.get("turns", [])
                    values["transcript"] = transcript_from_turns(values["transcript_turns"])
                if not values["transcript"]:
                    raise ValueError("Completed TeleExpert call did not include a transcript")
                dispatch_completed_call(db, updated.call_id, values)
            elif not is_active(updated.status):
                dispatch_failed_call(db, updated.call_id, values["failure_reason"])
            event.status = "processed"
            event.processed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            # The failure usually happened during a flush, which leaves the
            # session in a rolled-back state where any further commit raises
            # PendingRollbackError. Committing the failure record directly would
            # therefore replace the real error with a confusing one and leave the
            # event stuck on "received" forever.
            logger.exception("TeleExpert webhook event %s failed", event_id)
            db.rollback()
            failed = db.get(TeleExpertWebhookEvent, event_id)
            if failed is not None:
                failed.status = "failed"
                failed.error = str(exc)[:2000]
                db.commit()


def drain_failed_events(max_attempts: int = MAX_EVENT_ATTEMPTS) -> list[str]:
    """Retry events that failed after the webhook already answered 2xx.

    TeleExpert stops redelivering once it has seen any 2xx (``api.md:418``), so a
    transient provider failure would otherwise lose that call's answers for good.
    Attempts are capped, because an event failing for a permanent reason must not
    be retried on every scheduler poll.

    Returns the event ids that processed successfully on this pass.
    """

    with SessionLocal() as db:
        statement = select(TeleExpertWebhookEvent.event_id).where(
            TeleExpertWebhookEvent.status == "failed",
            TeleExpertWebhookEvent.attempts < max_attempts,
        )
        event_ids = list(db.scalars(statement).all())

    recovered: list[str] = []
    for event_id in event_ids:
        process_webhook_event(event_id)
        with SessionLocal() as db:
            event = db.get(TeleExpertWebhookEvent, event_id)
            if event is not None and event.status == "processed":
                recovered.append(event_id)
    if recovered:
        logger.info("Recovered %s failed TeleExpert webhook event(s)", len(recovered))
    return recovered
