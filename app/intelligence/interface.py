"""Stable interface Member B uses for Member A's pipeline.

Member A implements the actual prompt generation, extraction, evaluation, and
comparison logic behind this boundary.
"""

from typing import Any, Protocol

from app.contracts.integration import ProgrammeContext, WorkerEvidenceResult


class IntelligenceEngine(Protocol):
    def generate_interview_prompt(self, worker: Any, programme: ProgrammeContext) -> str: ...

    def extract_evidence(self, transcript: str, worker_id: str) -> dict[str, Any]: ...

    def evaluate_clauses(self, evidence: dict[str, Any]) -> WorkerEvidenceResult: ...

    def compare_with_employer(self, worker_result: WorkerEvidenceResult, employer: Any) -> WorkerEvidenceResult: ...
