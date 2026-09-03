"""The two rendered pages: the company listing and one company's round.

These are smoke tests with a purpose. The round page hands the whole thread to its
own script through ``{{ thread|tojson }}``, so a field that is not JSON
serialisable — a raw ``datetime``, say — breaks the page and nothing else. And the
listing is the landing page, so a template error there is the first thing anybody
sees. Both are cheap to catch here and awkward to catch in a browser.

The questionnaire assertions are a regression guard: the panel used to read its
questions off the round, which left it empty on a company opened for the first
time, because a round does not exist until a roster is uploaded.
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import dashboard as dashboard_route
from app.api.routes import pages as pages_route
from app.db.session import get_db
from app.intelligence.questions import FIXED_QUESTIONNAIRE
from app.services import batch_service

pytestmark = pytest.mark.integration

ROSTER_CSV = b"name,contact\nAbebe Kebede,+251900000001\nMarta Alemu,+251900000002\n"


@pytest.fixture()
def client(session_factory: sessionmaker) -> Iterator[TestClient]:
    api = FastAPI()
    api.include_router(dashboard_route.router, prefix="/api")
    # Mounted at the root in production, outside /api, because these are pages.
    api.include_router(pages_route.router)

    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    api.dependency_overrides[get_db] = override
    with TestClient(api) as test_client:
        yield test_client


def bootstrapped_state(page: str) -> dict:
    """The JSON literal the round page boots itself from."""

    return json.loads(page.split("let state=", 1)[1].split("; let busy", 1)[0])


def test_the_listing_page_offers_an_add_button_when_there_is_nothing_yet(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "No companies yet" in response.text
    assert "Add company" in response.text


def test_the_listing_page_shows_a_company_with_its_status_and_counts(
    client: TestClient, db: Session
) -> None:
    company = batch_service.create_company(db, name="August pay check")
    batch_service.handle_upload(db, company.company_id, ROSTER_CSV, "roster.csv")

    response = client.get("/")

    assert "August pay check" in response.text
    assert f"/c/{company.company_id}" in response.text
    assert "status-ready" in response.text
    assert "<b>2</b> people" in response.text


def test_the_round_page_renders_the_roster_and_a_serialisable_thread(
    client: TestClient, db: Session
) -> None:
    company = batch_service.create_company(db, name="Addis Retail")
    batch_service.handle_upload(db, company.company_id, ROSTER_CSV, "roster.csv")

    response = client.get(f"/c/{company.company_id}")

    assert response.status_code == 200
    assert "People to interview" in response.text
    # The page bootstraps itself from this literal, so it has to be real JSON.
    state = bootstrapped_state(response.text)
    assert state["company_id"] == company.company_id
    assert [person["name"] for person in state["people"]] == ["Abebe Kebede", "Marta Alemu"]


def test_the_questionnaire_is_on_screen_before_any_roster_is_uploaded(
    client: TestClient, db: Session
) -> None:
    """A company opened for the first time has no round, and must still show them."""

    company = batch_service.create_company(db, name="Brand new")

    response = client.get(f"/c/{company.company_id}")

    assert response.status_code == 200
    assert bootstrapped_state(response.text)["round"] is None
    for question in FIXED_QUESTIONNAIRE:
        assert question["text"] in response.text
    assert f"{len(FIXED_QUESTIONNAIRE)} questions" in response.text


def test_the_roster_offers_bulk_and_random_selection(client: TestClient, db: Session) -> None:
    company = batch_service.create_company(db, name="Bulk select")
    batch_service.handle_upload(db, company.company_id, ROSTER_CSV, "roster.csv")

    page = client.get(f"/c/{company.company_id}").text

    assert 'id="select-all"' in page
    assert 'id="clear-selection"' in page
    assert 'id="select-random"' in page
    assert 'id="random-size"' in page


def test_an_unknown_company_returns_to_the_listing(client: TestClient) -> None:
    """A browser can hold a link from another local checkout's database."""

    response = client.get("/c/co_nope", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_the_clause_path_report_is_still_reachable(client: TestClient) -> None:
    """The old dashboard is dormant, not deleted: it remains the fallback demo."""

    response = client.get("/api/dashboard/programme")

    assert response.status_code == 200
    assert "Programme health" in response.text


def test_the_questionnaire_survives_a_route_that_passes_no_context(client: TestClient) -> None:
    """The panel must not depend on a route remembering to pass the questions.

    Jinja renders a missing name as nothing rather than raising, so when the
    questions came from the route context a stale or forgetful route produced an
    empty questionnaire and a "0 questions" badge instead of an error. They are a
    template global now, which is what this pins.
    """

    rendered = pages_route.templates.env.get_template("round.html").render(
        request=None,
        thread={
            "company_id": "co_x",
            "name": "No context",
            "status": "draft",
            "people": [],
            "round": None,
        },
    )

    for question in FIXED_QUESTIONNAIRE:
        assert question["text"] in rendered
    assert f"{len(FIXED_QUESTIONNAIRE)} questions" in rendered


def test_the_legacy_admin_setup_page_is_gone(client: TestClient) -> None:
    """Superseded by "add a company, upload a CSV"; its JSON forms drove the
    campaign model the round flow no longer uses."""

    assert client.get("/api/dashboard/setup").status_code == 404
