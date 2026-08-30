"""The webhook endpoint and its asynchronous processing, end to end and offline.

Everything a completed interview knows arrives through this one endpoint, so its
failure modes matter more than its happy path. Two of them are load-bearing:

*   A request that fails verification must never create an inbox row. Storing an
    unverified payload would let anybody who can reach the URL write to it.
*   A request that verifies must return 2xx and then process, because TeleExpert
    never redelivers an event it has already had a 2xx for (``api.md:418``). An
    event that fails after that answer has to be recorded as failed so the drain
    can retry it, which is the bug the plan's prerequisite step fixed.

``TestClient`` runs background tasks synchronously once the response is returned,
so the asynchronous half is asserted in the same test as the request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import calls as calls_route
from app.core.config import settings
from app.db.session import get_db
from app.models.call import TeleExpertCall
from app.models.webhook import TeleExpertWebhookEvent
from app.repositories.batches import (
    BatchRepository,
    BatchTargetRepository,
    WorkerAnswersRepository,
)
from app.services import batch_service, teleexpert_service
from app.services.teleexpert_service import drain_failed_events
from tests.fakes import TRANSCRIPTS

pytestmark = pytest.mark.integration

SECRET = "test-webhook-secret"
ROSTER_CSV = b"name,contact\nAbebe Kebede,+251900000001\n"
QUESTIONS_MESSAGE = "1 - what is ur age? 2 - Do u get enough compensation?"


@pytest.fixture()
def client(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The calls router alone, on the test database.

    Built here rather than importing ``app.main`` so no startup hook runs: the
    real one calls ``init_db`` against the on-disk database and registers a
    Gemini engine, neither of which belongs in an offline test.
    """

    api = FastAPI()
    api.include_router(calls_route.router, prefix="/api")

    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    api.dependency_overrides[get_db] = override
    # ``process_webhook_event`` opens its own session, by design: it runs after the
    # response, when the request's session is already closed.
    monkeypatch.setattr(teleexpert_service, "SessionLocal", session_factory)
    monkeypatch.setattr(settings, "teleexpert_webhook_secret", SECRET)
    with TestClient(api) as test_client:
        yield test_client


def sign(event_id: str, timestamp: str, body: bytes, secret: str = SECRET) -> str:
    signed = event_id.encode() + b"." + timestamp.encode("ascii") + b"." + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def event_body(
    event_id: str,
    call_id: str,
    *,
    status: str = "completed",
    turns: list[dict[str, str]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    call: dict[str, Any] = {"id": call_id, "status": status}
    if turns is not None:
        call["result"] = {"transcript": {"turns": turns}}
    if error is not None:
        call["error"] = error
    return {"id": event_id, "type": f"call.{status}", "data": {"call": call}}


def post_event(
    client: TestClient,
    payload: dict[str, Any] | bytes,
    *,
    event_id: str | None = None,
    timestamp: str | None = None,
    signature: str | None = None,
    secret: str = SECRET,
    headers: dict[str, str] | None = None,
):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    identifier = event_id or (payload.get("id") if isinstance(payload, dict) else "evt_1")
    stamp = timestamp or str(int(time.time()))
    sent = {
        "Webhook-Id": identifier,
        "Webhook-Timestamp": stamp,
        "Webhook-Signature": signature or sign(identifier, stamp, body, secret),
        "Content-Type": "application/json",
    }
    if headers is not None:
        sent = {key: value for key, value in sent.items() if key in headers or key == "Content-Type"}
        sent.update(headers)
    return client.post("/api/teleexpert/webhook", content=body, headers=sent)


def batch_status(db: Session, batch_id: str) -> str:
    db.expire_all()
    return BatchRepository(db).get(batch_id).status


def turns_for(name: str) -> list[dict[str, str]]:
    return [
        {"role": line.split(": ", 1)[0], "text": line.split(": ", 1)[1]}
        for line in TRANSCRIPTS[name].splitlines()
    ]


def store_call(db: Session, call_id: str, status: str = "in_progress") -> TeleExpertCall:
    call = TeleExpertCall(
        call_id=call_id,
        worker_id="w_test",
        phone_number="+251900000001",
        language="am",
        prompt="ask the questions",
        status=status,
    )
    db.add(call)
    db.commit()
    return call


def dialed_batch(db: Session, intelligence) -> tuple[Any, str]:
    """A batch with one confirmed, dialed target. Returns the batch and its call id."""

    batch = batch_service.create_batch(db)
    batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "roster.csv")
    batch_service.handle_message(db, batch.batch_id, QUESTIONS_MESSAGE, intelligence)
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    target = BatchTargetRepository(db).for_batch(batch.batch_id)[0]
    return batch, target.call_id


# -- verification -------------------------------------------------------------- #


def test_a_correctly_signed_event_is_accepted_and_processed(client: TestClient, db: Session) -> None:
    store_call(db, "call_ok")
    response = post_event(client, event_body("evt_ok", "call_ok", status="failed", error="no answer"))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "event_id": "evt_ok", "call_id": "call_ok"}
    event = db.get(TeleExpertWebhookEvent, "evt_ok")
    assert event.status == "processed"
    assert event.attempts == 1
    assert event.error is None


