"""Operator-only recruitment demo API and a separately signed provider webhook."""

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError
from app.models.recruitment import RecruitmentCampaign, RecruitmentEvent
from app.schemas.recruitment import (RecruitmentConfig, RecruitmentDialInput, RecruitmentLeadInput,
                                     RecruitmentImportInput, RecruitmentUpdate, assistant_payload)
from app.services import recruitment_service as service
from app.services import recruitment_import
from app.core.company_access import workspace_access
from app.models.employer import Employer


operator_access = workspace_access


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request: Request):
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        return private


router = APIRouter(prefix="/recruitment", tags=["recruitment demo"], dependencies=[Depends(operator_access)], route_class=PrivateRoute)
webhook_router = APIRouter(prefix="/recruitment", tags=["recruitment webhook"])


def provider_client():
    client = TeleExpertClient()
    try:
        yield client
    finally:
        client.http_client.close()


def operation(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except service.RecruitmentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeleExpertError as exc:
        # Provider exception bodies may contain transcripts, URLs or credentials.
        raise HTTPException(status_code=502, detail="TeleExpert request failed. Check connection, tenant scopes and provider setup.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/defaults")
def defaults(request: Request, db: Session = Depends(get_db)):
    config = RecruitmentConfig()
    company_id = request.path_params.get('company_id')
    if company_id:
        company = db.get(Employer, company_id)
        if company.name.lower() != 'besingularity':
            config = RecruitmentConfig(name=company.name + ' calls', company_name=company.name,
                purpose='questionnaire', questions=['What would you like our team to help you with?'],
                prompt='Call {{recipient_name}} on behalf of the configured company. Explain the actual contact source: {{contact_source}}. Follow the configured questions, one at a time. Use only company-provided facts. If an answer is unclear, clarify once. Stop on request and do not invent answers or promises.',
                greeting='Hello {{recipient_name}}, I am an AI telephone assistant calling for ' + company.name + '. Is now a good time to speak?',
                contact_source='Replace with the actual source and permission to contact this person')
    return {"config": config.model_dump(mode="json"), "sample_settings": True,
            "assistant_payload": assistant_payload(config),
            "company_name": config.company_name,
            "connection_configured": bool(settings.teleexpert_base_url and settings.teleexpert_api_key),
            "webhook_configured": bool(settings.recruitment_webhook_url and settings.teleexpert_webhook_secret)}


@router.get("/campaigns")
def list_campaigns(request: Request, include_archived: bool = False, db: Session = Depends(get_db)):
    query = select(RecruitmentCampaign).order_by(RecruitmentCampaign.created_at.desc())
    if not include_archived:
        query = query.where(RecruitmentCampaign.archived.is_(False))
    if request.path_params.get('company_id'):
        query = query.where(RecruitmentCampaign.company_id == request.path_params['company_id'])
    values = db.scalars(query).all()
    return {"campaigns": [service.campaign_view(value) for value in values]}


@router.get("/connection")
def connection(client=Depends(provider_client)):
    return operation(service.connection_status, client)


@router.post("/campaigns", status_code=201)
def create_campaign(data: RecruitmentConfig, request: Request, db: Session = Depends(get_db)):
    return service.campaign_view(operation(service.save_campaign, db, data, company_id=request.path_params.get('company_id')))


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, data: RecruitmentUpdate, db: Session = Depends(get_db)):
    config = RecruitmentConfig.model_validate(data.model_dump(exclude={"expected_revision"}))
    return service.campaign_view(operation(service.save_campaign, db, config, campaign_id, data.expected_revision))


@router.get("/campaigns/{campaign_id}")
def get_workspace(campaign_id: str, db: Session = Depends(get_db)):
    return operation(service.workspace, db, campaign_id)


@router.get("/campaigns/{campaign_id}/assistant-payload")
def assistant_definition(campaign_id: str, db: Session = Depends(get_db)):
    current = operation(service.campaign, db, campaign_id)
    return assistant_payload(RecruitmentConfig.model_validate(current.config))


@router.post("/campaigns/{campaign_id}/prepare")
def prepare_campaign(campaign_id: str, db: Session = Depends(get_db), client=Depends(provider_client)):
    return service.campaign_view(operation(service.prepare_assistant, db, campaign_id, client))


