"""Pass two: derived category sets, and what code refuses to take from a model.

Categorization is the only place in the batch path where a label decides how a
person is counted, so the guarantees are narrow on purpose: a label outside the
declared set never reaches a count, a refusal is bucketed by code rather than
guessed by a model, and a provider failure loses no answers.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence import categorize
from app.intelligence.providers.base import ProviderError

QUESTIONS = [
    {"index": 0, "text": "How old are you?", "slug": "age_years"},
    {"index": 1, "text": "Do you get enough compensation?", "slug": "compensation"},
]


def record(worker_id: str, age: Any, compensation: Any) -> dict[str, Any]:
    return {"worker_id": worker_id, "name": worker_id.title(), "answers": {"age_years": age, "compensation": compensation}}


def stated(value: str) -> dict[str, Any]:
    return {"value": value, "state": "STATED", "confidence": "HIGH", "evidence": value, "category": None}


def refused() -> dict[str, Any]:
    return {"value": None, "state": "REFUSED", "confidence": "HIGH", "evidence": "I would rather not say", "category": None}


def not_asked() -> dict[str, Any]:
    return {"value": None, "state": "NOT_ASKED", "confidence": "LOW", "evidence": None, "category": None}


class StubProvider:
    """Returns a fixed payload and remembers what it was asked."""

    name = "stub"

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


RECORDS = [
    record("w1", stated("32"), stated("no, it is not enough")),
    record("w2", stated("19"), stated("yes it is fine")),
    record("w3", stated("41"), refused()),
]

PAYLOAD = {
    "age_years": {
        "categories": ["Under 25", "25 to 40", "Over 40"],
        "assignments": {"w1": "25 to 40", "w2": "Under 25", "w3": "Over 40"},
    },
    "compensation": {
        "categories": ["Enough", "Not enough"],
        "assignments": {"w1": "Not enough", "w2": "Enough"},
    },
}


def test_one_provider_call_for_the_whole_batch():
    provider = StubProvider(PAYLOAD)
    categorize.categorize_batch(provider, QUESTIONS, RECORDS)
    assert len(provider.calls) == 1


def test_every_person_gets_a_category_for_every_question():
    result = categorize.categorize_batch(StubProvider(PAYLOAD), QUESTIONS, RECORDS)
    categorize.apply_categories(RECORDS, result)
    for item in RECORDS:
        for question in QUESTIONS:
            assert item["answers"][question["slug"]]["category"]


def test_a_refusal_is_bucketed_by_code_and_never_sent_to_the_model():
    provider = StubProvider(PAYLOAD)
    result = categorize.categorize_batch(provider, QUESTIONS, RECORDS)
    assert result["compensation"]["assignments"]["w3"] == categorize.REFUSED_CATEGORY
    assert categorize.REFUSED_CATEGORY in result["compensation"]["categories"]
    # w3's refusal is not in the payload the model was asked to categorize.
    request = provider.calls[0]["user"]
    assert "would rather not say" not in request


def test_an_unasked_question_gets_its_own_bucket():
    records = [record("w1", stated("32"), not_asked())]
    result = categorize.categorize_batch(
        StubProvider({"age_years": {"categories": ["25 to 40"], "assignments": {"w1": "25 to 40"}}}),
        QUESTIONS,
        records,
    )
    assert result["compensation"]["assignments"]["w1"] == categorize.NOT_ASKED_CATEGORY


def test_a_label_outside_the_declared_set_becomes_uncategorized():
    payload = {
        "compensation": {
            "categories": ["Enough", "Not enough"],
            "assignments": {"w1": "At risk", "w2": "Enough"},
        }
    }
    result = categorize.categorize_batch(StubProvider(payload), QUESTIONS, RECORDS)
    assert result["compensation"]["assignments"]["w1"] == categorize.UNCATEGORIZED
    assert "At risk" not in result["compensation"]["categories"]


def test_more_categories_than_allowed_are_cut_back():
    many = [f"Band {index}" for index in range(12)]
    payload = {"age_years": {"categories": many, "assignments": {"w1": "Band 0"}}}
    result = categorize.categorize_batch(StubProvider(payload), QUESTIONS, RECORDS)
    derived = [name for name in result["age_years"]["categories"] if name not in categorize.RESERVED_CATEGORIES]
    assert len(derived) <= categorize.MAX_CATEGORIES


def test_an_assignment_for_a_worker_who_was_not_interviewed_is_dropped():
    payload = {"age_years": {"categories": ["25 to 40"], "assignments": {"w1": "25 to 40", "ghost": "25 to 40"}}}
    result = categorize.categorize_batch(StubProvider(payload), QUESTIONS, RECORDS)
    assert "ghost" not in result["age_years"]["assignments"]


def test_a_provider_failure_still_buckets_the_answers_code_can_bucket():
    result = categorize.categorize_batch(
        StubProvider(ProviderError("no key")), QUESTIONS, RECORDS
    )
    assert result["compensation"]["assignments"]["w3"] == categorize.REFUSED_CATEGORY
    assert result["age_years"]["assignments"]["w1"] == categorize.UNCATEGORIZED


def test_apply_categories_writes_onto_the_stored_answers():
    records = [record("w1", stated("32"), stated("no"))]
    categorize.apply_categories(
        records,
        {"age_years": {"categories": ["25 to 40"], "assignments": {"w1": "25 to 40"}}},
    )
    assert records[0]["answers"]["age_years"]["category"] == "25 to 40"


def test_the_prompt_forbids_verdict_shaped_category_names():
    prompt = categorize.build_categorization_prompt(QUESTIONS)
    assert "implies a verdict" in prompt
    assert "do not order them from best to worst" in prompt


@pytest.mark.parametrize("state", ["REFUSED", "NOT_ASKED"])
def test_the_request_carries_only_categorizable_answers(state: str):
    entry = {"value": None, "state": state, "confidence": "HIGH", "evidence": "x", "category": None}
    request = categorize.build_categorization_request(QUESTIONS, [record("w1", stated("32"), entry)])
    assert "compensation" not in request
    assert "age_years" in request
