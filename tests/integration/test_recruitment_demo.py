"""Recruitment UI/API → real HTTP client → fake Vapi-parity wire contract.

No models, devices or remote accounts are contacted. Contract fields come from
TeleExpert feat/vapi-parity-control-plane's api.py, assistants.py and OpenAPI.
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import pages, recruitment
from app.core.config import settings
from app.db.session import get_db
from app.integrations.teleexpert_client import TeleExpertClient
from app.models.recruitment import RecruitmentCall, RecruitmentEvent, RecruitmentLead
from app.services import recruitment_service as service

pytestmark = pytest.mark.integration
TOKEN = "FAKE_OPERATOR_" + "x" * 32
SECRET = "FAKE_WEBHOOK_" + "y" * 32
ASSISTANT = "asst_" + "a" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/recruitment"


def analysis(outcome="interested", **changes):
    return {
        "outcome": outcome, "interest": "yes", "work_preference": "part_time",
        "attendance_intent": "yes", "preferred_session": "second", "callback_request": "",
        "do_not_contact": False, "unresolved_questions": [], "summary": "Plans to attend the second session.",
        **changes,
    }


class Provider:
    def __init__(self):
        self.calls = {}
        self.bodies = {}
        self.submissions = 0
        self.executions = 0
        self.definition = None
        self.lose_response = False
        self.reject = False
        self.ttl = 86400

    def handle(self, request):
        path = request.url.path
        assert request.headers["Authorization"] == "Bearer FAKE_PROVIDER"
        if path == "/v1/provider-capabilities":
            return httpx.Response(200, json={"call_submission": {"idempotency_ttl_seconds": self.ttl}})
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/assistants":
            assert set(body) == {"name", "definition"}
            self.definition = body["definition"]
            # This assistant uses the platform's inline analysis schema, so it
            # requires no second local extractor or reusable-output resource.
            assert self.definition["analysis_schema"]["additionalProperties"] is False
            return httpx.Response(201, json={"id": ASSISTANT, "version": 1, "definition": self.definition})
        if path == f"/v1/assistants/{ASSISTANT}/preview":
            assert set(body) == {"version", "variables"}
            assert body["version"] == 1
            assert set(body["variables"]) == set(self.definition["variable_schema"]["required"])
            return httpx.Response(200, json={"assistant_id": ASSISTANT, "assistant_version": 1, "profile": {}})
        if path == "/v1/calls" and request.method == "POST":
            self.submissions += 1
            if self.reject:
                return httpx.Response(403, text="synthetic private provider error FAKE_SECRET")
            assert "prompt" not in body
            assert body["assistant_id"] == ASSISTANT
            assert body["assistant_version"] == 1
            assert body["retries"] == 0
            assert body["response_format"] == "text"
            assert body["webhook"]["secret"] == SECRET
            assert body["consent_evidence"]["method"] == "operator_attestation"
            key = request.headers["Idempotency-Key"]
            assert key.startswith("proofcall-recruitment:")
            if key in self.bodies:
                assert self.bodies[key] == body, "Replays must use the original immutable request"
                return httpx.Response(202, json=self.calls[key])
            self.executions += 1
            provider_id = f"{self.executions:032x}"
            self.bodies[key] = body
            self.calls[key] = {"id": provider_id, "status": "queued", "result": {},
                               "assistant_id": ASSISTANT, "assistant_version": 1}
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadError("accepted then disconnected", request=request)
            return httpx.Response(202, json=self.calls[key])
        if path.startswith("/v1/calls/"):
            value = next(item for item in self.calls.values() if item["id"] == path.rsplit("/", 1)[1])
            if request.method == "DELETE":
                value.update(status="cancelled", result={})
            return httpx.Response(200, json=value)
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    def complete(self, request_id, value):
        result = self.calls["proofcall-recruitment:" + request_id]
        result.update(status="completed", result={"analysis": value})
        return result


@pytest.fixture
def setup(db, monkeypatch):
    monkeypatch.setattr(settings, "recruitment_operator_token", TOKEN)
    monkeypatch.setattr(settings, "recruitment_webhook_url", "https://proofcall.test/api/recruitment/webhook")
    monkeypatch.setattr(settings, "teleexpert_webhook_secret", SECRET)
    provider = Provider()
    remote = TeleExpertClient(base_url="https://teleexpert.test", api_key="FAKE_PROVIDER",
                              http_client=httpx.Client(transport=httpx.MockTransport(provider.handle),
                                                       headers={"Authorization": "Bearer FAKE_PROVIDER"}))
    app = FastAPI()
    app.include_router(recruitment.router, prefix="/api")
    app.include_router(recruitment.webhook_router, prefix="/api")
    app.include_router(pages.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[recruitment.provider_client] = lambda: remote
    with TestClient(app) as client:
        yield client, provider, remote
    remote.http_client.close()


def campaign_and_lead(client, *, phone="+251900000001"):
    response = client.post(BASE + "/campaigns", headers=AUTH, json={})
    assert response.status_code == 201, response.text
    campaign = response.json()
    cid = campaign["campaign_id"]
    response = client.post(BASE + f"/campaigns/{cid}/prepare", headers=AUTH)
    assert response.status_code == 200, response.text
    lead = client.post(BASE + f"/campaigns/{cid}/leads", headers=AUTH, json={
        "name": "Demo contact", "phone_number": phone, "contact_source": "Consented test list",
        "contact_permission": True,
    }).json()["lead_id"]
    return cid, lead


def dial(client, cid, lead, request_id=None):
    request_id = request_id or str(uuid4())
    response = client.post(BASE + f"/campaigns/{cid}/leads/{lead}/call", headers=AUTH,
                           json={"request_id": request_id, "confirmed": True})
    return response, request_id


def signed_event(client, call, event_id="evt-recruitment", **changes):
    payload = {"id": event_id, "type": f"call.{call['status']}", "data": {"call": call}, **changes}
    raw = json.dumps(payload, ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    signed = event_id.encode() + b"." + timestamp.encode() + b"." + raw
    signature = "v1," + base64.b64encode(hmac.new(SECRET.encode(), signed, hashlib.sha256).digest()).decode()
    return client.post(BASE + "/webhook", content=raw, headers={"Webhook-Id": event_id,
        "Webhook-Timestamp": timestamp, "Webhook-Signature": signature, "Content-Type": "application/json"})


def test_public_page_has_no_private_bootstrap_and_api_requires_operator(setup, monkeypatch):
    client, _, _ = setup
    response = client.get("/recruitment")
    assert response.status_code == 200
    assert "Conversation prompt" in response.text
    assert TOKEN not in response.text and SECRET not in response.text
    assert client.get(BASE + "/campaigns").status_code == 401
    assert client.get(BASE + "/campaigns", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.setattr(settings, "recruitment_operator_token", "")
    assert client.get(BASE + "/defaults", headers=AUTH).status_code == 503


@pytest.mark.parametrize("outcome,changes", [
    ("interested", {}),
    ("not_interested", {"interest": "no", "attendance_intent": "no", "preferred_session": "unspecified"}),
    ("callback_requested", {"interest": "unknown", "attendance_intent": "unknown", "preferred_session": "unspecified",
                            "callback_request": "ነገ ከሰዓት በኋላ"}),
    ("stop_requested", {"interest": "unknown", "attendance_intent": "unknown", "do_not_contact": True}),
    ("wrong_person", {"interest": "unknown", "attendance_intent": "unknown", "preferred_session": "unspecified"}),
])
def test_end_to_end_recruitment_outcomes_do_not_use_worker_kpis(setup, db, outcome, changes):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    response, request_id = dial(client, cid, lead)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    complete = provider.complete(request_id, analysis(outcome, **changes))
    assert signed_event(client, complete).status_code == 200
    state = client.get(BASE + f"/campaigns/{cid}", headers=AUTH).json()
    result = state["calls"][0]
    assert result["status"] == "completed" and result["result_state"] == "ready"
    assert result["result"]["outcome"] == outcome
    assert result["outstanding"] is False
    assert "good_job" not in json.dumps(state)
    assert provider.executions == 1
    saved = db.get(RecruitmentCall, request_id)
    assert SECRET not in json.dumps(saved.request_payload)
    assert db.scalar(select(RecruitmentEvent)).payload == {}
    assert state["leads"][0]["do_not_contact"] == changes.get("do_not_contact", False)


def test_lost_acceptance_and_early_webhook_reconcile_one_intended_call(setup, db):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    provider.lose_response = True
    first, request_id = dial(client, cid, lead)
    assert first.json()["status"] == "submission_unknown"
    assert provider.submissions == 1  # Transport does not retry a POST silently.
    complete = provider.complete(request_id, analysis())
    assert signed_event(client, complete, "early-event").status_code == 200
    event = db.get(RecruitmentEvent, "early-event")
    assert event.processed is False
    # Another intention cannot bypass the active-contact claim.
    assert dial(client, cid, lead)[0].status_code == 409
    resumed = client.post(BASE + f"/calls/{request_id}/resume", headers=AUTH,
                          json={"request_id": request_id, "confirmed": True})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["result_state"] == "ready"
    assert provider.executions == 1 and provider.submissions == 2
    db.refresh(event)
    assert event.processed is True and event.payload == {}
    assert signed_event(client, complete, "early-event").status_code == 200


def test_polling_processing_then_completed_and_late_events_do_not_rewind(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    provider.calls["proofcall-recruitment:" + request_id]["status"] = "processing"
    value = client.post(BASE + f"/calls/{request_id}/sync", headers=AUTH).json()
    assert value["status"] == "processing" and value["result_state"] == "pending"
    complete = provider.complete(request_id, analysis())
    assert client.post(BASE + f"/calls/{request_id}/sync", headers=AUTH).json()["result_state"] == "ready"
    late = {**complete, "status": "cancelled", "result": {}}
    assert signed_event(client, late, "late-cancel").status_code == 200
    state = client.get(BASE + f"/campaigns/{cid}", headers=AUTH).json()
    assert state["calls"][0]["status"] == "completed"
    assert state["calls"][0]["result_state"] == "ready"


def test_cancellation_is_terminal_and_never_schedules_a_retry(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    response = client.post(BASE + f"/calls/{request_id}/cancel", headers=AUTH)
    assert response.json()["status"] == "cancelled"
    assert response.json()["outstanding"] is False
    assert client.post(BASE + f"/calls/{request_id}/cancel", headers=AUTH).status_code == 200
    assert provider.executions == 1


def test_provider_failure_is_not_an_interview_refusal(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    value = provider.calls["proofcall-recruitment:" + request_id]
    value.update(status="failed", error="gateway disconnected")
    assert signed_event(client, value).status_code == 200
    call = client.get(BASE + f"/campaigns/{cid}", headers=AUTH).json()["calls"][0]
    assert call["status"] == "failed" and call["result_state"] == "not_available"
    assert call["result"] is None and call["outstanding"] is False


@pytest.mark.parametrize("invalid", [None, {}, analysis(do_not_contact="false"), analysis(interest="probably")])
def test_missing_or_invalid_analysis_remains_review_work_and_blocks_redial(setup, invalid):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    provider.complete(request_id, invalid)
    response = client.post(BASE + f"/calls/{request_id}/sync", headers=AUTH).json()
    assert response["status"] == "completed"
    assert response["result_state"] == "needs_review" and response["result"] is None
    assert response["outstanding"] is True
    assert dial(client, cid, lead)[0].status_code == 409
    # A later authoritative corrected result can complete local processing.
    provider.complete(request_id, analysis())
    assert client.post(BASE + f"/calls/{request_id}/sync", headers=AUTH).json()["result_state"] == "ready"


def test_terminal_webhook_without_analysis_is_recovered_by_background_poll(setup, db):
    client, provider, remote = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    complete = provider.complete(request_id, analysis())
    assert signed_event(client, {**complete, "result": {}}).status_code == 200
    assert db.get(RecruitmentCall, request_id).result_state == "pending"
    service.reconcile(db, remote)
    assert db.get(RecruitmentCall, request_id).result_state == "ready"
    assert provider.executions == 1


def test_preparation_and_campaign_edits_pin_call_snapshots(setup, db):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    provider.lose_response = True
    _, request_id = dial(client, cid, lead)
    original = db.get(RecruitmentCall, request_id).request_payload
    config = client.get(BASE + f"/campaigns/{cid}", headers=AUTH).json()["campaign"]["config"]
    config.update(training_location="Different demo venue", prompt=config["prompt"] + "\nSpeak slowly.")
    updated = client.put(BASE + f"/campaigns/{cid}", headers=AUTH, json={**config, "expected_revision": 1})
    assert updated.json()["revision"] == 2 and updated.json()["assistant_id"] is None
    assert client.put(BASE + f"/campaigns/{cid}", headers=AUTH, json={**config, "expected_revision": 1}).status_code == 409
    resumed = client.post(BASE + f"/calls/{request_id}/resume", headers=AUTH,
                          json={"request_id": request_id, "confirmed": True})
    assert resumed.status_code == 200
    assert provider.bodies["proofcall-recruitment:" + request_id] | {} == {
        **original, "webhook": {**original["webhook"], "secret": SECRET}}
    assert resumed.json()["campaign_revision"] == 1


def test_stop_request_suppresses_same_number_across_campaigns(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    complete = provider.complete(request_id, analysis("stop_requested", do_not_contact=True))
    assert signed_event(client, complete).status_code == 200
    other_campaign, other_lead = campaign_and_lead(client)
    assert dial(client, other_campaign, other_lead)[0].status_code == 409
    assert provider.executions == 1


def test_replay_expiry_and_changed_credentials_cannot_start_a_fresh_call(setup, db):
    client, provider, remote = setup
    cid, lead = campaign_and_lead(client)
    provider.ttl = 60
    provider.lose_response = True
    _, request_id = dial(client, cid, lead)
    value = db.get(RecruitmentCall, request_id)
    assert value.replay_until - value.created_at < timedelta(seconds=31)
    remote.api_key = "CHANGED_TENANT"
    assert client.post(BASE + f"/calls/{request_id}/resume", headers=AUTH,
                       json={"request_id": request_id, "confirmed": True}).status_code == 409
    remote.api_key = "FAKE_PROVIDER"
    value.replay_until = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.post(BASE + f"/calls/{request_id}/resume", headers=AUTH,
                       json={"request_id": request_id, "confirmed": True}).status_code == 409
    assert provider.executions == 1 and provider.submissions == 1


def test_repeated_intention_does_not_submit_again_and_wrong_lead_conflicts(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    assert dial(client, cid, lead, request_id)[0].status_code == 202
    assert provider.submissions == 1
    cid2, lead2 = campaign_and_lead(client, phone="+251900000002")
    assert dial(client, cid2, lead2, request_id)[0].status_code == 409


def test_shortened_provider_retention_blocks_old_replay(setup, db):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    provider.lose_response = True
    _, request_id = dial(client, cid, lead)
    value = db.get(RecruitmentCall, request_id)
    value.created_at = datetime.utcnow() - timedelta(minutes=2)
    db.commit()
    provider.ttl = 60
    response = client.post(BASE + f"/calls/{request_id}/resume", headers=AUTH,
                           json={"request_id": request_id, "confirmed": True})
    assert response.status_code == 409
    assert provider.submissions == 1


def test_analysis_from_another_assistant_version_is_not_accepted(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    complete = provider.complete(request_id, analysis())
    complete["assistant_version"] = 2
    response = client.post(BASE + f"/calls/{request_id}/sync", headers=AUTH).json()
    assert response["result_state"] == "needs_review" and response["result"] is None


def test_rejected_provider_request_does_not_expose_error_body(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    provider.reject = True
    response, _ = dial(client, cid, lead)
    assert response.json()["status"] == "rejected"
    assert response.json()["outstanding"] is False
    assert "FAKE_SECRET" not in response.text


def test_stale_poll_from_another_session_cannot_rewind_processing(setup, db):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    old_payload = dict(provider.calls["proofcall-recruitment:" + request_id])
    with Session(db.get_bind()) as first, Session(db.get_bind()) as stale:
        newer = first.get(RecruitmentCall, request_id)
        older = stale.get(RecruitmentCall, request_id)
        service.apply_status(first, newer, {**old_payload, "status": "processing"})
        service.apply_status(stale, older, old_payload)
    db.expire_all()
    assert db.get(RecruitmentCall, request_id).status == "processing"


def test_provider_timestamp_rejects_older_updates(setup, db):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    current = db.get(RecruitmentCall, request_id)
    base = provider.calls["proofcall-recruitment:" + request_id]
    service.apply_status(db, current, {**base, "status": "processing", "updated_at": "2026-11-20T10:05:00+00:00"})
    service.apply_status(db, current, {**base, "status": "failed", "updated_at": "2026-11-20T10:04:00+00:00"})
    assert current.status == "processing"


@pytest.mark.parametrize("confirmed", [False, "true", 1])
def test_call_requires_literal_operator_confirmation(setup, confirmed):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    response = client.post(BASE + f"/campaigns/{cid}/leads/{lead}/call", headers=AUTH,
                           json={"request_id": str(uuid4()), "confirmed": confirmed})
    assert response.status_code == 422 and provider.executions == 0


def test_webhook_signature_and_event_identity_are_enforced(setup):
    client, provider, _ = setup
    cid, lead = campaign_and_lead(client)
    _, request_id = dial(client, cid, lead)
    complete = provider.complete(request_id, analysis())
    assert client.post(BASE + "/webhook", content=b"{}").status_code == 401
    assert signed_event(client, complete).status_code == 200
    changed = {**complete, "result": {"analysis": analysis(interest="no")}}
    assert signed_event(client, changed).status_code == 409
    assert signed_event(client, complete, "different-header", id="different-body").status_code == 400


def test_unknown_prompt_variables_and_nonconsenting_contact_are_rejected(setup):
    client, _, _ = setup
    response = client.post(BASE + "/campaigns", headers=AUTH,
                           json={"prompt": "Hello {{not_a_real_variable}} " + "test " * 20})
    assert response.status_code == 422
    cid, _ = campaign_and_lead(client)
    response = client.post(BASE + f"/campaigns/{cid}/leads", headers=AUTH, json={
        "phone_number": "+251900000003", "contact_source": "test", "contact_permission": "false"})
    assert response.status_code == 422
    response = client.post(BASE + f"/campaigns/{cid}/leads", headers=AUTH, json={
        "phone_number": "+251900000003", "contact_source": "test", "contact_permission": False})
    assert response.status_code == 400
