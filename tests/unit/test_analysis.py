"""The chat over a finished batch. Code counts; the model narrates.

The rule these tests exist to protect: every figure the admin reads is computed
in Python and handed to the model already calculated. So the assertions are about
what ``summarize`` counts, what the request contains, and — most importantly —
what an excluded record contributes to a count, which is nothing.
"""

from __future__ import annotations

import json
from typing import Any

from app.intelligence import analysis
from app.intelligence.providers.base import ProviderError

QUESTIONS = [
    {"index": 0, "text": "How old are you?", "slug": "age_years"},
    {"index": 1, "text": "Do you get enough compensation?", "slug": "compensation"},
]

CATEGORIES = {
    "age_years": {"categories": ["Under 25", "25 to 40"], "assignments": {"w1": "25 to 40", "w2": "Under 25"}},
    "compensation": {"categories": ["Enough", "Not enough"], "assignments": {"w1": "Not enough", "w2": "Not enough"}},
}


def answer(value: Any, state: str = "STATED", evidence: str | None = None, category: str | None = None) -> dict[str, Any]:
    return {"value": value, "state": state, "confidence": "HIGH", "evidence": evidence, "category": category}


def record(worker_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "worker_id": worker_id,
        "name": name,
        "consent": True,
        "excluded": False,
        "exclusion_reason": None,
        "answers": {
            "age_years": answer("32", evidence="I am 32"),
            "compensation": answer("not enough", evidence="it is not enough"),
        },
    }
    base.update(overrides)
    return base


RECORDS = [
    record("w1", "Abebe"),
    record("w2", "Marta"),
    record(
        "w3",
        "Hanna",
        excluded=True,
        exclusion_reason="Reported as a child, so the interview was stopped.",
        answers={"age_years": answer("15", evidence="15"), "compensation": answer(None, state="NOT_ASKED")},
    ),
]


class StubProvider:
    name = "stub"

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


# -- counting -------------------------------------------------------------- #


def test_excluded_records_are_counted_nowhere_but_the_exclusion_figures():
    summary = analysis.summarize(QUESTIONS, RECORDS, CATEGORIES)
    assert summary["interviews_counted"] == 2
    assert summary["records_total"] == 3
    assert summary["excluded_total"] == 1
    assert summary["excluded"][0]["name"] == "Hanna"
    age = summary["questions"]["age_years"]
    assert sum(age["category_counts"].values()) == 2
    assert sum(age["state_counts"].values()) == 2
    assert "15" not in json.dumps(summary)


def test_category_counts_come_from_the_assignments():
    summary = analysis.summarize(QUESTIONS, RECORDS, CATEGORIES)
    assert summary["questions"]["compensation"]["category_counts"] == {"Not enough": 2}
    assert summary["questions"]["age_years"]["category_counts"] == {"25 to 40": 1, "Under 25": 1}


def test_a_category_already_written_onto_an_answer_wins():
    records = [record("w1", "Abebe", answers={"age_years": answer("32", category="Over 30")})]
    summary = analysis.summarize([QUESTIONS[0]], records, CATEGORIES)
    assert summary["questions"]["age_years"]["category_counts"] == {"Over 30": 1}


def test_state_counts_and_answered_track_the_stored_states():
    records = [
        record("w1", "Abebe"),
        record("w2", "Marta", answers={"age_years": answer(None, state="REFUSED"), "compensation": answer("yes")}),
    ]
    summary = analysis.summarize(QUESTIONS, records, None)
    assert summary["questions"]["age_years"]["state_counts"] == {"STATED": 1, "REFUSED": 1}
    assert summary["questions"]["age_years"]["answered"] == 1


def test_quotes_are_capped_so_the_model_is_not_tempted_to_count_them():
    many = [record(f"w{index}", f"P{index}") for index in range(20)]
    summary = analysis.summarize(QUESTIONS, many, None)
    assert len(summary["questions"]["age_years"]["quotes"]) == analysis.MAX_QUOTES_PER_QUESTION
    assert summary["interviews_counted"] == 20


def test_consent_is_counted_only_among_counted_records():
    records = [record("w1", "Abebe"), record("w2", "Marta", consent=False)]
    assert analysis.summarize(QUESTIONS, records, None)["consented"] == 1


def test_headline_is_model_free_and_names_the_exclusions():
    summary = analysis.summarize(QUESTIONS, RECORDS, CATEGORIES)
    assert analysis.headline(summary) == "2 interview(s) counted, 1 excluded and not counted."


# -- narrating ------------------------------------------------------------- #


def test_the_model_is_handed_counts_and_never_raw_records():
    provider = StubProvider({"answer": "Two of two said pay is not enough."})
    summary = analysis.summarize(QUESTIONS, RECORDS, CATEGORIES)
    analysis.answer(provider, "How many said pay is not enough?", summary)
    request = provider.calls[0]["user"]
    payload = json.loads(request.split("Counts for this batch:\n\n", 1)[1].split("\n\nAdministrator")[0])
    assert payload["questions"]["compensation"]["category_counts"] == {"Not enough": 2}
    assert "transcript" not in request


def test_the_prompt_forbids_the_model_from_doing_arithmetic():
    prompt = analysis.build_analysis_prompt()
    assert "You do not compute" in prompt
    assert "Never add, subtract, average" in prompt
    assert "Excluded interviews are excluded on purpose" in prompt


def test_only_the_last_few_turns_of_history_are_sent():
    history = [{"role": "admin", "text": f"turn {index}"} for index in range(12)]
    request = analysis.build_analysis_request("and now?", {"interviews_counted": 0}, history)
    assert "turn 11" in request
    assert "turn 0" not in request


def test_a_provider_failure_says_so_instead_of_inventing_an_answer():
    reply = analysis.answer(StubProvider(ProviderError("no key")), "how many?", {"interviews_counted": 2})
    assert "could not be reached" in reply


def test_an_empty_model_reply_is_reported_rather_than_shown_as_an_answer():
    reply = analysis.answer(StubProvider({"answer": "  "}), "how many?", {"interviews_counted": 2})
    assert "nothing usable" in reply
