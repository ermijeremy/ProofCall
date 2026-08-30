"""The provider seam for the batch path.

:mod:`app.intelligence.engine` is the same idea for the clause path: the modules
around it hold the prompts and the logic, and this class owns nothing except the
boundary with the model. Keeping the boundary in one place is what lets the batch
services be tested with a fake provider and no network.

Four model calls exist in the whole batch path, and they are all here:
routing (once per administrator message, and up to three times when one message
asks for two things), question refinement (once per typed question set),
extraction (once per completed interview), and categorization (once per round).
Nothing else in the batch path talks to a model. Questions about the results are
routing too: the counts are in the context block, so the router answers them with
its ``answer`` tool rather than a second call of its own.

Routing is the only one that needs :class:`ToolCallingProvider` rather than
:class:`LLMProvider`. That is checked when it is used rather than at
construction, because a provider that can only extract is still perfectly useful
for everything else, and refusing to build the seam over it would take the whole
results conversation down with the router.
"""

from __future__ import annotations

import logging
from typing import Any

from app.intelligence import agent, categorize, questions, safeguarding
from app.intelligence.providers.base import LLMProvider, ProviderError, ToolCallingProvider

logger = logging.getLogger(__name__)


class BatchIntelligence:
    """Interview prompts, answer extraction, categories, and routing."""

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

    # -- routing ------------------------------------------------------------ #

    def decide(
        self,
        context: dict[str, Any],
        history: list[dict[str, str]],
        prior: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Choose the one action the latest administrator message asked for.

        Raises :class:`ProviderError` rather than falling back to a canned reply.
        A transport failure that reads as "I could not tell who you meant" is the
        exact bug this rewrite exists to remove: the administrator would retype a
        perfectly clear instruction and get the same answer again.
        """

        if not isinstance(self.provider, ToolCallingProvider):
            raise ProviderError(
                f"The {self.name} provider cannot choose an action; it only extracts."
            )
        return agent.decide(self.provider, context, history, prior=prior or [])


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
