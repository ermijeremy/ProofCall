"""Offline provider that replays recorded extractions.

Used by the test suite so that no test needs a network or an API key, and by
the demo for the preprocessed workers, where the same transcript must produce
the same verdict every time it is shown.

Recordings are keyed on the transcript text itself, so a recording can never be
served for a transcript it was not produced from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.intelligence.providers.base import ProviderError


def _transcript_key(transcript: str) -> str:
    """Normalize whitespace so trivial reformatting still matches."""

    return " ".join(transcript.split())


class RecordedProvider:
    """Serves a recorded extraction for each known transcript."""

    name = "recorded"

    def __init__(self, recordings: dict[str, dict[str, Any]] | None = None) -> None:
        self._by_transcript: dict[str, dict[str, Any]] = {}
        for transcript, payload in (recordings or {}).items():
            self.add(transcript, payload)

    def add(self, transcript: str, payload: dict[str, Any]) -> None:
        self._by_transcript[_transcript_key(transcript)] = payload

    @classmethod
    def from_fixture_dir(cls, directory: str | Path) -> RecordedProvider:
        """Load every ``*.json`` transcript fixture in ``directory``."""

        provider = cls()
        paths = sorted(Path(directory).glob("*.json"))
        if not paths:
            raise ProviderError(f"No transcript fixtures found in {directory}")
        for path in paths:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            provider.add(fixture["transcript"], fixture["expected_extraction"])
        return provider

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        """Match on the transcript embedded in the request payload.

        ``system`` is ignored: a recording is a recording regardless of the
        prompt that produced it. The transcript is what must match.
        """

        key = _transcript_key(user)
        for transcript_key, payload in self._by_transcript.items():
            if transcript_key and transcript_key in key:
                return dict(payload)
        raise ProviderError(
            "No recorded extraction matches this transcript. "
            f"{len(self._by_transcript)} recording(s) loaded."
        )
