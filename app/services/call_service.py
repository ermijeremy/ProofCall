"""Call submission, status synchronization, and completion processing."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError
from app.models.call import TeleExpertCall
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.schemas.call import CallCreate


def submit_teleexpert_call(
    db: Session,
    data: CallCreate,
    client: TeleExpertClient | None = None,
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

    response = (client or TeleExpertClient()).get_call(call.call_id)
    values = {
        "status": response.get("status", call.status),
        "transcript": response.get("transcript", call.transcript),
        "transcript_turns": response.get("transcript_turns", call.transcript_turns),
        "language": response.get("language", call.language),
        "audio_url": response.get("audio_url", call.audio_url),
        "failure_reason": response.get("failure_reason", call.failure_reason),
    }
    if values["status"] == "completed" and call.completed_at is None:
        values["completed_at"] = datetime.utcnow()
    return repository.update(call, values)


def cancel_call(db: Session, call_id: str, client: TeleExpertClient | None = None) -> TeleExpertCall:
    repository = CallRepository(db)
    call = repository.get(call_id)
    if call is None:
        raise ValueError(f"Call not found: {call_id}")
    if not call.call_id.startswith("demo_call_"):
        (client or TeleExpertClient()).cancel_call(call.call_id)
    return repository.update(call, {"status": "cancelled"})
