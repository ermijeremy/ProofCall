"""Exercise real multipart parsing and all import entry points with bounded input."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import uploads
from app.api.routes import batches, beneficiaries, companies, employers
from app.core.import_limits import MAX_CSV_BYTES
from app.db.session import get_db
from app.models.employer import Employer

pytestmark = pytest.mark.integration


@pytest.fixture
def api(db: Session) -> FastAPI:
    db.add(Employer(company_id="co_upload", name="Upload tests"))
    db.commit()
    app = FastAPI()
    for router in (companies.router, beneficiaries.router, employers.router, batches.router):
        app.include_router(router, prefix="/api")

    def override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override
    return app


@pytest.mark.parametrize("path,content", [
    ("/api/companies/co_upload/upload", b"name,contact\nTest,+251900000001\n"),
    ("/api/beneficiaries/import-csv", b"worker_id,company_id,phone_number\nw1,co_upload,+251900000001\n"),
    ("/api/employers/import-csv", b"company_id,name\nco_other,Other\n"),
])
def test_ordinary_csv_imports_still_work(api, path, content):
    with TestClient(api) as client:
        response = client.post(path, files={"file": ("roster.csv", content, "text/csv")})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path", [
    "/api/companies/co_upload/upload",
    "/api/beneficiaries/import-csv",
    "/api/employers/import-csv",
    "/api/batches/not-a-real-batch/upload",
])
def test_file_budget_is_enforced_before_import(api, path):
    with TestClient(api) as client:
        response = client.post(path, files={"file": ("roster.csv", b"a" * (MAX_CSV_BYTES + 1))})
    assert response.status_code == 413


def test_declared_oversized_request_is_rejected_before_reading(api):
    with TestClient(api) as client:
        response = client.post(
            "/api/companies/co_upload/upload", content=b"",
            headers={"Content-Type": "multipart/form-data; boundary=test",
                     "Content-Length": str(uploads.MAX_UPLOAD_BODY_BYTES + 1)},
        )
    assert response.status_code == 413


@pytest.mark.anyio
@pytest.mark.parametrize("declared_length", [None, "1"])
async def test_streaming_budget_does_not_trust_content_length(api, monkeypatch, declared_length):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BODY_BYTES", 512)
    chunks_read = 0

    async def chunks():
        nonlocal chunks_read
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="roster.csv"\r\n\r\n'
        for _ in range(100):
            chunks_read += 1
            yield b"a" * 128
        yield b"\r\n--test--\r\n"

    headers = {"Content-Type": "multipart/form-data; boundary=test"}
    if declared_length is not None:
        headers["Content-Length"] = declared_length
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.post("/api/companies/co_upload/upload", content=chunks(), headers=headers)
    assert response.status_code == 413
    assert chunks_read < 100


@pytest.mark.parametrize("parts", [
    [("file", ("roster.csv", b"name,contact\n")), ("second", ("other.csv", b"x"))],
    [("file", ("roster.csv", b"name,contact\n")), ("extra", (None, "x" * 8192))],
])
def test_extra_files_and_text_fields_are_rejected(api, parts):
    with TestClient(api) as client:
        response = client.post("/api/companies/co_upload/upload", files=parts)
    assert response.status_code == 400


@pytest.mark.parametrize("extra_headers", [
    b"X-Large: " + b"x" * 5000 + b"\r\n",
    b"".join(f"X-{n}: value\r\n".encode() for n in range(9)),
    b"Invalid Header\r\n",
])
def test_bounded_malformed_or_excessive_part_headers_are_rejected(api, extra_headers):
    body = (b'--test\r\nContent-Disposition: form-data; name="file"; filename="roster.csv"\r\n'
            + extra_headers + b"\r\nname,contact\n\r\n--test--\r\n")
    with TestClient(api) as client:
        response = client.post("/api/companies/co_upload/upload", content=body,
                               headers={"Content-Type": "multipart/form-data; boundary=test"})
    assert response.status_code == 400


def test_missing_multipart_boundary_is_rejected(api):
    with TestClient(api) as client:
        response = client.post("/api/companies/co_upload/upload", content=b"invalid",
                               headers={"Content-Type": "multipart/form-data"})
    assert response.status_code == 400


@pytest.mark.parametrize("content_type", ["multipart/form-data; boundary=test", "application/x-www-form-urlencoded"])
def test_other_form_encodings_cannot_bypass_the_request_budget(api, content_type):
    with TestClient(api) as client:
        response = client.post("/api/companies/co_upload/upload", content=b"",
                               headers={"Content-Type": content_type,
                                        "Content-Length": str(uploads.MAX_UPLOAD_BODY_BYTES + 1)})
    assert response.status_code == 413
