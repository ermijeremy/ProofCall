"""Who gets called. The model parses the instruction; code draws the sample.

Every assertion here is about the second half of that split, because the second
half is the one that places phone calls. A misread instruction must fail loudly:
an unmatched name is reported, two people with the same name is a question rather
than a coin toss, and an unusable specification selects nobody at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence import selection
from app.intelligence.providers.base import ProviderError

NAMES = ["Abebe", "Marta", "Yonas", "Hanna"]


def roster(names: list[str] | None = None) -> list[dict[str, Any]]:
    return [
        {"worker_id": f"w{index}", "name": name, "phone_number": f"+2519000000{index}"}
        for index, name in enumerate(names or NAMES, start=1)
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


def spec(**overrides: Any) -> dict[str, Any]:
    """Build a spec the way the parser would, so tests never resolve a shape the
    parser cannot produce — ``remaining`` implying already-called exclusion is
    normalization, and skipping it would test a spec that never occurs."""

    base = {"mode": "all", "confidence": "HIGH"}
    base.update(overrides)
    return selection.normalize_spec(base)


# -- parsing --------------------------------------------------------------- #


def test_the_prompt_forbids_the_model_from_choosing_people():
    prompt = selection.build_selection_prompt()
    assert "You do not choose the people" in prompt
    assert "Never return names for a random selection" in prompt


def test_parse_selection_normalizes_a_well_formed_response():
    provider = StubProvider({"mode": "fraction", "fraction": 0.5, "confidence": "HIGH"})
    parsed = selection.parse_selection(provider, "call half of them at random")
    assert parsed["mode"] == "fraction"
    assert parsed["fraction"] == 0.5


def test_a_malformed_response_becomes_unclear_rather_than_everybody():
    parsed = selection.parse_selection(StubProvider({"mode": "call_everyone_now"}), "??")
    assert parsed["mode"] == "unclear"


def test_a_provider_failure_becomes_unclear_rather_than_everybody():
    parsed = selection.parse_selection(StubProvider(ProviderError("no key")), "call all")
    assert parsed["mode"] == "unclear"


def test_a_count_of_zero_is_not_a_count():
    parsed = selection.parse_selection(StubProvider({"mode": "count", "count": 0}), "call none")
    assert parsed["count"] is None


# -- resolution ------------------------------------------------------------ #


def test_all_takes_everyone():
    resolution = selection.resolve(spec(mode="all"), roster())
    assert [person["name"] for person in resolution["selected"]] == NAMES
    assert resolution["error"] is None


def test_a_count_draws_exactly_that_many():
    resolution = selection.resolve(spec(mode="count", count=2), roster())
    assert len(resolution["selected"]) == 2


def test_half_of_twenty_draws_ten():
    resolution = selection.resolve(spec(mode="fraction", fraction=0.5), roster([f"P{index}" for index in range(20)]))
    assert len(resolution["selected"]) == 10


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.5, 11), (0.2, 5), (0.1, 3), (1.0, 21)],
)
def test_a_fraction_of_an_odd_roster_rounds_up(fraction: float, expected: int):
    """Rounding up rather than down: asking for half of 21 and getting 10 would
    quietly under-sample, and the admin has no way to see that from the reply."""

    resolution = selection.resolve(
        spec(mode="fraction", fraction=fraction), roster([f"P{index}" for index in range(21)])
    )
    assert len(resolution["selected"]) == expected


def test_a_fraction_is_taken_against_the_candidates_not_the_whole_roster():
    people = roster()
    resolution = selection.resolve(
        spec(mode="fraction", fraction=0.5, exclude_already_called=True),
        people,
        already_called={"w1", "w2"},
    )
    assert len(resolution["selected"]) == 1  # half of the two remaining, rounded up
    assert {person["worker_id"] for person in resolution["selected"]} <= {"w3", "w4"}


def test_named_people_resolve_case_insensitively_and_only_them():
    resolution = selection.resolve(spec(mode="explicit", names=["abebe", "MARTA"]), roster())
    assert sorted(person["name"] for person in resolution["selected"]) == ["Abebe", "Marta"]


def test_an_unmatched_name_is_reported_rather_than_dropped():
    resolution = selection.resolve(spec(mode="explicit", names=["Abebe", "Ghost"]), roster())
    assert resolution["unmatched"] == ["Ghost"]
    assert [person["name"] for person in resolution["selected"]] == ["Abebe"]


def test_nothing_matching_at_all_selects_nobody():
    resolution = selection.resolve(spec(mode="explicit", names=["Ghost"]), roster())
    assert resolution["selected"] == []
    assert resolution["error"] == "nothing_matched"


def test_two_people_with_the_same_name_is_a_question_not_a_coin_toss():
    people = roster(["Abebe", "Abebe", "Marta"])
    resolution = selection.resolve(spec(mode="explicit", names=["Abebe"]), people)
    assert "abebe" in {key.lower() for key in resolution["ambiguous"]}
    assert resolution["selected"] == []


def test_remaining_skips_anyone_already_called():
    resolution = selection.resolve(spec(mode="remaining"), roster(), already_called={"w1", "w3"})
    assert {person["worker_id"] for person in resolution["selected"]} == {"w2", "w4"}
    assert resolution["skipped_already_called"] == ["w1", "w3"] or set(
        resolution["skipped_already_called"]
    ) == {"w1", "w3"}


def test_remaining_with_nobody_left_is_an_error_not_an_empty_success():
    resolution = selection.resolve(spec(mode="remaining"), roster(), already_called={"w1", "w2", "w3", "w4"})
    assert resolution["selected"] == []
    assert resolution["error"] == "no_candidates"


def test_over_requesting_reports_how_many_exist():
    resolution = selection.resolve(spec(mode="count", count=99), roster())
    assert resolution["error"] == "too_many_requested"
    assert resolution["available"] == 4
    assert resolution["requested"] == 99


def test_an_unclear_specification_selects_nobody():
    resolution = selection.resolve(spec(mode="unclear"), roster())
    assert resolution["selected"] == []
    assert resolution["error"] == "unclear"


def test_the_draw_goes_through_the_shared_sampler():
    """The sampler is injectable so the batch path and the campaign path stay one
    implementation. Selection never shuffles a list itself."""

    seen: dict[str, Any] = {}

    def sampler(candidates: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
        seen["size"] = size
        return candidates[:size]

    resolution = selection.resolve(spec(mode="count", count=2), roster(), sampler=sampler)
    assert seen["size"] == 2
    assert [person["name"] for person in resolution["selected"]] == ["Abebe", "Marta"]
