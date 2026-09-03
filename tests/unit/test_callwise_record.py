"""The Callwise record built from one Gemini extraction of one interview.

Every test drives the whole seam -- :meth:`BatchIntelligence.extract_answers`
over a stub provider, then the rule engine, then the record builder -- because the
contract spans all three: the model returns a typed reading, code checks it
against a declared vocabulary, and only then does it become a clause.

The stub returns what the prompt asks Gemini for. That makes these tests a check
on *our* code, not on the model's reading: they prove that a given extraction
becomes the right record, including when the extraction is wrong. Whether the
real model reads this Amharic correctly is a separate question, and only
``tests/live/test_live_callwise.py`` can answer it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.intelligence import callwise_record, questions
from app.intelligence.batch_engine import BatchIntelligence
from app.intelligence.callwise_rules import evaluate

FIXTURE = Path("tests/fixtures/callwise/callwise_am_placed_employee.json")

# turn, verbatim words, typed reading, state. Turns are the respondent's 1-based
# positions in the fixture's own turn array.
INTERVIEW: dict[str, tuple[Any, str | None, Any, str]] = {
    "age_years": (8, "21", 21, "STATED"),
    "employment_status": (10, "እ በክፍያ ተቀጥረያለሁ", "working", "STATED"),
    "sales_related": (12, "አዎ", True, "STATED"),
    "employment_type": (14, "እ ክፍያ የሚከፍለኝ በድርጅቱ ነው የራሴ አይደለም", "employee", "STATED"),
    # An Ethiopian-calendar month; the model is told to leave it unresolved.
    "start_date": (16, "ሃምሌ በዚህ አመት ወይም 2018", None, "VAGUE"),
    "employment_continuity": (18, "አይ አላቋረጥኩም እየሰራሁ ነው እስካሁን", True, "STATED"),
    "working_hours": (
        20,
        "በሳምንት አምስት ቀን ነው የምሰራው እና ቀን ደግሞ ሰባት ሰአት እሰራለሁ",
        {"days_per_week": 5, "hours_per_day": 7, "hours_per_week": 35},
        "STATED",
    ),
    "monthly_pay": (
        22,
        "አዎ ግብር ይቆረጣል ያው ከግብር ውጪ ወደ 13 ሺ ብር አገኛለሁ",
        {"amount_etb": 13000, "deductions_reported": "tax"},
        "STATED",
    ),
    "freedom_to_leave": (24, "አዎ እችላለሁ ችግር የለውም", True, "STATED"),
    "equal_treatment": (26, "አዎ ተመሳሳይ ነው", True, "STATED"),
    # "to the next one": a request to move on, not an answer.
    "worker_representation": (28, "ወደ ቀጣዩ", None, "REFUSED"),
    "time_to_first_work": (30, "ሁለት ወር", 2, "STATED"),
    "training_help": (32, "እ በጣም ረድቶኛል", "a_lot", "STATED"),
    "skills_used": (34, "የመሸጥ ቴክኒክ", "selling technique", "STATED"),
    "satisfaction": (36, "ሶስት", 3, "STATED"),
    "other_changes": (38, "ያው ስራ አግኝቻለሁ እና ለዛ ረድቶኛል እሱን ከሆነ የምትፈልጊው", "found work", "STATED"),
}


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


SUMMARY = (
    "Works as a paid employee in a sales role, five days a week and about "
    "thirty-five hours, taking home around 13,000 birr after tax. Did not give a "
    "start date that could be pinned to a month. Says the training helped a lot "
    "and rates it three out of five."
)


class StubProvider:
    """Returns the JSON the extraction prompt asks for, with overrides."""

    name = "stub"

    def __init__(self, *, answers: dict[str, tuple] | None = None, **top: Any) -> None:
        self.answers = {**INTERVIEW, **(answers or {})}
        self.top = top
        self.system = ""
        self.user = ""

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self.system, self.user = system, user
        return {
            "consent": True,
            "consent_details": {"state": "granted_no_name", "name": False, "quote": True,
                                "voice": False, "photo": False},
            "language": "am-ET",
            "interview_stopped": False,
            "stop_reason": None,
            "age_assessment": "ADULT",
            "wage_complaint": False,
            "summary_en": SUMMARY,
            "answers": {
                slug: {"value": words, "normalized": typed, "state": state,
                       "confidence": "HIGH", "evidence": words, "evidence_turn": turn}
                for slug, (turn, words, typed, state) in self.answers.items()
            },
            **self.top,
        }


def build(payload: dict[str, Any], provider: StubProvider | None = None, **kwargs: Any) -> dict[str, Any]:
    """One record, through extraction and the rule engine, as production does it."""

    extraction = BatchIntelligence(provider or StubProvider()).extract_answers(
        "", "BSG-2026-0143", questions.fixed_question_set(), payload["turns"]
    )
    kpi = evaluate(extraction["answers"], age_assessment=extraction["age_assessment"],
                   safeguarding=bool(extraction.get("safeguarding_flag")))
    return callwise_record.build_record(
        extraction, kpi, payload, record_id="CW-001", beneficiary_id="BSG-2026-0143",
        training_cohort_id="COH-2026-04", interview_date="2026-09-03", **kwargs
    )


# -- the record ------------------------------------------------------------- #


def test_nine_clauses_in_specification_order(payload):
    assert tuple(build(payload)["clauses"]) == (
        "age_ok", "hours_ok", "tenure_ok", "wage_ok", "no_child_labour",
        "no_forced_labour", "no_discrimination", "association_ok", "seasonal_over_6m",
    )


def test_typed_answers_become_the_employment_block(payload):
    assert build(payload)["employment"] == {
        "status": "working", "type": "employee", "sales_related": True, "employer_name": None,
        "start_date": None, "months_since_start": None, "hours_per_week": 35,
        "weeks_per_year": None, "monthly_take_home_etb": 13000,
        "deductions_reported": "tax", "continuous": True,
    }


def test_call_block_comes_from_the_transcript(payload):
    assert build(payload, attempts=2, cost_usd=0.28)["call"] == {
        "attempts": 2, "disposition": "completed", "duration_seconds": 282,
        "language_switched": False, "cost_usd": 0.28,
    }


def test_two_open_clauses_leave_the_record_uncounted(payload):
    record = build(payload)
    assert record["counted"] is False
    assert record["unresolved_clause_count"] == 2
    assert record["clauses"]["tenure_ok"]["status"] == "unclear"
    assert record["clauses"]["association_ok"]["status"] == "unclear"


def test_an_ethiopian_calendar_date_is_left_unresolved(payload):
    """Converting it would invent a tenure the respondent never claimed."""

    record = build(payload)
    assert record["employment"]["start_date"] is None
    assert record["clauses"]["tenure_ok"] == {"status": "unclear", "confidence": 0.3,
                                             "evidence_turn": None, "source": "none"}


def test_asking_to_move_on_is_not_agreement(payload):
    """Every surrounding answer was "yes"; this one was a request to skip."""

    clause = build(payload)["clauses"]["association_ok"]
    assert clause["status"] == "unclear"
    assert clause["source"] == "none"


def test_the_summary_is_the_models_prose_plus_the_open_clauses(payload):
    """The model writes it; code appends what code alone knows."""

    summary = build(payload)["summary_en"]
    assert summary.startswith("Works as a paid employee in a sales role")
    assert summary.endswith("Unresolved: tenure_ok, association_ok.")
    assert len(summary) <= 500


def test_a_summary_longer_than_the_limit_is_cut(payload):
    summary = build(payload, StubProvider(summary_en="word " * 200))["summary_en"]
    assert len(summary) == 500


def test_a_missing_summary_leaves_only_what_code_can_say(payload):
    assert build(payload, StubProvider(summary_en=None))["summary_en"] == (
        "Unresolved: tenure_ok, association_ok."
    )


def test_a_complete_record_needs_no_unresolved_tail(payload):
    summary = build(payload, StubProvider(answers={
        "start_date": (16, "ሃምሌ 2025", "2025-07", "STATED"),
        "worker_representation": (28, "አዎ አለ", True, "STATED"),
    }))["summary_en"]
    assert summary == SUMMARY
    assert "Unresolved" not in summary


# -- evidence --------------------------------------------------------------- #


def test_transcript_reaches_the_model_numbered(payload):
    provider = StubProvider()
    build(payload, provider)
    assert "[8] caller: 21" in provider.user
    assert "[28] caller: ወደ ቀጣዩ" in provider.user


def test_each_clause_cites_the_line_it_rests_on(payload):
    record = build(payload)
    assert {name: clause["evidence_turn"] for name, clause in record["clauses"].items()} == {
        "age_ok": 8, "hours_ok": 20, "tenure_ok": None, "wage_ok": 22,
        "no_child_labour": 8, "no_forced_labour": 24, "no_discrimination": 26,
        "association_ok": None, "seasonal_over_6m": 18,
    }


def test_a_citation_that_does_not_hold_is_dropped(payload):
    """A miscounted turn would point a reviewer at the wrong line."""

    record = build(payload, StubProvider(answers={
        "freedom_to_leave": (21, "አዎ እችላለሁ ችግር የለውም", True, "STATED"),  # 21 is the interviewer
        "equal_treatment": (99, "አዎ ተመሳሳይ ነው", True, "STATED"),  # off the end
    }))
    # Both quotes are unique to one respondent line, so the line is recovered.
    assert record["clauses"]["no_forced_labour"]["evidence_turn"] == 24
    assert record["clauses"]["no_discrimination"]["evidence_turn"] == 26


def test_an_ambiguous_quote_with_no_citation_gets_no_turn(payload):
    """"አዎ" answers six questions. Offering the first for the fourth is fabrication."""

    clause = build(payload, StubProvider(answers={
        "freedom_to_leave": (None, "አዎ", True, "STATED"),
    }))["clauses"]["no_forced_labour"]
    assert clause["evidence_turn"] is None
    assert clause["source"] == "none"
    # The status stands: the worker answered, only the citation is missing.
    assert clause["status"] == "met"


def test_interviewer_words_are_never_evidence(payload):
    record = build(payload, StubProvider(answers={
        "equal_treatment": (25, "እዛው ላይ ተመሳሳይ ስራ ከሚሰሩ ሌሎች ሰዎች ጋር እኩል ክፍያና አያያዝ ያገኛሉ?",
                            True, "STATED"),
    }))
    assert record["clauses"]["no_discrimination"]["evidence_turn"] is None


# -- the vocabulary guard --------------------------------------------------- #


@pytest.mark.parametrize(
    "slug, returned",
    [
        ("employment_status", "PLACED"),      # not in the declared set
        ("employment_type", "contractor"),    # plausible, still not declared
        ("training_help", "very much"),       # the note's words, not the vocabulary
        ("satisfaction", 7),                  # outside 1 to 5
        ("start_date", "2018-13"),            # not a month
        ("start_date", "ሃምሌ 2018"),           # not Gregorian
        ("age_years", "twenty one"),          # not a number
    ],
)
def test_a_value_outside_the_declared_vocabulary_is_dropped(payload, slug, returned):
    """Rejecting leaves a field open; accepting could make a clause indefensible."""

    assert questions.coerce_normalized(slug, returned) is None
    turn, words, _, state = INTERVIEW[slug]
    record = build(payload, StubProvider(answers={slug: (turn, words, returned, state)}))
    assert record["counted"] is False


def test_a_declared_value_survives():
    for slug, expected in (("employment_status", "working"), ("training_help", "a_lot"),
                           ("satisfaction", 3), ("start_date", "2026-04"), ("age_years", 21)):
        assert questions.coerce_normalized(slug, expected) == expected


def test_a_boolean_spelled_as_a_string_is_accepted():
    """One reading, not an ambiguity: some providers stringify a JSON boolean."""

    assert questions.coerce_normalized("freedom_to_leave", "true") is True
    assert questions.coerce_normalized("freedom_to_leave", "false") is False
    assert questions.coerce_normalized("freedom_to_leave", "maybe") is None


def test_a_typed_value_is_ignored_unless_the_answer_was_stated(payload):
    record = build(payload, StubProvider(answers={
        "monthly_pay": (22, None, {"amount_etb": 13000}, "REFUSED"),
    }))
    assert record["employment"]["monthly_take_home_etb"] is None
    assert record["clauses"]["wage_ok"]["status"] == "unclear"


# -- counting --------------------------------------------------------------- #


def test_every_clause_met_is_counted(payload):
    record = build(payload, StubProvider(answers={
        "start_date": (16, "ሃምሌ 2025", "2025-07", "STATED"),
        "worker_representation": (28, "አዎ አለ", True, "STATED"),
    }))
    assert record["unresolved_clause_count"] == 0
    assert record["counted"] is True


def test_one_failed_clause_is_not_unresolved(payload):
    """A "no" is a determination: it stops the count without leaving a gap."""

    record = build(payload, StubProvider(answers={
        "start_date": (16, "ሃምሌ 2025", "2025-07", "STATED"),
        "worker_representation": (28, "አይ የለም", False, "STATED"),
    }))
    assert record["clauses"]["association_ok"]["status"] == "not_met"
    assert record["unresolved_clause_count"] == 0
    assert record["counted"] is False


# -- consent, safeguarding, flags ------------------------------------------- #


def test_a_declined_call_still_produces_a_record(payload):
    record = build(payload, StubProvider(
        consent=False,
        consent_details={"state": "declined", "name": False, "quote": False,
                         "voice": False, "photo": False},
    ), age_band="18-24", gender="F")
    assert record["consent"]["state"] == "declined"
    assert {clause["status"] for clause in record["clauses"].values()} == {"unclear"}
    assert record["unresolved_clause_count"] == 9
    assert record["counted"] is False
    assert set(record["employment"].values()) == {None, "unknown"}
    assert record["aggregation_key"] is None
    assert record["quotes"] == []
    assert "consent_withdrawn" in record["flags"]
    assert record["call"]["duration_seconds"] == 282


def test_a_voided_call_is_blanked_the_same_way(payload):
    record = build(payload, StubProvider(
        consent=False,
        consent_details={"state": "voided", "name": False, "quote": True,
                         "voice": False, "photo": False},
    ))
    assert record["quotes"] == []
    assert record["training"]["satisfaction_1_5"] is None
    assert "consent_withdrawn" in record["flags"]


def test_a_child_is_flagged_and_never_counted(payload):
    record = build(payload, StubProvider(age_assessment="CHILD", interview_stopped=True,
                                         stop_reason="UNDER_MINIMUM_AGE"))
    assert record["clauses"]["no_child_labour"]["status"] == "not_met"
    assert record["counted"] is False
    assert "under_age_stop" in record["flags"]


def test_a_wage_complaint_is_flagged_from_the_extraction(payload):
    record = build(payload, StubProvider(wage_complaint=True))
    assert "wage_complaint" in record["flags"]
    # An escalation, not a clause: it changes nothing about the job's status.
    assert record["clauses"]["wage_ok"]["status"] == "met"


def test_a_small_cohort_is_flagged(payload):
    assert "small_cell_risk" in build(payload, cohort_size=3)["flags"]
    assert "small_cell_risk" not in build(payload, cohort_size=42)["flags"]


def test_only_the_open_answers_are_quotable(payload):
    """Consent to be quoted is not consent to reprint fifteen answers."""

    assert build(payload)["quotes"] == [
        {"lang": "am", "text": INTERVIEW["skills_used"][1]},
        {"lang": "am", "text": INTERVIEW["other_changes"][1]},
    ]


def test_no_quotes_without_consent_to_be_quoted(payload):
    record = build(payload, StubProvider(
        consent_details={"state": "granted_no_name", "name": False, "quote": False,
                         "voice": False, "photo": False},
    ))
    assert record["quotes"] == []


def test_a_language_switch_is_flagged(payload):
    record = build({**payload, "detected_languages": ["am-ET", "en-US"]})
    assert record["call"]["language_switched"] is True
    assert "language_fallback_used" in record["flags"]


# -- the prompt ------------------------------------------------------------- #


def test_the_prompt_declares_every_normalized_field():
    prompt = questions.build_extraction_prompt(questions.fixed_question_set())
    for slug, spec in questions.NORMALIZED_FIELDS.items():
        assert f'"{slug}"' in prompt
        if spec["type"] == "enum":
            for value in spec["values"]:
                assert f'"{value}"' in prompt


def test_the_prompt_never_states_a_threshold():
    """The prompt may forbid comparison; it may not say what to compare against.

    So the check is for the values, not the vocabulary: "do not compare anything
    to a minimum" is the instruction working, and a phrase list would flag it.
    """

    prompt = questions.build_extraction_prompt(questions.fixed_question_set()).lower()
    for phrase in ("20 hours", "6 months", "15 years", "at least 20", "under 15", "over 6"):
        assert phrase not in prompt, phrase


def test_a_question_set_with_no_known_slug_gets_no_normalized_block():
    prompt = questions.build_extraction_prompt([{"slug": "favourite_colour", "text": "Colour?"}])
    assert "NORMALIZED" not in prompt
    assert "favourite_colour" in prompt
