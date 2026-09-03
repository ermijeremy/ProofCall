"""When the batch seam is allowed to reach a model, and when it must not.

Dialing a round is the case that matters here. It needs an interview prompt, and
building that prompt is templating -- the questionnaire is fixed and versioned, so
no model chooses anything. Constructing the provider eagerly made a missing API key
a 500 on the one confirmed action in the whole flow, which is exactly the action
that must not fail for a reason unrelated to placing calls.
"""

from __future__ import annotations

import pytest

from app.intelligence.batch_engine import BatchIntelligence, build_batch_intelligence
from app.intelligence.providers.base import ProviderError


class Person:
    """The few fields an interview prompt reads off a worker."""

    worker_id = "w_1"
    name = "Abebe Kebede"
    preferred_language = "am"


QUESTIONS = [{"id": "q1", "slug": "employment_status", "text": "Are you working at the moment?"}]


class Exploding:
    """Stands in for a provider that cannot be built without configuration."""

    name = "exploding"

    def __init__(self) -> None:
        raise ProviderError("No API key found.")


def test_a_factory_is_not_called_until_a_model_is_actually_needed() -> None:
    calls: list[int] = []

    def factory():
        calls.append(1)
        raise AssertionError("built the provider for an action that needs no model")

    BatchIntelligence(provider_factory=factory)

    assert calls == []


def test_an_interview_prompt_needs_no_provider_at_all() -> None:
    """The dialing path, with a provider that would raise if it were built."""

    engine = BatchIntelligence(provider_factory=Exploding)

    prompt = engine.interview_prompt(Person(), QUESTIONS)

    assert QUESTIONS[0]["text"] in prompt


def test_resolving_the_provider_still_surfaces_a_missing_key() -> None:
    """Deferring construction must not turn a real misconfiguration into silence.

    Extraction does call the model, so this is where a missing key belongs. The
    seam already reports a provider failure as an empty answer set flagged with
    ``extraction_error`` rather than a completed interview.
    """

    engine = BatchIntelligence(provider_factory=Exploding)

    with pytest.raises(ProviderError):
        engine.provider  # noqa: B018 -- resolving the provider is the assertion


def test_the_seam_refuses_to_be_built_with_neither_provider_nor_factory() -> None:
    with pytest.raises(ValueError):
        BatchIntelligence()


def test_the_default_build_defers_construction(monkeypatch) -> None:
    """`build_batch_intelligence()` must not need a key merely to be called."""

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    engine = build_batch_intelligence()

    assert isinstance(engine, BatchIntelligence)


def test_an_explicit_provider_is_used_as_given(provider) -> None:
    engine = build_batch_intelligence(provider)

    assert engine.provider is provider
