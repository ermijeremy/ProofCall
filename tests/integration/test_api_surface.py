"""Runtime smoke tests for the dashboard and operational API surface."""

import pytest
import httpx

from app.main import app
from app.db.session import init_db



@pytest.mark.anyio
@pytest.mark.integration
async def test_health_and_dashboard_pages() -> None:
    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/api/dashboard")).status_code == 200
        assert (await client.get("/api/dashboard/calls")).status_code == 200


@pytest.mark.integration
def test_required_json_routes_are_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/employers" in paths
    assert "/api/beneficiaries/import-csv" in paths
    assert "/api/campaigns" in paths
    assert "/api/teleexpert/calls" in paths
    assert "/api/calls/process-completed" in paths
    assert "/api/dashboard/overview" in paths
