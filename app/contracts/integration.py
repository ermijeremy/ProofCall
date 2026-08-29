"""Member A/B integration contract from the project specification."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["CONFIRMED_GOOD_JOB", "NOT_CONFIRMED", "UNCLEAR"]
ClauseStatus = Literal["MET", "NOT_MET", "UNCLEAR", "REFUSED", "NOT_ASKED", "STOPPED"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class ClauseEvidence(BaseModel):
    value: Any = None
    status: ClauseStatus = "UNCLEAR"
    confidence: Confidence = "LOW"
    evidence: str | None = None


class WorkerEvidenceResult(BaseModel):
    worker_id: str
    consent: bool = False
    clauses: dict[str, ClauseEvidence] = Field(default_factory=dict)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    overall_verdict: Verdict = "UNCLEAR"


class CompletedCallResult(BaseModel):
    """Frozen handoff object between Member A and Member B."""

    worker_id: str
    call_id: str
    transcript: str = ""
    consent: bool = False
    clauses: dict[str, ClauseEvidence] = Field(default_factory=dict)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    overall_verdict: Verdict = "UNCLEAR"
    audio_url: str | None = None