def test_a_wrong_signature_is_rejected_and_stores_nothing(client: TestClient, db: Session) -> None:
    response = post_event(client, event_body("evt_bad", "call_bad"), secret="not-the-secret")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"
    # The point of rejecting before the insert: an unverified payload must not be
    # able to put a row in the inbox at all.
    assert db.get(TeleExpertWebhookEvent, "evt_bad") is None


def test_a_tampered_body_no_longer_matches_its_signature(client: TestClient, db: Session) -> None:
    payload = event_body("evt_tamper", "call_tamper")
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    good = sign("evt_tamper", stamp, body)
    tampered = json.dumps(event_body("evt_tamper", "call_someone_else")).encode()

    response = post_event(client, tampered, event_id="evt_tamper", timestamp=stamp, signature=good)

    assert response.status_code == 401
    assert db.get(TeleExpertWebhookEvent, "evt_tamper") is None


@pytest.mark.parametrize(
    "present",
    [
        {"Webhook-Timestamp", "Webhook-Signature"},
        {"Webhook-Id", "Webhook-Signature"},
        {"Webhook-Id", "Webhook-Timestamp"},
        set(),
    ],
)
def test_missing_signature_headers_are_rejected(client: TestClient, present: set[str]) -> None:
    payload = event_body("evt_headers", "call_headers")
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    available = {
        "Webhook-Id": "evt_headers",
        "Webhook-Timestamp": stamp,
        "Webhook-Signature": sign("evt_headers", stamp, body),
    }
    headers = {key: value for key, value in available.items() if key in present}

    response = post_event(client, body, headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing webhook signature headers"


def test_a_non_numeric_timestamp_is_rejected(client: TestClient) -> None:
    response = post_event(client, event_body("evt_stamp", "call_stamp"), timestamp="yesterday")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook timestamp"


@pytest.mark.parametrize("offset", [-3600, 3600])
def test_a_timestamp_outside_the_window_is_rejected(client: TestClient, offset: int) -> None:
    """Old and future stamps both fail: the window is what stops replay of a
    captured request once the id has been forgotten."""

    stamp = str(int(time.time()) + offset)
    response = post_event(client, event_body("evt_old", "call_old"), timestamp=stamp)

    assert response.status_code == 401
    assert response.json()["detail"] == "Expired webhook timestamp"


def test_a_replayed_event_id_is_answered_without_processing_again(
    client: TestClient, db: Session
) -> None:
    store_call(db, "call_replay", status="in_progress")
    payload = event_body("evt_replay", "call_replay", status="failed", error="busy")

    first = post_event(client, payload)
    second = post_event(client, payload)

    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "event_id": "evt_replay"}
    event = db.get(TeleExpertWebhookEvent, "evt_replay")
    # One attempt, not two: the duplicate short-circuits before the background task.
    assert event.attempts == 1


def test_the_endpoint_refuses_to_run_without_a_configured_secret(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "teleexpert_webhook_secret", "")

    response = post_event(client, event_body("evt_nosecret", "call_nosecret"))

    assert response.status_code == 503
    assert response.json()["detail"] == "Webhook secret is not configured"
    assert db.get(TeleExpertWebhookEvent, "evt_nosecret") is None


# -- payload shape ------------------------------------------------------------- #


def test_a_body_that_is_not_json_is_rejected(client: TestClient) -> None:
    response = post_event(client, b"{not json", event_id="evt_json")

    assert response.status_code == 400
    assert response.json()["detail"] == "Webhook payload is not valid JSON"


def test_a_json_body_that_is_not_an_object_is_rejected(client: TestClient) -> None:
    response = post_event(client, b"[1, 2, 3]", event_id="evt_list")

    assert response.status_code == 400
    assert response.json()["detail"] == "Webhook payload must be an object"


