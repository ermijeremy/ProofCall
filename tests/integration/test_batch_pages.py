"""The two rendered pages: the batch listing and one thread.

These are smoke tests with a purpose. The thread page hands the whole batch to
its own script through ``{{ batch|tojson }}``, so a field that is not JSON
serialisable — a raw ``datetime``, say — breaks the page and nothing else. And the
listing is now the landing page, so a template error there is the first thing
anybody sees. Both are cheap to catch here and awkward to catch in a browser.
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import batches as batches_route
from app.api.routes import dashboard as dashboard_route
from app.db.session import get_db
from app.services import batch_service

pytestmark = pytest.mark.integration

ROSTER_CSV = b"name,contact\nAbebe Kebede,+251900000001\nMarta Alemu,+251900000002\n"


@pytest.fixture()
def client(session_factory: sessionmaker) -> Iterator[TestClient]:
    api = FastAPI()
    api.include_router(dashboard_route.router, prefix="/api")
    api.include_router(batches_route.router, prefix="/api")

    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    api.dependency_overrides[get_db] = override
    with TestClient(api) as test_client:
        yield test_client


def test_the_listing_page_offers_a_plus_button_when_there_is_nothing_yet(client: TestClient) -> None:
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert "New batch" in response.text
    assert "No batches yet" in response.text


def test_the_listing_page_shows_a_batch_with_its_phase_and_counts(
    client: TestClient, db: Session, intelligence
) -> None:
    batch = batch_service.create_batch(db, title="August pay check")
    batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "roster.csv")
    batch_service.handle_message(db, batch.batch_id, "1 - what is ur age?", intelligence)

    response = client.get("/api/dashboard")

    assert "August pay check" in response.text
    assert f"/api/dashboard/batches/{batch.batch_id}" in response.text
    assert "phase-ready" in response.text
    assert "2 on the list" in response.text


def test_the_thread_page_renders_the_conversation_and_a_serialisable_batch(
    client: TestClient, db: Session
) -> None:
    batch = batch_service.create_batch(db)
    batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "roster.csv")

    response = client.get(f"/api/dashboard/batches/{batch.batch_id}")

    assert response.status_code == 200
    assert "Nobody is called until you confirm." in response.text
    assert f'const BATCH_ID = "{batch.batch_id}";' in response.text
    # The page bootstraps itself from this literal, so it has to be real JSON.
    payload = response.text.split("const INITIAL = ", 1)[1].split(";\n", 1)[0]
    parsed = json.loads(payload)
    assert parsed["batch_id"] == batch.batch_id
    assert [person["name"] for person in parsed["roster"]] == ["Abebe Kebede", "Marta Alemu"]


def test_an_unknown_batch_page_is_a_404(client: TestClient) -> None:
    response = client.get("/api/dashboard/batches/batch_nope")

    assert response.status_code == 404


def test_the_clause_path_report_is_still_reachable(client: TestClient) -> None:
    """The old dashboard is dormant, not deleted: it remains the fallback demo."""

    response = client.get("/api/dashboard/programme")

    assert response.status_code == 200
    assert "Programme health" in response.text
