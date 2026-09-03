"""TeleExpert response normalization and webhook event processing."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError
from app.models.call import TeleExpertCall
from app.models.webhook import TeleExpertWebhookEvent
from app.repositories.calls import CallRepository

logger = logging.getLogger(__name__)

#: Statuses TeleExpert reports while a call is still in flight. Anything else is
#: terminal, so a call that leaves this set without a transcript never gets one.
ACTIVE_CALL_STATUSES = frozenset({"queued", "dispatching", "dialing", "retry_wait", "in_progress"})

#: How many times a failed event may be drained before it is left alone.
MAX_EVENT_ATTEMPTS = 3


def transcript_from_turns(turns: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{turn.get('role', turn.get('speaker', 'unknown'))}: {turn.get('text', '')}".strip()
        for turn in turns
        if isinstance(turn, dict) and turn.get("text")
    )


def download_audio_artifact(client: TeleExpertClient, call_id: str) -> Path:
    """Fetch completed-call audio once and store it outside the database."""

    audio = client.get_audio(call_id)
    if not audio:
        raise ValueError(f"TeleExpert returned empty audio for call {call_id}")
    safe_call_id = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id)
    directory = Path(settings.callwise_audio_dir)
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob(f"{safe_call_id}_*.wav"))
    path = existing[0] if existing else directory / (
        f"{safe_call_id}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.wav"
    )
    if not path.exists() or path.stat().st_size == 0:
        path.write_bytes(audio)
    logger.info("TeleExpert audio saved call_id=%s path=%s bytes=%s", call_id, path, len(audio))
    return path


def _transcript_metadata(payload: dict[str, Any], call: TeleExpertCall) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Normalize TeleExpert's status, result, and transcript response shapes.

    A completed status response may expose ``result.transcript.turns`` while
    ``GET /transcript`` exposes ``turns`` at the top level.  Both are successful
    transcript responses and must not be confused with a missing result.
    """

    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    nested = result.get("transcript") if isinstance(result.get("transcript"), dict) else {}
    turns = (
        nested.get("turns")
        or result.get("turns")
        or payload.get("turns")
        or payload.get("transcript_turns")
        or call.transcript_turns
        or []
    )
    if not isinstance(turns, list):
        turns = []
    transcript_value = result.get("transcript", payload.get("transcript"))
    transcript = transcript_value if isinstance(transcript_value, str) else None
    if not transcript and turns:
        transcript = transcript_from_turns(turns)
    if not transcript:
        transcript = call.transcript
    language = (
        payload.get("language")
        or payload.get("detected_language")
        or result.get("language")
        or result.get("detected_language")
        or call.language
    )
    return transcript, turns, language


def result_values(payload: dict[str, Any], call: TeleExpertCall) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    transcript, turns, language = _transcript_metadata(payload, call)
    audio_url = result.get("audio_url", payload.get("audio_url", call.audio_url))
    return {
        "status": payload.get("status", call.status),
        "transcript": transcript,
        "transcript_turns": turns,
        "language": language,
        "audio_url": audio_url,
        "failure_reason": payload.get("error", payload.get("failure_reason", call.failure_reason)),
    }


def update_call_from_status(db: Session, call: TeleExpertCall, payload: dict[str, Any]) -> TeleExpertCall:
    values = result_values(payload, call)
    if values["status"] == "completed" and call.completed_at is None:
        values["completed_at"] = datetime.utcnow()
    return CallRepository(db).update(call, values)


def dispatch_completed_call(db: Session, call_id: str, values: dict[str, Any]) -> None:
    """Send a finished Callwise call through the single batch pipeline."""

    # Do not put callers' words in application logs.  The normalized result is
    # logged by the batch pipeline only after consent has been evaluated.
    logger.info(
        "TeleExpert worker response received call_id=%s language=%s turns=%s",
        call_id,
        values.get("language") or "unknown",
        len(values.get("transcript_turns") or []),
    )

    from app.services import batch_service

    if not batch_service.is_batch_call(db, call_id):
        raise ValueError(f"Call {call_id} is not attached to a Callwise batch")
    batch_service.process_batch_call(
        db,
        call_id,
        values["transcript"],
        values["language"],
        values["audio_url"],
        values["transcript_turns"] or [],
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
                # This can happen after the local database is recreated while
                # TeleExpert still drains an older webhook queue. There is no
                # local call to update and retrying can never recover it.
                event.status = "processed"
                event.error = f"Ignored stale event; local call not found: {event.call_id}"
                event.processed_at = datetime.utcnow()
                db.commit()
                logger.warning("Ignored webhook event %s for missing call %s", event_id, event.call_id)
                return
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
                if not values["transcript_turns"]:
                    try:
                        transcript_payload = client.get_transcript(updated.call_id)
                        values["transcript_turns"] = transcript_payload.get("turns", [])
                        if values["transcript_turns"]:
                            values["transcript"] = transcript_from_turns(values["transcript_turns"])
                    except TeleExpertError:
                        if not values["transcript"]:
                            raise
                if not values["transcript"]:
                    raise ValueError("Completed TeleExpert call did not include a transcript")
                dispatch_completed_call(db, updated.call_id, values)
                # Audio is intentionally fetched after extraction. If consent
                # was declined/voided, the batch pipeline has already removed
                # transcript/media references and no audio is retained.
                from app.repositories.batches import WorkerAnswerRecordRepository
                completed_record = WorkerAnswerRecordRepository(db).by_call(updated.call_id)
                if completed_record is not None and completed_record.consent:
                    audio_path = download_audio_artifact(client, updated.call_id)
                    from app.services import batch_service
                    batch_service.attach_local_audio_artifact(updated.call_id, audio_path)
            elif updated.status not in ACTIVE_CALL_STATUSES:
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