def test_a_payload_id_that_disagrees_with_the_header_is_rejected(
    client: TestClient, db: Session
) -> None:
    """Signed, but self-inconsistent: dedupe keys off the header, so accepting a
    mismatch would let the same body be stored twice under two ids."""

    response = post_event(client, event_body("evt_other", "call_mismatch"), event_id="evt_header")

    assert response.status_code == 400
    assert response.json()["detail"] == "Webhook ID header does not match payload"
    assert db.get(TeleExpertWebhookEvent, "evt_header") is None


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"call": {}},
        {"call": "not-an-object"},
        {"call": {"status": "completed"}},
    ],
)
def test_a_payload_without_a_call_id_is_rejected(client: TestClient, data: dict[str, Any]) -> None:
    payload = {"id": "evt_nocall", "type": "call.completed", "data": data}

    response = post_event(client, payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Webhook payload did not include a call ID"


# -- the failure path and the drain -------------------------------------------- #


def test_an_event_for_an_unknown_call_is_recorded_as_failed(client: TestClient, db: Session) -> None:
    """The prerequisite fix. The inner failure poisons the session, so recording it
    needs a rollback first; without that the event stayed on ``received`` with no
    error and was never retried."""

    response = post_event(client, event_body("evt_unknown", "call_missing", turns=turns_for("Marta Alemu")))

    assert response.status_code == 200
    event = db.get(TeleExpertWebhookEvent, "evt_unknown")
    assert event.status == "failed"
    assert event.error == "Call not found: call_missing"
    assert event.attempts == 1


def test_a_completed_event_without_any_transcript_is_recorded_as_failed(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_call(db, "call_silent")
    # The endpoint would otherwise fetch the transcript over the network.
    monkeypatch.setattr(
        "app.integrations.teleexpert_client.TeleExpertClient.get_transcript",
        lambda self, call_id: {"turns": []},
    )

    post_event(client, event_body("evt_silent", "call_silent"))

    event = db.get(TeleExpertWebhookEvent, "evt_silent")
    assert event.status == "failed"
    assert "did not include a transcript" in event.error


def test_the_drain_retries_a_failed_event_and_marks_it_processed(
    client: TestClient, db: Session
) -> None:
    """TeleExpert will not redeliver after the 2xx, so this drain is the only
    retry an event of this kind ever gets."""

    post_event(client, event_body("evt_drain", "call_late", status="failed", error="no answer"))
    assert db.get(TeleExpertWebhookEvent, "evt_drain").status == "failed"

    store_call(db, "call_late")  # the call arrives after its own webhook did
    recovered = drain_failed_events()

    assert recovered == ["evt_drain"]
    db.expire_all()
    event = db.get(TeleExpertWebhookEvent, "evt_drain")
    assert event.status == "processed"
    assert event.attempts == 2


def test_the_drain_gives_up_on_an_event_that_keeps_failing(client: TestClient, db: Session) -> None:
    post_event(client, event_body("evt_stuck", "call_never", status="failed"))

    for _ in range(5):
        drain_failed_events(max_attempts=3)

    db.expire_all()
    event = db.get(TeleExpertWebhookEvent, "evt_stuck")
    assert event.status == "failed"
    # Capped: a permanently broken event must not be retried on every poll.
    assert event.attempts == 3


def test_the_drain_leaves_a_processed_event_alone(client: TestClient, db: Session) -> None:
    store_call(db, "call_done")
    post_event(client, event_body("evt_done", "call_done", status="failed", error="no answer"))

    assert drain_failed_events() == []
    assert db.get(TeleExpertWebhookEvent, "evt_done").attempts == 1


# -- routing through to a batch ------------------------------------------------ #


def test_a_batch_call_completing_by_webhook_produces_answers(
    client: TestClient, db: Session, intelligence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole asynchronous half, for the path this pivot added: a signed webhook
    lands, the event is stored, and the batch — not the clause engine — reads the
    transcript."""

    monkeypatch.setattr(batch_service, "build_batch_intelligence", lambda: intelligence)
    batch, call_id = dialed_batch(db, intelligence)

    response = post_event(
        client,
        event_body("evt_batch", call_id, turns=turns_for("Abebe Kebede")),
    )

    assert response.status_code == 200
    assert db.get(TeleExpertWebhookEvent, "evt_batch").status == "processed"
    db.expire_all()
    record = WorkerAnswersRepository(db).for_batch(batch.batch_id)[0]
    assert record.answers["age_years"]["value"] == "32"
    assert record.excluded is False
    assert BatchTargetRepository(db).for_batch(batch.batch_id)[0].status == "completed"
    # One person, all targets terminal, so pass two ran and the batch is finished.
    assert batch_status(db, batch.batch_id) == batch_service.COMPLETE


def test_a_batch_call_failing_by_webhook_is_recorded_against_the_batch(
    client: TestClient, db: Session, intelligence, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batch_service, "build_batch_intelligence", lambda: intelligence)
    batch, call_id = dialed_batch(db, intelligence)

    post_event(client, event_body("evt_batch_fail", call_id, status="failed", error="no answer"))

    db.expire_all()
    assert BatchTargetRepository(db).for_batch(batch.batch_id)[0].status == "failed"
    assert WorkerAnswersRepository(db).for_batch(batch.batch_id) == []
    assert batch_status(db, batch.batch_id) == batch_service.COMPLETE
