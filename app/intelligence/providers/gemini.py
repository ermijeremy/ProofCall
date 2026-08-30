"""Gemini provider: the project default for transcript extraction.

Imports of ``google.genai`` are deferred to construction time so that the rest
of the intelligence package, and the whole test suite, run without the SDK
installed and without an API key present.

Several API keys may be supplied at once. The provider spreads calls across them
and fails over on quota, so a demo running twenty interviews does not stall on
one key's per-minute limit. See :class:`GeminiProvider` for the rotation rules.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Sequence

from app.intelligence.providers.base import ProviderError

logger = logging.getLogger(__name__)

#: Overridable so a newer model can be selected without a code change.
#:
#: ``gemini-2.5-flash`` is deliberately not the default: Google now gates it to
#: projects that already used it, and newer keys get
#: ``404 ... no longer available to new users``. All twelve of the project's keys
#: serve ``gemini-3.6-flash``.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

#: Checked in order. GEMINI_API_KEY is the SDK's own preferred name.
API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

#: Substrings marking a failure that another key might not have. Quota is the
#: obvious one; 404 is here because model availability is granted per project,
#: so one key can be refused a model that the next key serves.
ROTATABLE_ERRORS = (
    "429",
    "RESOURCE_EXHAUSTED",
    "quota",
    "rate limit",
    "503",
    "UNAVAILABLE",
    "overloaded",
    "500",
    "INTERNAL",
    "404",
    "NOT_FOUND",
    "no longer available",
)

#: Substrings marking a key that will never work. These are parked after one
#: failure instead of being retried on every later call.
#: Transport failures, which say nothing at all about the key. These are retried
#: on a fresh key — a new key means a new client and a new connection — but only
#: ``MAX_TRANSPORT_RETRIES`` times, because a network that is down stays down and
#: walking twelve keys through it would hang the request instead of failing it.
#:
#: Before this existed a single reset socket ended the whole call: the error
#: matched neither list, ``complete_json`` re-raised on the first key, and one
#: flaky connection read to the admin as "I could not tell who you meant".
TRANSPORT_ERRORS = (
    "Connection reset by peer",
    "ReadError",
    "ReadTimeout",
    "ConnectError",
    "ConnectTimeout",
    "RemoteProtocolError",
    "Errno 104",
    "Errno 32",
    "timed out",
    "Temporary failure in name resolution",
)

#: How many transport failures to ride out before giving up on the request.
MAX_TRANSPORT_RETRIES = 3


DEAD_KEY_ERRORS = (
    "401",
    "403",
    "UNAUTHENTICATED",
    "PERMISSION_DENIED",
    "API_KEY_INVALID",
    "API key not valid",
    "expired",
)


def mask_key(key: str) -> str:
    """Render a key safe to log. Never let a full key reach a log or an error."""

    if len(key) <= 14:
        return "***"
    return f"{key[:10]}...{key[-4:]}"


def parse_api_keys(raw: str | None) -> list[str]:
    """Read one key or many from a single environment value.

    Accepts a JSON array (what this project uses), a comma- or
    whitespace-separated list, or a single bare key. Order is preserved and
    duplicates are dropped, so a key repeated in the list does not get double
    the share of traffic.
    """

    if not raw or not raw.strip():
        return []

    candidates: list[str]
    stripped = raw.strip()
    if stripped[0] in "[{":
        # Anything opening a JSON container was meant as JSON. Splitting it on
        # whitespace instead would turn '{"key": "a"}' into two garbage keys and
        # every call would 401 with no hint as to why.
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"GEMINI_API_KEY looks like JSON but does not parse: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ProviderError(
                f"GEMINI_API_KEY JSON must be an array of strings, got {type(parsed).__name__}"
            )
        candidates = [str(item).strip() for item in parsed]
    elif "," in stripped:
        candidates = [part.strip() for part in stripped.split(",")]
    else:
        candidates = stripped.split()

    seen: set[str] = set()
    keys: list[str] = []
    for key in candidates:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def load_env_file(path: str | os.PathLike[str] = ".env") -> dict[str, str]:
    """Read ``KEY=value`` pairs from a dotenv file, without a dependency.

    Nothing else in the application loads ``.env``: ``app/core/config.py``
    defines plain defaults and never consults the environment. Without this,
    running under uvicorn would find no keys even though ``.env`` holds twelve.

    Values already present in the real environment always win, so this can only
    fill gaps, never override an explicit export.
    """

    found: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return found

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        value = value.strip()
        # Strip one matched pair of surrounding quotes; a JSON array of keys is
        # normally pasted quoted, and the quotes are not part of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name.strip()] = value
    return found


def api_keys_from_environment() -> list[str]:
    """Every key available, from the first source that has any.

    The real environment is consulted first, then ``.env``.
    """

    for variable in API_KEY_ENV_VARS:
        keys = parse_api_keys(os.environ.get(variable))
        if keys:
            return keys

    from_file = load_env_file()
    for variable in API_KEY_ENV_VARS:
        keys = parse_api_keys(from_file.get(variable))
        if keys:
            return keys
    return []


def api_key_from_environment() -> str | None:
    """The first available key, or ``None``."""

    keys = api_keys_from_environment()
    return keys[0] if keys else None


class GeminiProvider:
    """Extraction through the unified ``google-genai`` SDK.

    Temperature is pinned to 0 because two runs over the same transcript must
    produce the same facts; an audit finding that moves between runs is not an
    audit finding. Rotating keys does not weaken that: the key that serves a
    call has no effect on the text the model returns.

    Given several keys, each call starts at the next key in the list and, on a
    rotatable failure, walks the remaining keys before giving up. Starting at a
    different key each time spreads per-minute quota; walking the rest means a
    single exhausted key costs one retry, not a failed interview. A key that
    fails authentication is parked and not tried again.
    """

    name = "gemini"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        api_keys: Sequence[str] | None = None,
        client: Any = None,
        clients: Sequence[Any] | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature
        self._lock = threading.Lock()
        self._cursor = 0
        self._dead: set[str] = set()
        #: Strong references, deliberately. An unreferenced ``genai.Client`` can be
        #: garbage-collected while its request is in flight, and the call then
        #: fails with "Cannot send a request, as the client has been closed."
        self._clients: dict[str, Any] = {}

        explicit = list(clients) if clients is not None else ([client] if client is not None else [])
        if explicit:
            self._keys = [f"__client_{index}__" for index in range(len(explicit))]
            self._clients = dict(zip(self._keys, explicit))
            return

        keys = list(api_keys) if api_keys is not None else ([api_key] if api_key else api_keys_from_environment())
        keys = [key for key in keys if key]
        if not keys:
            raise ProviderError(
                "No Gemini API key found. Set GEMINI_API_KEY (or GOOGLE_API_KEY), "
                "or pass api_key/api_keys/client explicitly."
            )
        self._keys = keys

    @property
    def key_count(self) -> int:
        """How many keys are still in play."""

        return len([key for key in self._keys if key not in self._dead])

    def _client_for(self, key: str) -> Any:
        """The client for one key, built once and kept."""

        existing = self._clients.get(key)
        if existing is not None:
            return existing

        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError(
                "google-genai is not installed. Install it with "
                "'pip install google-genai' or use a different provider."
            ) from exc

        created = genai.Client(api_key=key)
        self._clients[key] = created
        return created

    def _attempt_order(self) -> list[str]:
        """Live keys, starting one past the last call's starting point."""

        with self._lock:
            live = [key for key in self._keys if key not in self._dead]
            if not live:
                return []
            start = self._cursor % len(live)
            self._cursor = (start + 1) % len(live)
        return live[start:] + live[:start]

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        order = self._attempt_order()
        if not order:
            raise ProviderError(
                f"Every Gemini key failed authentication ({len(self._dead)} parked); none left to try."
            )

        failures: list[str] = []
        transport_failures = 0
        for key in order:
            try:
                return self._call_once(key, system=system, user=user)
            except ProviderError as exc:
                message = str(exc)
                failures.append(f"{mask_key(key)}: {message}")
                if any(marker in message for marker in DEAD_KEY_ERRORS):
                    with self._lock:
                        self._dead.add(key)
                    continue
                if any(marker in message for marker in TRANSPORT_ERRORS):
                    transport_failures += 1
                    if transport_failures >= MAX_TRANSPORT_RETRIES:
                        raise ProviderError(
                            f"Gemini unreachable after {transport_failures} transport failure(s): {message}"
                        ) from exc
                    logger.warning(
                        "Gemini transport failure %s/%s on %s, retrying on another key",
                        transport_failures,
                        MAX_TRANSPORT_RETRIES,
                        mask_key(key),
                    )
                    continue
                if any(marker in message for marker in ROTATABLE_ERRORS):
                    continue
                raise

        raise ProviderError(
            f"All {len(order)} Gemini key(s) failed. " + " | ".join(failures[-3:])
        )

    def _call_once(self, key: str, *, system: str, user: str) -> dict[str, Any]:
        """One request on one key. Every failure leaves as :class:`ProviderError`."""

        try:
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError("google-genai is not installed") from exc

        client = self._client_for(key)
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=self.temperature,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - any SDK failure is a provider failure
            raise ProviderError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise ProviderError("Gemini returned an empty response")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gemini returned text that is not JSON: {text[:200]!r}") from exc

        if not isinstance(payload, dict):
            raise ProviderError(f"Gemini returned {type(payload).__name__}, expected an object")
        return payload
