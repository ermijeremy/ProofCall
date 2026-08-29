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
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            if len(detail) > 500:
                detail = detail[:500] + "..."
            suffix = f": {detail}" if detail else ""
            raise TeleExpertError(
                f"TeleExpert request failed ({exc.response.status_code}){suffix}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TeleExpertError(f"TeleExpert request failed: {exc}") from exc

    def _request_bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        if not self.base_url:
            raise TeleExpertError("TeleExpert base URL is not configured")
        try:
            url = f"{self.base_url}{path}" if path.startswith("/") else path
            response = self.http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            if len(detail) > 500:
                detail = detail[:500] + "..."
            suffix = f": {detail}" if detail else ""
            raise TeleExpertError(
                f"TeleExpert request failed ({exc.response.status_code}){suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TeleExpertError(f"TeleExpert request failed: {exc}") from exc

    def absolute_url(self, path: str) -> str:
        return path if path.startswith("http://") or path.startswith("https://") else f"{self.base_url}{path}"

    def submit_call(
        self,
        phone_number: str,
        prompt: str,
        response_format: str = "both",
        retries: int = 2,
        retry_delay_seconds: int = 30,
        answer_timeout_seconds: int = 45,
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "phone_number": phone_number,
            "prompt": prompt,
            "response_format": response_format,
            "retries": retries,
            "retry_delay_seconds": retry_delay_seconds,
            "answer_timeout_seconds": answer_timeout_seconds,
        }
        if webhook_url:
            payload["webhook"] = {"url": webhook_url}
            if webhook_secret:
                payload["webhook"]["secret"] = webhook_secret
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        result = self._request("POST", "/v1/calls", json=payload, headers=headers)
        if not result.get("call_id") and not result.get("id"):
            raise TeleExpertError("TeleExpert response did not include a call ID")
        return result

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/calls/{call_id}")

    def get_transcript(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/calls/{call_id}/transcript")

    def get_audio(self, call_id: str) -> bytes:
        return self._request_bytes("GET", f"/v1/calls/{call_id}/audio")

    def cancel_call(self, call_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/calls/{call_id}")
