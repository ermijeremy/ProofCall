"""Member A/B integration contract from the project specification."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["CONFIRMED_GOOD_JOB", "NOT_CONFIRMED", "UNCLEAR", "STOPPED"]
ClauseStatus = Literal["MET", "NOT_MET", "UNCLEAR", "REFUSED", "NOT_ASKED", "STOPPED"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class ClauseEvidence(BaseModel):
    value: Any = None
    status: ClauseStatus = "UNCLEAR"
    confidence: Confidence = "LOW"
    evidence: str | None = None


class Contradiction(BaseModel):
    type: str
    description: str
    material: bool = False
    employer_value: Any = None
    worker_value: Any = None
    evidence: str | None = None


class ProgrammeContext(BaseModel):
    programme_name: str
    language: str = "am"
    criteria: dict[str, Any] = Field(default_factory=dict)


class WorkerEvidenceResult(BaseModel):
    worker_id: str
    consent: bool = False
    clauses: dict[str, ClauseEvidence] = Field(default_factory=dict)
    contradictions: list[Contradiction] = Field(default_factory=list)
    safeguarding_flag: bool = False
    overall_verdict: Verdict = "UNCLEAR"


class CompletedCallResult(BaseModel):
    """Frozen handoff object between Member A and Member B."""

    worker_id: str
    call_id: str
    transcript: str = ""
    transcript_turns: list[dict[str, Any]] = Field(default_factory=list)
    language: str | None = None
    consent: bool = False
    clauses: dict[str, ClauseEvidence] = Field(default_factory=dict)
    contradictions: list[Contradiction] = Field(default_factory=list)
    safeguarding_flag: bool = False
    overall_verdict: Verdict = "UNCLEAR"
    audio_url: str | None = None
