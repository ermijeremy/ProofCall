"""The provider seam for the batch path.

:mod:`app.intelligence.engine` is the same idea for the clause path: the modules
around it hold the prompts and the logic, and this class owns nothing except the
boundary with the model. Keeping the boundary in one place is what lets the batch
services be tested with a fake provider and no network.

Five model calls exist in the whole batch path, and they are all here:
question refinement (once per typed question set), extraction (once per completed
interview), categorization (once per batch), selection parsing (once per
instruction), and analysis (once per admin question). Nothing else in the batch
path talks to a model.
"""

from __future__ import annotations

import logging
from typing import Any

from app.intelligence import analysis, categorize, questions, safeguarding, selection
from app.intelligence.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)


class BatchIntelligence:
    """Interview prompts, answer extraction, categories, selection, and chat."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @property
    def name(self) -> str:
        return getattr(self.provider, "name", "unknown")

    # -- questions ---------------------------------------------------------- #

    def refine_questions(self, raw: list[str]) -> list[dict[str, str]]:
        """Rewrite the admin's typed questions into ones fit to be read aloud."""

        return questions.refine_questions(self.provider, raw)

    # -- interview ---------------------------------------------------------- #

    def interview_prompt(
        self,
        worker: Any,
        question_set: list[dict[str, Any]],
        *,
        language: str = "am",
        programme_name: str = "an internal workforce review",
    ) -> str:
        return questions.build_interview_prompt(
            worker, question_set, language=language, programme_name=programme_name
        )

    # -- extraction --------------------------------------------------------- #

    def extract_answers(
        self,
        transcript: str,
        worker_id: str,
        question_set: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Read one transcript into one answer per question, then safeguard it.

        A provider failure is not allowed to look like a completed interview: it
        produces an empty answer set with ``extraction_error`` true, which counts
        as nothing and is visible in the thread.
        """

        text = transcript or ""
        try:
            payload = self.provider.complete_json(
                system=questions.build_extraction_prompt(question_set),
                user=questions.build_extraction_request(text),
            )
        except ProviderError:
            logger.exception("Answer extraction failed for %s", worker_id)
            payload = {"consent": False, "answers": {}, "extraction_error": True}

        extraction = questions.normalize_extraction(
            payload, worker_id=worker_id, transcript=text, questions=question_set
        )
        return safeguarding.apply(extraction)

    # -- pass two ----------------------------------------------------------- #

    def categorize(
        self,
        question_set: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return categorize.categorize_batch(self.provider, question_set, records)

    # -- selection ---------------------------------------------------------- #

    def parse_selection(self, instruction: str) -> dict[str, Any]:
        return selection.parse_selection(self.provider, instruction)

    # -- chat --------------------------------------------------------------- #

    def answer(
        self,
        admin_question: str,
        summary: dict[str, Any],
        history: list[dict[str, str]] | None = None,
    ) -> str:
        return analysis.answer(self.provider, admin_question, summary, history)


def build_batch_intelligence(provider: LLMProvider | None = None) -> BatchIntelligence:
    """Build the batch seam, defaulting to Gemini.

    Constructed lazily so importing this module never requires the Gemini SDK or
    an API key.
    """

    if provider is None:
        from app.intelligence.providers.gemini import GeminiProvider

        provider = GeminiProvider()
    return BatchIntelligence(provider)


__all__ = ["BatchIntelligence", "build_batch_intelligence"]
