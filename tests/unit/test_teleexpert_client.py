"""Transport behaviour at the TeleExpert boundary."""

import httpx
import pytest

from app.integrations.teleexpert_client import TeleExpertClient, TeleExpertError


def test_idempotent_call_submission_retries_a_disconnected_response():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        return httpx.Response(202, json={"id": "call-1", "status": "queued"})

    client = TeleExpertClient(
        base_url="https://teleexpert.test",
        api_key="key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client._RETRY_DELAYS_SECONDS = (0, 0)

    result = client.submit_call(
        phone_number="+251900000000",
        prompt="Ask one question.",
        idempotency_key="batch-1:worker-1",
    )

    assert result["id"] == "call-1"
    assert calls == 2


def test_provider_http_error_is_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"detail": "invalid request"})

    client = TeleExpertClient(
        base_url="https://teleexpert.test",
        api_key="key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(TeleExpertError, match="400"):
        client.submit_call(
            phone_number="+251900000000",
            prompt="Ask one question.",
            idempotency_key="batch-1:worker-1",
        )

    assert calls == 1
