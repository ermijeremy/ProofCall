"""Public page shells cannot bootstrap private company data or fixed questions."""
from typing import Iterator
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from app.api.routes import dashboard as dashboard_route
from app.api.routes import pages as pages_route
from app.db.session import get_db
from app.services import batch_service

pytestmark = pytest.mark.integration

@pytest.fixture()
def client(session_factory: sessionmaker) -> Iterator[TestClient]:
    api = FastAPI()
    api.include_router(dashboard_route.router, prefix='/api')
    api.include_router(pages_route.router)
    def override():
        with session_factory() as session:
            yield session
    api.dependency_overrides[get_db] = override
    with TestClient(api) as client:
        yield client


def test_home_offers_administrator_access_and_company_sign_in(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'Administrator token' in response.text
    assert 'Add a company' in response.text
    assert 'href="/login"' in response.text


def test_home_never_embeds_company_names_or_rosters(client, db):
    batch_service.create_company(db, name='Private company')
    page = client.get('/').text
    assert 'Private company' not in page
    assert 'let state=' not in page


def test_legacy_company_link_redirects_to_scoped_workspace(client, db):
    company = batch_service.create_company(db, name='Private company')
    response = client.get('/c/' + company.company_id, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/companies/' + company.company_id


def test_company_shell_offers_editable_questions_without_private_bootstrap(client, db):
    company = batch_service.create_company(db, name='Private company')
    page = client.get('/companies/' + company.company_id).text
    assert 'Your questions (one per line)' in page
    assert 'id="campaign-questions"' in page
    assert 'Private company' not in page
    assert 'let state=' not in page
    assert 'fixed KPI' not in page


def test_company_shell_keeps_excel_import_and_single_call_review(client):
    page = client.get('/companies/example').text
    assert 'Preview spreadsheet' in page
    assert 'Review and place one call' in page
    assert 'Company questionnaire' in page


def test_unknown_company_shell_does_not_disclose_whether_company_exists(client):
    assert client.get('/companies/unknown').status_code == 200


def test_legacy_report_template_remains_available_to_admin_router(client):
    # Production app middleware requires administrator authentication for this API.
    response = client.get('/api/dashboard/programme')
    assert response.status_code == 200
    assert 'Programme health' in response.text


def test_login_page_has_no_company_or_credential_bootstrap(client):
    page = client.get('/login').text
    assert 'Current password' in page and 'Confirm new password' in page
    assert 'autocomplete="new-password"' in page
    assert 'temporary_password' not in page


def test_legacy_setup_page_is_not_exposed(client):
    assert client.get('/api/dashboard/setup').status_code == 404
