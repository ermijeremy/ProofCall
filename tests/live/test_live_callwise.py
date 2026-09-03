"""What the real model actually reads out of a real Amharic interview.

The offline suite in ``tests/unit/test_callwise_record.py`` is circular by
construction: it hands the record builder a reading we wrote by hand and checks
the record. This file is the other half, and the only test in the project that
can fail for the reason that matters -- the parse being wrong rather than the
plumbing.

Each assertion below is a trap the transcript actually contains, not a
paraphrase of the prompt:

* ``የራሴ አይደለም`` -- "it is not my own" -- contains the word for one's own
  business and means the opposite of self-employed.
* ``አላቋረጥኩም`` -- "I have not stopped" -- is grammatically negative and affirms
  that the work continued.
* ``13 ሺ`` is thirteen thousand, not thirteen.
* ``አምስት`` and ``ሰባት`` are five and seven written as words, and the weekly hours
  are their product, never stated.
* ``ሃምሌ ... 2018`` is an Ethiopian-calendar month and year. Converting it would
  invent a tenure; it has to come back unresolved.
* ``ወደ ቀጣዩ`` -- "on to the next one" -- is a request to skip, surrounded by six
  answers that are the bare word yes.

A keyword table gets all seven wrong. That is why the parser is the model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.intelligence import callwise_record, questions
from app.intelligence.batch_engine import BatchIntelligence
from app.intelligence.callwise_rules import evaluate
from app.intelligence.providers.gemini import GeminiProvider, api_keys_from_environment

pytestmark = pytest.mark.live

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "callwise" / "callwise_am_placed_employee.json"


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """One real extraction, shared by every test in the file.

    Module-scoped on purpose: this is a paid call, and every assertion below is
    about the same reading of the same interview.
    """

    if not api_keys_from_environment():
        pytest.skip("no GEMINI_API_KEY configured")
    engine = BatchIntelligence(GeminiProvider())
    return engine.extract_answers(
        "", "BSG-2026-0143", questions.fixed_question_set(), payload["turns"]
    )


@pytest.fixture(scope="module")
def record(extraction: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    kpi = evaluate(
        extraction["answers"],
        age_assessment=extraction["age_assessment"],
        safeguarding=bool(extraction.get("safeguarding_flag")),
    )
    return callwise_record.build_record(
        extraction,
        kpi,
        payload,
        record_id="CW-LIVE-001",
        beneficiary_id="BSG-2026-0143",
        training_cohort_id="COH-2026-04",
        interview_date="2026-09-03",
    )


def typed(extraction: dict[str, Any], slug: str) -> Any:
    entry = extraction["answers"].get(slug) or {}
    return entry.get("normalized")


# -- the reading ------------------------------------------------------------- #


def test_consent_is_read_as_granted(extraction) -> None:
    assert extraction["consent"] is True
    assert extraction["consent_details"]["state"] == "granted_no_name"
    assert extraction["age_assessment"] == "ADULT"


def test_not_my_own_is_read_as_employed_not_self_employed(extraction) -> None:
    """The self-employment word appears inside a negation."""

    assert typed(extraction, "employment_status") == "working"
    assert typed(extraction, "employment_type") == "employee"


def test_the_work_is_read_as_sales(extraction) -> None:
    assert typed(extraction, "sales_related") is True


def test_thirteen_thousand_is_not_thirteen(extraction) -> None:
    pay = typed(extraction, "monthly_pay") or {}
    assert pay.get("amount_etb") == 13000


def test_a_deduction_is_reported(extraction) -> None:
    pay = typed(extraction, "monthly_pay") or {}
    assert pay.get("deductions_reported")


def test_five_days_of_seven_hours_is_thirty_five_hours(extraction) -> None:
    """Both numbers are words, and the product is never said aloud."""

    hours = typed(extraction, "working_hours") or {}
    assert hours.get("days_per_week") == 5
    assert hours.get("hours_per_day") == 7
    assert hours.get("hours_per_week") == 35


def test_i_have_not_stopped_affirms_continuity(extraction) -> None:
    """A negative verb form that answers the question yes."""

    assert typed(extraction, "employment_continuity") is True


def test_an_ethiopian_calendar_date_is_left_unresolved(extraction) -> None:
    """The one answer the model is asked to refuse to convert.

    What matters is that no Gregorian month is invented. Whether the answer is
    STATED with unresolvable words or VAGUE is a reporting detail; either way
    ``normalized`` is null and ``tenure_ok`` stays open.
    """

    assert typed(extraction, "start_date") is None


def test_asking_to_move_on_is_not_agreement(extraction) -> None:
    """Six answers here are the bare word yes. This one is not one of them."""

    entry = extraction["answers"]["worker_representation"]
    assert entry["state"] in {"REFUSED", "VAGUE"}
    assert entry["normalized"] is None


def test_the_remaining_rights_answers_are_read_as_yes(extraction) -> None:
    assert typed(extraction, "freedom_to_leave") is True
    assert typed(extraction, "equal_treatment") is True


def test_the_training_answers_are_read(extraction) -> None:
    assert typed(extraction, "age_years") == 21
    assert typed(extraction, "training_help") == "a_lot"
    assert typed(extraction, "satisfaction") == 3


def test_no_wage_complaint_is_invented_from_a_deduction(extraction) -> None:
    """Tax withheld lawfully is not pay withheld unlawfully."""

    assert extraction["wage_complaint"] is False


# -- evidence --------------------------------------------------------------- #


def test_every_stated_answer_cites_a_respondent_line(extraction, payload) -> None:
    turns = payload["turns"]
    for slug, entry in extraction["answers"].items():
        if entry["state"] != "STATED":
            continue
        turn = entry["evidence_turn"]
        assert turn is not None, f"{slug} cited no line"
        assert 1 <= turn <= len(turns), f"{slug} cited line {turn}"
        assert turns[turn - 1]["role"] == "caller", f"{slug} cited the interviewer"


def test_citations_survive_validation_against_the_transcript(record) -> None:
    """A citation dropped by :func:`resolve_turn` means the model miscounted."""

    for name in ("age_ok", "hours_ok", "wage_ok", "no_forced_labour", "no_discrimination"):
        assert record["clauses"][name]["evidence_turn"] is not None, name


# -- the record ------------------------------------------------------------- #


def test_the_record_is_not_counted_and_says_why(record) -> None:
    """Two clauses stay open, so the record must not claim a decent job."""

    assert record["clauses"]["tenure_ok"]["status"] == "unclear"
    assert record["clauses"]["association_ok"]["status"] == "unclear"
    assert record["counted"] is False
    assert record["unresolved_clause_count"] == 2
    assert "Unresolved: tenure_ok, association_ok." in record["summary_en"]


def test_no_clause_is_failed_on_this_interview(record) -> None:
    """The worker described a lawful job. A ``not_met`` here is a false positive.

    This is Criterion 5 as a test: one overstated clause about a real employer
    fails the pilot outright, and the old keyword rules produced four of them on
    exactly this transcript.
    """

    failed = [name for name, clause in record["clauses"].items() if clause["status"] == "not_met"]
    assert failed == []


def test_the_summary_describes_the_work_and_not_the_person(record) -> None:
    """English prose about the job: no name, no gender, no age.

    Two things this deliberately does not require. English is checked as the
    absence of Ethiopic script rather than as ASCII, because an em dash or a
    curly quote is ordinary English typography. And the pronoun search uses word
    boundaries, because "he" lives inside "The respondent" and a substring match
    would fail a summary that is perfectly correct.
    """

    import re

    summary = record["summary_en"]
    assert 20 < len(summary) <= 500
    assert not re.search(r"[ሀ-፿]", summary), "the summary must be English"
    for word in ("Aster", "she", "her", "hers", "he", "him", "his", "21"):
        assert not re.search(rf"\b{word}\b", summary, re.I), word


def test_the_record_carries_the_call_and_the_language(record) -> None:
    assert record["language"].startswith("am")
    assert record["call"]["duration_seconds"] == 282
    assert record["call"]["language_switched"] is False
