"""Call submission, status synchronization, and completion processing."""

from datetime import datetime
import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError
from app.models.call import TeleExpertCall
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.repositories.evidence import EvidenceRepository
from app.schemas.call import CallCreate
from app.services.teleexpert_service import result_values, transcript_from_turns

ACTIVE_CALL_STATUSES = frozenset({"queued", "dispatching", "dialing", "retry_wait", "in_progress"})
logger = logging.getLogger(__name__)


def submit_teleexpert_call(
    db: Session,
    data: CallCreate,
    client: TeleExpertClient | None = None,
    idempotency_key: str | None = None,
) -> TeleExpertCall:
    if BeneficiaryRepository(db).get(data.worker_id) is None:
        raise ValueError(f"Worker not found: {data.worker_id}")

    remote = client or TeleExpertClient()
    try:
        response = remote.submit_call(
            phone_number=data.phone_number,
            prompt=data.prompt,
            response_format=data.response_format,
            retries=data.retries,
            retry_delay_seconds=data.retry_delay_seconds,
            answer_timeout_seconds=data.answer_timeout_seconds,
            webhook_url=settings.teleexpert_webhook_url or None,
            webhook_secret=settings.teleexpert_webhook_secret or None,
            idempotency_key=idempotency_key,
        )
    except TeleExpertError:
        if settings.teleexpert_base_url:
            raise
        # Local demo mode preserves the same lifecycle until credentials are set.
        response = {"call_id": f"demo_call_{uuid4().hex}", "status": "queued"}

    call = TeleExpertCall(
        call_id=str(response.get("call_id") or response.get("id")),
        worker_id=data.worker_id,
        campaign_id=data.campaign_id,
        phone_number=data.phone_number,
        prompt=data.prompt,
        response_format=data.response_format,
        retries=data.retries,
        retry_delay_seconds=data.retry_delay_seconds,
        answer_timeout_seconds=data.answer_timeout_seconds,
        status=response.get("status", "queued"),
    )
    return CallRepository(db).add(call)


def sync_call_status(db: Session, call_id: str, client: TeleExpertClient | None = None) -> TeleExpertCall:
    repository = CallRepository(db)
    call = repository.get(call_id)
    if call is None:
        raise ValueError(f"Call not found: {call_id}")
    if call.call_id.startswith("demo_call_") and client is None:
        return call

    client = client or TeleExpertClient()
    response = client.get_call(call.call_id)
    values = result_values(response, call)
    if values["status"] == "completed" and not values["transcript_turns"] and not values["transcript"]:
        transcript_payload = client.get_transcript(call.call_id)
        values["transcript_turns"] = transcript_payload.get("turns", [])
        values["transcript"] = transcript_from_turns(values["transcript_turns"])
    if values["audio_url"]:
        values["audio_url"] = client.absolute_url(values["audio_url"])
    if values["status"] == "completed" and call.completed_at is None:
        values["completed_at"] = datetime.utcnow()
    updated = repository.update(call, values)
    # Webhook delivery is the preferred completion path. During local
    # development the webhook may be disabled, so a browser Sync must continue
    # the same transcript -> evidence -> aggregation pipeline. Existing
    # evidence makes repeated Sync clicks idempotent and avoids another LLM call.
    if updated.status == "completed" and updated.transcript and EvidenceRepository(db).get(updated.worker_id) is None:
        from app.services.completion_service import process_completed_call

        process_completed_call(
            db,
            updated.call_id,
            updated.transcript,
            updated.audio_url,
            updated.transcript_turns or [],
            updated.language,
        )
    return updated


def cancel_call(db: Session, call_id: str, client: TeleExpertClient | None = None) -> TeleExpertCall:
    repository = CallRepository(db)
    call = repository.get(call_id)
    if call is None:
        raise ValueError(f"Call not found: {call_id}")
    if not call.call_id.startswith("demo_call_"):
        (client or TeleExpertClient()).cancel_call(call.call_id)
    return repository.update(call, {"status": "cancelled"})


def sync_active_calls(db: Session) -> list[TeleExpertCall]:
    """Poll non-terminal provider calls and process completion automatically."""

    repository = CallRepository(db)
    evidence = EvidenceRepository(db)
    updated: list[TeleExpertCall] = []
    for call in repository.list():
        needs_status_poll = call.status in ACTIVE_CALL_STATUSES
        needs_evidence_retry = call.status == "completed" and evidence.get(call.worker_id) is None
        if needs_status_poll or needs_evidence_retry:
            try:
                updated.append(sync_call_status(db, call.call_id))
            except TeleExpertError as exc:
                # TeleExpert keeps active call state in memory. After its
                # backend restarts, old local IDs permanently return 404 and
                # must not be polled forever.
                if "(404)" in str(exc):
                    updated.append(
                        repository.update(
                            call,
                            {
                                "status": "failed",
                                "failure_reason": "TeleExpert no longer has this call (provider state was reset).",
                            },
                        )
                    )
                    logger.warning("Marked stale TeleExpert call %s as failed", call.call_id)
                else:
                    logger.exception("Automatic synchronization failed for call %s", call.call_id)
            except Exception:
                logger.exception("Automatic synchronization failed for call %s", call.call_id)
    return updated
