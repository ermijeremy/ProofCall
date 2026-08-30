"""Checks that call the real Gemini API.

Excluded from the default run by ``addopts = -m "not live"`` in ``pytest.ini``,
because these cost money and need a key. Run them deliberately:

    .venv/bin/python -m pytest -m live -v

They answer the question the offline suite cannot: does the *real* model, reading
these transcripts through the real prompt, still produce facts the deterministic
rules turn into the expected verdict? A mock can only confirm that our code is
self-consistent.

Every test skips rather than fails when no key is configured, so a contributor
without one still gets a green suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts.integration import ProgrammeContext
from app.intelligence.engine import EXPECTED_FACTS, CallProofEngine, build_default_engine
from app.intelligence.providers.gemini import (
    DEFAULT_MODEL,
    GeminiProvider,
    api_keys_from_environment,
    mask_key,
)

pytestmark = pytest.mark.live

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"


def _keys() -> list[str]:
    keys = api_keys_from_environment()
    if not keys:
        pytest.skip("no GEMINI_API_KEY configured")
    return keys


def _fixtures() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    ]


FIXTURES = _fixtures()
FIXTURE_IDS = [fixture["fixture"] for fixture in FIXTURES]


@pytest.fixture(scope="module")
def engine() -> CallProofEngine:
    _keys()
    return build_default_engine()


def test_every_configured_key_can_serve_the_default_model() -> None:
    """Catches a dead or model-gated key before a demo does.

    ``gemini-2.5-flash`` is gated to projects that already used it, and a newer
    key gets a 404 while the model still appears in ``models.list()`` — so only a
    real call proves a key works.
    """

    keys = _keys()
    failures: list[str] = []

    for key in keys:
        provider = GeminiProvider(api_keys=[key])
        try:
            provider.complete_json(
                system='Reply with JSON only.',
                user='Return exactly {"ok": true}',
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised, so all keys get checked
            failures.append(f"{mask_key(key)}: {str(exc)[:120]}")

    assert not failures, (
        f"{len(failures)} of {len(keys)} keys cannot serve {DEFAULT_MODEL}:\n"
        + "\n".join(failures)
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_the_live_model_produces_the_expected_verdict(
    fixture: dict[str, Any], engine: CallProofEngine
) -> None:
    """The end-to-end claim: real transcript, real model, expected verdict.

    Asserts the verdict rather than the whole extraction. Wording varies between
    runs in ways that do not matter; the verdict is what the dashboard shows and
    what an employer would be asked to accept.
    """

    evidence = engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
    assert evidence["extraction_error"] is False, "the model call itself failed"

    result = engine.evaluate_clauses(evidence)
    result = engine.compare_with_employer(result, fixture["employer_claims"])

    assert result.overall_verdict == fixture["expected_result"]["overall_verdict"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_the_live_model_never_invents_a_salary_for_a_refusal(
    fixture: dict[str, Any], engine: CallProofEngine
) -> None:
    """The one extraction error that would be a scandal, not a bug.

    If a respondent declined to state their pay, no number may appear — least of
    all the employer's figure, which the prompt never sees.
    """

    if fixture["fixture"] != "salary_refusal":
        pytest.skip("only meaningful for the refusal case")

    assert "salary_amount" in EXPECTED_FACTS, "fact renamed; update this test"

    evidence = engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
    salary = evidence["facts"]["salary_amount"]

    assert salary["state"] == "REFUSED"
    assert salary["value"] is None
    assert "8500" not in json.dumps(evidence)


def test_the_under_15_case_stops_the_interview(engine: CallProofEngine) -> None:
    """Safeguarding must not depend on the model's mood."""

    fixture = next(f for f in FIXTURES if f["fixture"] == "under_15_stopped")
    evidence = engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
    result = engine.evaluate_clauses(evidence)

    assert result.overall_verdict == "STOPPED"
    assert result.safeguarding_flag is True


def test_two_runs_of_one_transcript_agree(engine: CallProofEngine) -> None:
    """Temperature 0 is pinned so a finding cannot move between runs.

    A verdict that changes on re-run is not verification, whatever it says.
    """

    fixture = next(f for f in FIXTURES if f["fixture"] == "normal_worker")
    verdicts = set()
    for _ in range(2):
        evidence = engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
        verdicts.add(engine.evaluate_clauses(evidence).overall_verdict)

    assert len(verdicts) == 1, f"verdict moved between runs: {verdicts}"


def test_the_prompt_sent_to_the_live_model_leaks_no_threshold() -> None:
    """The safety invariant, checked on the exact string that goes over the wire.

    ``criteria`` is populated here the way Member B populates it, because that is
    the live leak vector: a threshold handed to us must not reach the interviewer.
    """

    import re

    engine = build_default_engine()
    prompt = engine.generate_interview_prompt(
        {"worker_id": "W001", "name": "Test", "phone_number": "+251900000000"},
        ProgrammeContext(
            programme_name="Youth Employment Programme",
            language="en",
            criteria={"minimum_age": 15, "minimum_duration_months": 6},
        ),
    )

    assert re.findall(r"(?<!\w)\d+(?!\w)", prompt) == []
    assert "8500" not in prompt
