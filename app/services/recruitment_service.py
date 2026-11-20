"""Single-contact recruitment orchestration for TeleExpert Vapi-parity.

No local LLM, worker-verification rules, synthetic dialing fallback or auto-redial.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError, TeleExpertHTTPError
from app.models.recruitment import RecruitmentCampaign, RecruitmentCall, RecruitmentEvent, RecruitmentLead
from app.schemas.recruitment import RecruitmentConfig, RecruitmentLeadInput, RecruitmentOutcome, assistant_payload

TERMINAL = {"completed", "failed", "cancelled", "rejected"}
PROVIDER_STATES = {"queued", "rate_wait", "dispatching", "dialing", "not_answered", "retry_wait",
                   "in_progress", "processing", "completed", "failed", "cancelled"}
PROVIDER_ID = re.compile(r"^[0-9a-f]{32}$")
PHASE = {name: i for i, name in enumerate(("queued", "rate_wait", "dispatching", "dialing", "not_answered",
                                          "retry_wait", "in_progress", "processing"))}


class RecruitmentConflict(ValueError):
    pass


def campaign(db: Session, campaign_id: str) -> RecruitmentCampaign:
    value = db.get(RecruitmentCampaign, campaign_id)
    if value is None:
        raise LookupError("Recruitment campaign not found")
    return value


def call(db: Session, request_id: str) -> RecruitmentCall:
    value = db.get(RecruitmentCall, request_id)
    if value is None:
        raise LookupError("Recruitment call not found")
    return value


def connection_ready(client: TeleExpertClient) -> None:
    parsed = urlsplit(client.base_url)
    if not parsed.hostname or parsed.scheme not in {"http", "https"} or not client.api_key:
        raise ValueError("Configure TELEEXPERT_BASE_URL and a tenant TELEEXPERT_API_KEY before calling")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a base URL without embedded credentials, query or fragment")


def context_hash(client: TeleExpertClient) -> str:
    # A key must never be replayed against a different tenant or callback secret.
    context = [client.base_url, client.api_key, settings.teleexpert_webhook_secret]
    return hashlib.sha256(json.dumps(context).encode()).hexdigest()


def replay_deadline(client: TeleExpertClient, created_at: datetime) -> datetime:
    capabilities = client.provider_capabilities()
    submission = capabilities.get("call_submission")
    ttl = submission.get("idempotency_ttl_seconds") if isinstance(submission, dict) else None
    if type(ttl) is not int or ttl < 60:
        raise ValueError("Provider must report its durable call-submission idempotency window")
    return created_at + timedelta(seconds=min(ttl - 30, 20 * 3600))


def variables(config: RecruitmentConfig, lead: RecruitmentLead | None = None) -> dict:
    data = config.model_dump(mode="json")
    return {key: data[key] for key in ("training_location", "training_date", "session_one", "session_two", "clock_convention", "timezone")} | {
        "recipient_name": lead.name if lead else "Demo recipient",
        "contact_source": lead.contact_source if lead else config.contact_source,
    }


def save_campaign(db: Session, config: RecruitmentConfig, campaign_id: str | None = None,
                  expected_revision: int | None = None) -> RecruitmentCampaign:
    if campaign_id is None:
        value = RecruitmentCampaign(campaign_id=f"recruit_{uuid4().hex}", config=config.model_dump(mode="json"))
        db.add(value)
        db.commit()
        return value
    result = db.execute(update(RecruitmentCampaign).where(
        RecruitmentCampaign.campaign_id == campaign_id,
        RecruitmentCampaign.revision == expected_revision,
    ).values(config=config.model_dump(mode="json"), revision=RecruitmentCampaign.revision + 1,
             assistant_id=None, assistant_version=None))
    if result.rowcount != 1:
        db.rollback()
        raise RecruitmentConflict("Campaign changed; reload before saving")
    db.commit()
    db.expire_all()
    return campaign(db, campaign_id)


def prepare_assistant(db: Session, campaign_id: str, client: TeleExpertClient) -> RecruitmentCampaign:
    connection_ready(client)
    current = campaign(db, campaign_id)
    revision = current.revision
    config = RecruitmentConfig.model_validate(current.config)
    if current.assistant_id:
        # Preview verifies that the pinned version, variables, provider and
        # analysis prerequisites remain usable; it never places a phone call.
        client.preview_assistant(current.assistant_id, current.assistant_version, variables(config))
        return current
    response = client.create_assistant(assistant_payload(config))
    assistant_id, version = response.get("id"), response.get("version")
    if not isinstance(assistant_id, str) or not re.fullmatch(r"asst_[0-9a-f]{32}", assistant_id):
        raise ValueError("Provider did not return a valid assistant ID")
    if type(version) is not int or version < 1:
        raise ValueError("Provider did not return a valid assistant version")
    client.preview_assistant(assistant_id, version, variables(config))
    result = db.execute(update(RecruitmentCampaign).where(
        RecruitmentCampaign.campaign_id == campaign_id,
        RecruitmentCampaign.revision == revision,
        RecruitmentCampaign.assistant_id.is_(None),
    ).values(assistant_id=assistant_id, assistant_version=version))
    if result.rowcount != 1:
        db.rollback()
        raise RecruitmentConflict("Campaign changed during assistant preparation; reload it")
    db.commit()
    db.expire_all()
    return campaign(db, campaign_id)


def add_lead(db: Session, campaign_id: str, data: RecruitmentLeadInput) -> RecruitmentLead:
    campaign(db, campaign_id)
    if not data.contact_permission:
        raise ValueError("Only add a contact you have permission to call")
    existing = db.scalar(select(RecruitmentLead).where(
        RecruitmentLead.campaign_id == campaign_id, RecruitmentLead.phone_number == data.phone_number))
    if existing:
        raise RecruitmentConflict("This contact is already in the campaign")
    value = RecruitmentLead(lead_id=f"lead_{uuid4().hex}", campaign_id=campaign_id, **data.model_dump())
    db.add(value)
    db.commit()
    return value


def new_call(db: Session, campaign_id: str, lead_id: str, request_id: str,
             client: TeleExpertClient) -> RecruitmentCall:
    existing = db.get(RecruitmentCall, request_id)
    if existing:
        if existing.campaign_id != campaign_id or existing.lead_id != lead_id:
            raise RecruitmentConflict("Request ID already belongs to another intended call")
        return existing
    current = campaign(db, campaign_id)
    lead = db.get(RecruitmentLead, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise LookupError("Contact not found in this campaign")
    suppressed = db.scalar(select(RecruitmentLead.lead_id).where(
        RecruitmentLead.phone_number == lead.phone_number, RecruitmentLead.do_not_contact.is_(True)).limit(1))
    if not lead.contact_permission or suppressed:
        raise RecruitmentConflict("This contact is not eligible to call")
    if not current.assistant_id:
        raise RecruitmentConflict("Prepare the current campaign assistant before calling")
    connection_ready(client)
    # Leave margin for transit; never assume the server uses its default 24h.
    replay_until = replay_deadline(client, datetime.utcnow())
    config = RecruitmentConfig.model_validate(current.config)
    payload = {
        "phone_number": lead.phone_number, "assistant_id": current.assistant_id,
        "assistant_version": current.assistant_version, "variables": variables(config, lead),
        "task": "Run the configured BeSingularity recruitment invitation. Record attendance intent, not registration.",
        "response_format": "text", "retries": 0, "answer_timeout_seconds": 45,
        "consent_evidence": {"captured_at": lead.created_at.replace(tzinfo=UTC).timestamp(),
                             "method": "operator_attestation", "reference": lead.contact_source},
    }
    if settings.recruitment_webhook_url:
        if len(settings.teleexpert_webhook_secret) < 32:
            raise ValueError("Configure a webhook secret of at least 32 characters")
        payload["webhook"] = {"url": settings.recruitment_webhook_url}
    value = RecruitmentCall(
        request_id=request_id, campaign_id=campaign_id, lead_id=lead_id,
        active_lead_id=hashlib.sha256(lead.phone_number.encode()).hexdigest(),
        request_payload=payload, context_hash=context_hash(client),
        replay_until=replay_until,
        campaign_snapshot={"revision": current.revision, **current.config},
    )
    db.add(value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.get(RecruitmentCall, request_id)
        if existing and existing.campaign_id == campaign_id and existing.lead_id == lead_id:
            return existing
        raise RecruitmentConflict("This contact already has an outstanding call; resolve it first") from exc
    return _dispatch(db, value, client)


def resume_submission(db: Session, request_id: str, client: TeleExpertClient) -> RecruitmentCall:
    current = call(db, request_id)
    if current.provider_call_id or current.status in TERMINAL:
        return current
    now = datetime.utcnow()
    if now >= current.replay_until:
        raise RecruitmentConflict("Submission replay window expired; reconcile with TeleExpert before another call")
    if current.context_hash != context_hash(client):
        raise RecruitmentConflict("Provider or credential configuration changed; restore the original context to reconcile")
    if now >= replay_deadline(client, current.created_at):
        raise RecruitmentConflict("Provider idempotency retention changed; reconcile the existing call before replay")
    claim = db.execute(update(RecruitmentCall).where(
        RecruitmentCall.request_id == request_id,
        (RecruitmentCall.status == "submission_unknown") |
        ((RecruitmentCall.status == "submitting") & (RecruitmentCall.updated_at < now - timedelta(minutes=2))),
    ).values(status="submitting", updated_at=now))
    if claim.rowcount != 1:
        db.rollback()
        raise RecruitmentConflict("Submission is still in progress; wait before checking it again")
    db.commit()
    db.refresh(current)
    return _dispatch(db, current, client)


def _dispatch(db: Session, current: RecruitmentCall, client: TeleExpertClient) -> RecruitmentCall:
    payload = json.loads(json.dumps(current.request_payload))
    if "webhook" in payload:
        payload["webhook"]["secret"] = settings.teleexpert_webhook_secret
    try:
        response = client.submit_assistant_call(payload, current.request_id)
        provider_id = response.get("id")
        state = response.get("status")
        if (not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id) or
                not isinstance(state, str) or state not in PROVIDER_STATES):
            raise TeleExpertError("Invalid call acceptance response")
        current.provider_call_id = provider_id
        db.commit()
        apply_status(db, current, response)
        drain_events(db)
    except TeleExpertHTTPError as exc:
        if 400 <= exc.status_code < 500 and exc.status_code not in {408, 409, 429}:
            current.status = "rejected"
            current.active_lead_id = None
            current.error = f"TeleExpert rejected the request ({exc.status_code}); check tenant permissions and campaign setup."
        else:
            current.status = "submission_unknown"
            current.error = "Acceptance is unresolved. Check the same submission; do not create another call."
        current.updated_at = datetime.utcnow()
        db.commit()
    except TeleExpertError:
        current.status = "submission_unknown"
        current.error = "Acceptance is unresolved. Check the same submission; do not create another call."
        current.updated_at = datetime.utcnow()
        db.commit()
    return current


def apply_status(db: Session, current: RecruitmentCall, payload: dict, *, authoritative: bool = False) -> RecruitmentCall:
    if payload.get("id") != current.provider_call_id:
        raise ValueError("Provider call ID does not match the intended call")
    status = payload.get("status")
    if not isinstance(status, str) or status not in PROVIDER_STATES:
        raise ValueError("Unknown provider call status")
    for _ in range(3):
        # Polling and webhook workers may have read different snapshots. Claim
        # the exact version read before writing; a late poll cannot rewind it.
        db.refresh(current)
        if current.status in TERMINAL and status != current.status:
            return current
        timestamp = payload.get("updated_at")
        provider_time = None
        if isinstance(timestamp, str):
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    provider_time = parsed.astimezone(UTC).replace(tzinfo=None)
            except ValueError:
                pass
        if provider_time and current.provider_updated_at and provider_time < current.provider_updated_at:
            return current
        if (not provider_time and status not in TERMINAL and
                PHASE.get(status, -1) < PHASE.get(current.status, -1)):
            return current
        previous_update = current.updated_at
        values = {"status": status, "updated_at": datetime.utcnow(), "error": None}
        if provider_time:
            values["provider_updated_at"] = provider_time
        outcome = None
        if status in TERMINAL:
            if status == "completed":
                result = payload.get("result")
                result = result if isinstance(result, dict) else {}
                try:
                    if (payload.get("assistant_id") != current.request_payload["assistant_id"] or
                            payload.get("assistant_version") != current.request_payload["assistant_version"]):
                        raise ValueError("Unexpected assistant version")
                    outcome = RecruitmentOutcome.model_validate(result.get("analysis"))
                    values.update(result=outcome.model_dump(), result_state="ready", active_lead_id=None)
                except (ValidationError, ValueError):
                    if current.result_state != "ready":
                        values.update(result_state="needs_review" if authoritative else "pending", error=(
                            "The conversation ended, but valid recruitment analysis from the pinned assistant "
                            "is unavailable. Review in TeleExpert."))
            else:
                values.update(active_lead_id=None, result_state="not_available", error=("Call cancelled." if status == "cancelled"
                              else "The call did not complete. Check TeleExpert for the technical reason."))
        changed = db.execute(update(RecruitmentCall).where(
            RecruitmentCall.request_id == current.request_id,
            RecruitmentCall.updated_at == previous_update,
        ).values(**values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            continue
        if outcome and outcome.do_not_contact:
            lead = db.get(RecruitmentLead, current.lead_id)
            db.execute(update(RecruitmentLead).where(RecruitmentLead.phone_number == lead.phone_number)
                       .values(do_not_contact=True))
        db.commit()
        db.refresh(current)
        return current
    raise RecruitmentConflict("Call status changed concurrently; refresh it again")


def sync_call(db: Session, request_id: str, client: TeleExpertClient) -> RecruitmentCall:
    current = call(db, request_id)
    if not current.provider_call_id:
        return current
    if current.context_hash != context_hash(client):
        raise RecruitmentConflict("Restore the original provider/credential context before synchronizing this call")
    try:
        return apply_status(db, current, client.get_call(current.provider_call_id), authoritative=True)
    except TeleExpertError:
        current.error = "Status could not be refreshed. The existing call has not been redialed."
        db.commit()
        return current


def cancel_call(db: Session, request_id: str, client: TeleExpertClient) -> RecruitmentCall:
    current = call(db, request_id)
    if current.status in TERMINAL:
        return current
    if not current.provider_call_id:
        raise RecruitmentConflict("Resolve the existing submission before cancelling it in TeleExpert")
    if current.context_hash != context_hash(client):
        raise RecruitmentConflict("Restore the original provider/credential context before cancelling this call")
    return apply_status(db, current, client.cancel_call(current.provider_call_id))


def drain_events(db: Session) -> int:
    processed = 0
    for event in db.scalars(select(RecruitmentEvent).where(RecruitmentEvent.processed.is_(False))).all():
        current = db.scalar(select(RecruitmentCall).where(RecruitmentCall.provider_call_id == event.provider_call_id))
        if current:
            apply_status(db, current, event.payload)
            event.processed = True
            event.payload = {}  # Keep event/hash audit, not another transcript copy.
            db.commit()
            processed += 1
        elif event.received_at < datetime.utcnow() - timedelta(days=1):
            event.payload = {}
            event.processed = True
            db.commit()
    return processed


def reconcile(db: Session, client: TeleExpertClient | None = None) -> None:
    """Repair received events and poll known active calls; never originate calls."""
    drain_events(db)
    pending = db.scalars(select(RecruitmentCall).where(
        RecruitmentCall.provider_call_id.is_not(None), RecruitmentCall.active_lead_id.is_not(None),
        (~RecruitmentCall.status.in_(TERMINAL)) | (RecruitmentCall.result_state == "pending"))).all()
    if not pending:
        return
    owns_client = client is None
    client = client or TeleExpertClient()
    try:
        for current in pending:
            try:
                sync_call(db, current.request_id, client)
            except (RecruitmentConflict, ValueError):
                db.rollback()
                current.error = "Status requires review: check provider configuration and the saved call contract."
                db.commit()
    finally:
        if owns_client:
            client.http_client.close()


def campaign_view(current: RecruitmentCampaign) -> dict:
    return {"campaign_id": current.campaign_id, "revision": current.revision, "config": current.config,
            "assistant_id": current.assistant_id, "assistant_version": current.assistant_version}


def call_view(current: RecruitmentCall) -> dict:
    return {"request_id": current.request_id, "lead_id": current.lead_id,
            "provider_call_id": current.provider_call_id, "status": current.status,
            "outstanding": current.active_lead_id is not None,
            "result_state": current.result_state, "result": current.result, "error": current.error,
            "assistant_id": current.request_payload["assistant_id"],
            "assistant_version": current.request_payload["assistant_version"],
            "campaign_revision": current.campaign_snapshot["revision"],
            "training": {key: current.request_payload["variables"][key] for key in
                         ("training_location", "training_date", "session_one", "session_two", "clock_convention")},
            "created_at": current.created_at.replace(tzinfo=UTC).isoformat(),
            "updated_at": current.updated_at.replace(tzinfo=UTC).isoformat()}


def workspace(db: Session, campaign_id: str) -> dict:
    current = campaign(db, campaign_id)
    leads = db.scalars(select(RecruitmentLead).where(RecruitmentLead.campaign_id == campaign_id)
                       .order_by(RecruitmentLead.created_at)).all()
    calls = db.scalars(select(RecruitmentCall).where(RecruitmentCall.campaign_id == campaign_id)
                       .order_by(RecruitmentCall.created_at.desc())).all()
    return {"campaign": campaign_view(current), "leads": [
        {"lead_id": lead.lead_id, "name": lead.name, "phone_number": lead.phone_number,
         "contact_source": lead.contact_source, "contact_permission": lead.contact_permission,
         "do_not_contact": lead.do_not_contact} for lead in leads],
        "calls": [call_view(item) for item in calls]}
