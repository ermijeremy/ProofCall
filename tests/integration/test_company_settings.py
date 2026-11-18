from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.companies import router
from app.db.session import get_db
from app.models.employer import Employer

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db: Session):
    db.add(Employer(company_id="co_settings", name="Settings", timezone="Africa/Addis_Ababa",
                    clock_convention="24h", small_cell_threshold=5, minimum_wage_etb=4500))
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("field,value", [
    ("timezone", None), ("clock_convention", None), ("small_cell_threshold", None),
    ("timezone", "not/a/timezone"), ("timezone", ""), ("timezone", "../etc/passwd"),
    ("clock_convention", "unknown"), ("small_cell_threshold", -1), ("small_cell_threshold", 1001),
])
def test_invalid_settings_are_rejected_without_changing_the_company(client, field, value):
    response = client.patch("/api/companies/co_settings/settings", json={field: value})
    assert response.status_code == 422
    # A subsequent request still works: there is no failed database transaction.
    current = client.patch("/api/companies/co_settings/settings", json={}).json()
    assert current["timezone"] == "Africa/Addis_Ababa"
    assert current["clock_convention"] == "24h"
    assert current["small_cell_threshold"] == 5
    assert current["minimum_wage_etb"] == 4500


def test_partial_update_can_clear_wage_and_set_valid_settings(client):
    response = client.patch("/api/companies/co_settings/settings", json={"minimum_wage_etb": None})
    assert response.status_code == 200
    assert response.json()["minimum_wage_etb"] is None
    assert response.json()["timezone"] == "Africa/Addis_Ababa"
    response = client.patch("/api/companies/co_settings/settings",
                            json={"timezone": "UTC", "clock_convention": "ethiopian", "small_cell_threshold": 0})
    assert response.status_code == 200
    assert response.json()["timezone"] == "UTC"
    assert response.json()["clock_convention"] == "ethiopian"
    assert response.json()["small_cell_threshold"] == 0
