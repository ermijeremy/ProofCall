"""TeleExpert HTTP client boundary.

This module owns transport concerns only. CallProof business decisions stay in
the application services and intelligence modules.
"""

from typing import Any

import httpx

from app.core.config import settings


class TeleExpertError(RuntimeError):
    """Raised when TeleExpert cannot accept or return a call."""


class TeleExpertClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.teleexpert_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.teleexpert_api_key
        self.http_client = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout or settings.teleexpert_timeout_seconds,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.base_url:
            raise TeleExpertError("TeleExpert base URL is not configured")
        try:
            url = f"{self.base_url}{path}" if path.startswith("/") else path
            response = self.http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TeleExpertError(f"TeleExpert request failed: {exc}") from exc

    def submit_call(
        self,
        phone_number: str,
        prompt: str,
        response_format: str = "both",
        retries: int = 2,
        retry_delay_seconds: int = 30,
    ) -> dict[str, Any]:
        payload = {
            "phone_number": phone_number,
            "prompt": prompt,
            "response_format": response_format,
            "retries": retries,
            "retry_delay_seconds": retry_delay_seconds,
        }
        result = self._request("POST", "/v1/calls", json=payload)
        if not result.get("call_id") and not result.get("id"):
            raise TeleExpertError("TeleExpert response did not include a call ID")
        return result

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/calls/{call_id}")

    def cancel_call(self, call_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/calls/{call_id}/cancel")
