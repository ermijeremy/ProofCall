"""Member A's intelligence engine.

Implements :class:`app.intelligence.interface.IntelligenceEngine`. The methods
are synchronous by Member B's decision: B's routes and services are sync and
FastAPI runs them in a worker thread.

The engine is a thin seam. It owns no qualification logic at all — that lives in
:mod:`app.intelligence.rules` — and no prompt text, which lives in
:mod:`app.intelligence.prompts`. What it does own is the boundary with the
provider: turning a transcript into a validated fact payload, and refusing to
let a malformed model response reach the rule engine as if it were evidence.
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.integration import ProgrammeContext, WorkerEvidenceResult
from app.intelligence import prompts, rules
from app.intelligence.criteria import DECENT_WORK_CRITERIA
from app.intelligence.providers.base import LLMProvider, ProviderError
from app.intelligence.registry import configure_engine

logger = logging.getLogger(__name__)

#: Facts the extraction must report. Anything missing is filled in as unknown by
#: the rule engine, which can never turn unknown into a confirmation.
EXPECTED_FACTS = tuple(name for name, _ in prompts.FACT_SCHEMA)


class CallProofEngine:
    """Prompt generation, extraction, evaluation, and employer comparison."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        criteria: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.criteria = dict(DECENT_WORK_CRITERIA if criteria is None else criteria)

    # -- interview ---------------------------------------------------------- #

    def generate_interview_prompt(self, worker: Any, programme: ProgrammeContext) -> str:
        return prompts.build_interview_prompt(worker, programme)

    # -- extraction --------------------------------------------------------- #

    def extract_evidence(self, transcript: str, worker_id: str) -> dict[str, Any]:
        """Ask the provider for facts, then normalize its answer.

        The returned payload always carries ``worker_id`` and ``transcript`` from
        the caller rather than from the model. The model is not a source of truth
        about who was interviewed, and keeping the transcript alongside the facts
        lets :func:`rules.evaluate_clauses` verify that every quote is real.

        A provider failure is not allowed to look like a completed interview:
        it produces an empty fact set, which evaluates to UNCLEAR.
        """

        text = transcript or ""
        try:
            payload = self.provider.complete_json(
                system=prompts.build_extraction_prompt(),
                user=prompts.build_extraction_request(text),
            )
        except ProviderError:
            payload = {"consent": False, "facts": {}, "extraction_error": True}

        return self._normalize(payload, worker_id=worker_id, transcript=text)

    def _normalize(
        self, payload: dict[str, Any], *, worker_id: str, transcript: str
    ) -> dict[str, Any]:
        raw_facts = payload.get("facts")
        facts: dict[str, Any] = {}
        for name in EXPECTED_FACTS:
            entry = raw_facts.get(name) if isinstance(raw_facts, dict) else None
            facts[name] = entry if isinstance(entry, dict) else {
                "value": None,
                "state": "NOT_ASKED" if payload.get("interview_stopped") else "VAGUE",
                "confidence": "LOW",
                "evidence": None,
            }

        language = payload.get("language")
        stop_reason = payload.get("stop_reason")
        return {
            "worker_id": worker_id,
            "consent": bool(payload.get("consent", False)),
            "language": language if isinstance(language, str) and language else None,
            "interview_stopped": bool(payload.get("interview_stopped", False)),
            "stop_reason": stop_reason if isinstance(stop_reason, str) and stop_reason else None,
            "facts": facts,
            "transcript": transcript,
            "provider": getattr(self.provider, "name", "unknown"),
            "extraction_error": bool(payload.get("extraction_error", False)),
        }

    # -- evaluation --------------------------------------------------------- #

    def evaluate_clauses(self, evidence: dict[str, Any]) -> WorkerEvidenceResult:
        return rules.evaluate_clauses(evidence, self.criteria)

    def compare_with_employer(
        self, worker_result: WorkerEvidenceResult, employer: Any
    ) -> WorkerEvidenceResult:
        return rules.compare_with_employer(worker_result, employer, self.criteria)


def build_default_engine(
    provider: LLMProvider | None = None,
    *,
    criteria: dict[str, Any] | None = None,
) -> CallProofEngine:
    """Build the engine, defaulting to Gemini.

    Constructed lazily so that importing this module never requires the Gemini
    SDK or an API key. Pass ``provider`` to use a different model, or
    :class:`~app.intelligence.providers.fixture.RecordedProvider` to run offline.
    """

    if provider is None:
        from app.intelligence.providers.gemini import GeminiProvider

        provider = GeminiProvider()
    return CallProofEngine(provider, criteria=criteria)


def register_default_engine(
    provider: LLMProvider | None = None,
    *,
    criteria: dict[str, Any] | None = None,
) -> CallProofEngine:
    """Build the engine and register it for Member B's services to pick up."""

    engine = build_default_engine(provider, criteria=criteria)
    configure_engine(engine)
    return engine


def try_register_default_engine(
    provider: LLMProvider | None = None,
    *,
    criteria: dict[str, Any] | None = None,
) -> CallProofEngine | None:
    """Register the engine if it can be built, and report rather than raise.

    Safe to call unconditionally from application startup. ``register_default_engine``
    is not: with no API key present it raises :class:`ProviderError`, which would
    stop the process from starting for anyone who has not set a key.

    Returns the engine, or ``None`` when no provider could be constructed. In the
    ``None`` case nothing is registered, so ``get_engine()`` keeps raising and the
    completion endpoint keeps answering 503 with an explanatory message — a
    missing key must never be papered over with fixture data, which would report
    invented interviews as real ones.
    """

    try:
        return register_default_engine(provider, criteria=criteria)
    except ProviderError as exc:
        logger.warning(
            "Intelligence engine not registered; call processing will return 503. %s", exc
        )
        return None