@router.post("/campaigns/{campaign_id}/leads", status_code=201)
def add_contact(campaign_id: str, data: RecruitmentLeadInput, db: Session = Depends(get_db)):
    value = operation(service.add_lead, db, campaign_id, data)
    return {"lead_id": value.lead_id}


@router.post("/campaigns/{campaign_id}/leads/import/preview")
async def preview_contacts(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > recruitment_import.MAX_BYTES:
            raise HTTPException(status_code=413, detail="Excel file must be 2 MB or smaller")
        content.extend(chunk)
    return operation(recruitment_import.preview_excel, db, campaign_id, bytes(content))


@router.post("/campaigns/{campaign_id}/leads/import", status_code=201)
def import_contacts(campaign_id: str, data: RecruitmentImportInput, db: Session = Depends(get_db)):
    return operation(recruitment_import.import_contacts, db, campaign_id, data)


@router.post("/campaigns/{campaign_id}/leads/{lead_id}/call", status_code=202)
def dial(campaign_id: str, lead_id: str, data: RecruitmentDialInput,
         db: Session = Depends(get_db), client=Depends(provider_client)):
    return service.call_view(operation(service.new_call, db, campaign_id, lead_id, str(data.request_id), client))


@router.post("/calls/{request_id}/resume")
def resume(request_id: str, data: RecruitmentDialInput, db: Session = Depends(get_db), client=Depends(provider_client)):
    if str(data.request_id) != request_id:
        raise HTTPException(status_code=400, detail="Request ID must match the existing submission")
    return service.call_view(operation(service.resume_submission, db, request_id, client))


@router.post("/calls/{request_id}/sync")
def sync(request_id: str, db: Session = Depends(get_db), client=Depends(provider_client)):
    return service.call_view(operation(service.sync_call, db, request_id, client))


@router.post("/calls/{request_id}/cancel")
def cancel(request_id: str, db: Session = Depends(get_db), client=Depends(provider_client)):
    return service.call_view(operation(service.cancel_call, db, request_id, client))


@router.post("/calls/{request_id}/analysis/retry")
def retry_analysis(request_id: str, db: Session = Depends(get_db), client=Depends(provider_client)):
    return service.call_view(operation(service.retry_call_analysis, db, request_id, client))


@webhook_router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    if len(settings.teleexpert_webhook_secret) < 32:
        raise HTTPException(status_code=503, detail="Recruitment webhook secret is not configured")
    event_id = request.headers.get("Webhook-Id", "")
    timestamp = request.headers.get("Webhook-Timestamp", "")
    signature = request.headers.get("Webhook-Signature", "")
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    if not event_id or len(event_id) > 100 or not fresh:
        raise HTTPException(status_code=401, detail="Invalid webhook headers")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 256 * 1024:
            raise HTTPException(status_code=413, detail="Webhook exceeds the request limit")
        raw.extend(chunk)
    signed = event_id.encode() + b"." + timestamp.encode() + b"." + raw
    expected = "v1," + base64.b64encode(hmac.new(settings.teleexpert_webhook_secret.encode(), signed, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        data = json.loads(raw)
        payload = data["data"]["call"]
        provider_id = payload["id"]
        status = payload["status"]
        if (data["id"] != event_id or data["type"] != f"call.{status}" or
                status not in {"completed", "failed", "cancelled"} or
                not isinstance(provider_id, str) or not service.PROVIDER_ID.fullmatch(provider_id)):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid terminal call event")
    payload_hash = hashlib.sha256(raw).hexdigest()
    existing = db.get(RecruitmentEvent, event_id)
    if existing is None:
        # Store only status/analysis needed for reconciliation, not raw audio,
        # phone details, transcript copies or arbitrary provider metadata.
        result = payload.get("result")
        safe = {key: payload.get(key) for key in ("id", "status", "assistant_id", "assistant_version", "updated_at")}
        safe["result"] = {"analysis": result.get("analysis")} if isinstance(result, dict) else {}
        db.add(RecruitmentEvent(event_id=event_id, provider_call_id=provider_id, payload=safe, payload_hash=payload_hash))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        existing = db.get(RecruitmentEvent, event_id)
    if existing.payload_hash != payload_hash:
        raise HTTPException(status_code=409, detail="Webhook ID reused with different content")
    # Received events remain recoverable if the process stops between the inbox
    # commit and processing, including events arriving before POST acceptance.
    operation(service.drain_events, db)
    return {"status": "accepted", "event_id": event_id}
