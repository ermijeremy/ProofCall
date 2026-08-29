"""Worker evidence schemas delegated through the Member A contract."""

from typing import Any

from pydantic import BaseModel, Field

from app.contracts.integration import CompletedCallResult, WorkerEvidenceResult


class TranscriptCompletion(BaseModel):
    transcript: str = Field(default="")
    audio_url: str | None = None
    transcript_turns: list[dict[str, Any]] = Field(default_factory=list)
    language: str | None = None

__all__ = ["CompletedCallResult", "WorkerEvidenceResult"]
