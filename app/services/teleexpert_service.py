"""TeleExpert response normalization and webhook event processing."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.integrations.teleexpert_client import TeleExpertClient
from app.models.call import TeleExpertCall
from app.models.webhook import TeleExpertWebhookEvent
from app.repositories.calls import CallRepository
from app.services.completion_service import process_completed_call


def transcript_from_turns(turns: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{turn.get('role', turn.get('speaker', 'unknown'))}: {turn.get('text', '')}".strip()
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


def process_webhook_event(event_id: str) -> None:
    """Process a durably stored event outside the webhook request."""
    with SessionLocal() as db:
        event = db.get(TeleExpertWebhookEvent, event_id)
        if event is None or event.status == "processed":
            return
        try:
            call = CallRepository(db).get(event.call_id)
            if call is None:
                raise ValueError(f"Call not found: {event.call_id}")
            call_payload = event.payload["data"]["call"]
            client = TeleExpertClient()
            updated = update_call_from_status(db, call, call_payload)
            if updated.status == "completed":
                values = result_values(call_payload, updated)
                if values["audio_url"]:
                    values["audio_url"] = client.absolute_url(values["audio_url"])
                if not values["transcript_turns"] and not values["transcript"]:
                    transcript_payload = client.get_transcript(updated.call_id)
                    values["transcript_turns"] = transcript_payload.get("turns", [])
                    values["transcript"] = transcript_from_turns(values["transcript_turns"])
                if not values["transcript"]:
                    raise ValueError("Completed TeleExpert call did not include a transcript")
                process_completed_call(
                    db,
                    updated.call_id,
                    values["transcript"],
                    values["audio_url"],
                    values["transcript_turns"] or [],
                    values["language"],
                )
            event.status = "processed"
            event.processed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            event.status = "failed"
            event.error = str(exc)[:2000]
            db.commit()
