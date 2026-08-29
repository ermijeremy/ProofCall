"""TeleExpert submission, status, and completion endpoints."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError
from app.models.webhook import TeleExpertWebhookEvent
from app.repositories.calls import CallRepository
from app.schemas.call import CallCreate, CallStatusRead
from app.services.call_service import cancel_call, submit_teleexpert_call, sync_call_status
from app.services.teleexpert_service import process_webhook_event

router = APIRouter(prefix="/teleexpert", tags=["calls"])


@router.post("/calls", response_model=CallStatusRead, status_code=status.HTTP_202_ACCEPTED)
def submit_call(data: CallCreate, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return submit_teleexpert_call(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/calls/{call_id}", response_model=CallStatusRead)
def get_call(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return sync_call_status(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/calls/{call_id}/sync", response_model=CallStatusRead)
def sync_call(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return sync_call_status(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/calls/{call_id}/cancel", response_model=CallStatusRead)
def cancel(call_id: str, db: Session = Depends(get_db)) -> CallStatusRead:
    try:
        return cancel_call(db, call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/calls/{call_id}/transcript")
def transcript(call_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Proxy the authenticated TeleExpert transcript to the dashboard client."""
    if CallRepository(db).get(call_id) is None:
        raise HTTPException(status_code=404, detail=f"Call not found: {call_id}")
    try:
        return TeleExpertClient().get_transcript(call_id)
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/calls/{call_id}/audio")
def audio(call_id: str, db: Session = Depends(get_db)) -> Response:
    """Proxy authenticated TeleExpert audio without exposing its bearer token."""
    if CallRepository(db).get(call_id) is None:
        raise HTTPException(status_code=404, detail=f"Call not found: {call_id}")
    try:
        return Response(content=TeleExpertClient().get_audio(call_id), media_type="audio/wav")
    except TeleExpertError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/webhook")
async def teleexpert_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    webhook_id: str | None = Header(default=None, alias="Webhook-Id"),
    webhook_timestamp: str | None = Header(default=None, alias="Webhook-Timestamp"),
    webhook_signature: str | None = Header(default=None, alias="Webhook-Signature"),
) -> dict[str, Any]:
    """Verify, durably store, and asynchronously process a TeleExpert event."""
    raw_body = await request.body()
    if not settings.teleexpert_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature headers")
    try:
        timestamp = int(webhook_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp") from exc
    if abs(int(time.time()) - timestamp) > 300:
        raise HTTPException(status_code=401, detail="Expired webhook timestamp")
    signed = webhook_id.encode() + b"." + webhook_timestamp.encode("ascii") + b"." + raw_body
    expected = "v1," + base64.b64encode(
        hmac.new(settings.teleexpert_webhook_secret.encode(), signed, hashlib.sha256).digest()
    ).decode("ascii")
    if not hmac.compare_digest(expected, webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object")
    if payload.get("id") != webhook_id:
        raise HTTPException(status_code=400, detail="Webhook ID header does not match payload")
    event_data = payload.get("data")
    call_payload = event_data.get("call") if isinstance(event_data, dict) else None
    call_id = call_payload.get("id") if isinstance(call_payload, dict) else None
    if not call_id:
        raise HTTPException(status_code=400, detail="Webhook payload did not include a call ID")
    if db.get(TeleExpertWebhookEvent, webhook_id) is not None:
        return {"status": "duplicate", "event_id": webhook_id}
    event = TeleExpertWebhookEvent(
        event_id=webhook_id,
        event_type=str(payload.get("type", "unknown")),
        call_id=str(call_id),
        payload=payload,
    )
    db.add(event)
    db.commit()
    background_tasks.add_task(process_webhook_event, webhook_id)
    return {"status": "accepted", "event_id": webhook_id, "call_id": str(call_id)}
