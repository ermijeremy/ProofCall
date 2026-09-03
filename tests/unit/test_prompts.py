"""Prompt safety.

Two invariants matter more than anything else in this module, and neither is
visible by reading a prompt casually — which is exactly why they are tested.

**A prompt must never contain a threshold.** Told that six months is the bar,
some respondents will answer "six months"; told the same, the extraction model
starts deciding verdicts instead of reporting speech. Thresholds live in
:mod:`app.intelligence.criteria` and are applied by code only.

**A prompt must never contain an employer claim.** Reading the employer's number
back to the respondent is a leading question, and acquiescence to it would
destroy the only thing the call produces: an independent account.

The threshold checks use word-boundary matching deliberately. ``"W015"`` contains
the substring ``15``, so a naive ``"15" in prompt`` test would fail on a perfectly
safe prompt and teach the next person to delete the test.
"""

from __future__ import annotations

import re

import pytest

from app.contracts.integration import ProgrammeContext
from app.intelligence import prompts
from app.intelligence.criteria import DECENT_WORK_CRITERIA
from app.intelligence.engine import EXPECTED_FACTS

PROGRAMME = ProgrammeContext(programme_name="Youth Employment Programme", language="am")

#: Standalone integers, so an identifier like "W015" is not mistaken for 15.
STANDALONE_INTEGER = re.compile(r"(?<!\w)\d+(?!\w)")

#: Every numeric threshold, in the digit form a prompt could leak it as.
NUMERIC_THRESHOLDS = tuple(
    str(int(value))
    for value in DECENT_WORK_CRITERIA.values()
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer()
)

#: The same thresholds as spoken words, which is how a voice prompt would say them.
SPELLED_THRESHOLDS = ("fifteen", "twenty", "six months", "6 months")

#: Whatever the employer told the programme. None of it may reach either prompt.
EMPLOYER_CLAIM_LEAKS = (
    "8500",
    "employer reports",
    "employer reported",
    "the company says",
    "we have been told",
    "according to the employer",
    "our records show",
)


def _worker(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"worker_id": "W015", "preferred_language": None}
    base.update(overrides)
    return base


def _prompts() -> dict[str, str]:
    """Both prompts, keyed for parametrization, built the way the engine builds them."""

    return {
        "interview": prompts.build_interview_prompt(_worker(), PROGRAMME),
        "extraction": prompts.build_extraction_prompt(),
    }


ALL_PROMPTS = _prompts()
PROMPT_NAMES = sorted(ALL_PROMPTS)


# --------------------------------------------------------------------------- #
# No thresholds
# --------------------------------------------------------------------------- #


def test_interview_prompt_contains_no_standalone_number() -> None:
    """The strongest available form of the rule: no bare numerals at all.

    Every threshold is a number, so forbidding standalone numerals outright
    removes the whole class of leak rather than the values we thought to list.
    """

    assert STANDALONE_INTEGER.findall(ALL_PROMPTS["interview"]) == []


@pytest.mark.parametrize("threshold", NUMERIC_THRESHOLDS)
def test_interview_prompt_discloses_no_numeric_threshold(threshold: str) -> None:
    assert re.search(rf"\b{threshold}\b", ALL_PROMPTS["interview"]) is None


@pytest.mark.parametrize("name", PROMPT_NAMES)
@pytest.mark.parametrize("phrase", SPELLED_THRESHOLDS)
def test_no_prompt_speaks_a_threshold_aloud(name: str, phrase: str) -> None:
    assert phrase not in ALL_PROMPTS[name].lower()


def test_the_word_boundary_check_is_the_reason_this_passes() -> None:
    """Guards the guard: "W015" must not read as the number 15.

    If this ever fails, the threshold tests above have become vacuous or
    over-eager, depending on the direction.
    """

    assert re.search(r"\b15\b", "Interview reference: W015") is None
    assert re.search(r"\b15\b", "at least 15 years") is not None


def test_the_interview_prompt_forbids_naming_a_qualifying_level() -> None:
    interview = ALL_PROMPTS["interview"]
    assert "Never mention any minimum, target, requirement, or qualifying level" in interview


def test_the_extraction_prompt_forbids_comparing_to_a_minimum() -> None:
    extraction = ALL_PROMPTS["extraction"]
    assert "Do not compare anything to a minimum, a target, or a requirement" in extraction


def test_programme_criteria_never_reach_the_interview_prompt() -> None:
    """The live leak vector: Member B populates ``ProgrammeContext.criteria``.

    That field exists so the dashboard can render the active thresholds
    read-only. If it were interpolated into the interview prompt, every call
    would coach the respondent — so the prompt must ignore it entirely.
    """

    leaky = ProgrammeContext(
        programme_name="Youth Employment Programme",
        language="am",
        criteria={"minimum_age": 15, "minimum_duration_months": 6, "note": "at least six months"},
    )
    prompt = prompts.build_interview_prompt(_worker(), leaky)
    assert STANDALONE_INTEGER.findall(prompt) == []
    assert "minimum_age" not in prompt
    assert "six months" not in prompt.lower()


def test_a_threshold_change_cannot_change_a_prompt() -> None:
    """Raising the bar must not alter a single character of either prompt."""

    before = _prompts()
    raised = dict(DECENT_WORK_CRITERIA, minimum_age=18, minimum_duration_months=24)
    # The prompt builders take no criteria argument at all; this asserts that
    # design property rather than trusting it.
    assert _prompts() == before
    assert raised["minimum_age"] != DECENT_WORK_CRITERIA["minimum_age"]


# --------------------------------------------------------------------------- #
# No employer claims
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", PROMPT_NAMES)
@pytest.mark.parametrize("leak", EMPLOYER_CLAIM_LEAKS)
def test_no_prompt_repeats_an_employer_claim(name: str, leak: str) -> None:
    assert leak not in ALL_PROMPTS[name].lower()


def test_the_interview_prompt_says_it_holds_no_employer_figures() -> None:
    interview = ALL_PROMPTS["interview"]
    assert "You have no figures from the employer and must not" in interview
    assert "Never read a number back to them as if you already knew it" in interview


def test_the_interview_prompt_forbids_offering_a_number_to_agree_with() -> None:
    """Acquiescence bias is the failure mode a refusal or a vague answer invites."""

    interview = ALL_PROMPTS["interview"]
    assert "do not offer them a\nnumber to agree with" in interview
    assert "never propose a figure for them to confirm" in interview


# --------------------------------------------------------------------------- #
# No verdicts from the model
# --------------------------------------------------------------------------- #


def test_the_requested_json_shape_asks_for_no_status_or_verdict() -> None:
    """Checks the shape the model is told to return, not the prohibitions.

    "MET" and "verdict" appear later in the prompt precisely because they are
    forbidden, so the assertion is scoped to the schema block above that.
    """

    schema_block = ALL_PROMPTS["extraction"].split("Report every one of these facts")[0]
    for banned in ("MET", "NOT_MET", "verdict", "CONFIRMED", "material", "contradiction"):
        assert banned not in schema_block


def test_the_extraction_prompt_prohibits_verdicts_explicitly() -> None:
    assert "Do not output MET, NOT_MET, a verdict, or any recommendation." in ALL_PROMPTS["extraction"]


def test_the_extraction_prompt_asks_only_for_the_four_fact_states() -> None:
    extraction = ALL_PROMPTS["extraction"]
    assert '"state": "STATED" | "REFUSED" | "VAGUE" | "NOT_ASKED"' in extraction
    # MET and STOPPED are clause statuses, which only the rule engine assigns.
    assert "STOPPED" not in extraction


# --------------------------------------------------------------------------- #
# The fact vocabulary stays aligned
# --------------------------------------------------------------------------- #


def test_the_extraction_prompt_names_every_expected_fact() -> None:
    extraction = ALL_PROMPTS["extraction"]
    for name in EXPECTED_FACTS:
        assert f'"{name}"' in extraction


def test_the_engine_and_the_prompt_agree_on_the_fact_list() -> None:
    """One vocabulary, one source. A prompt-only fact would never be evaluated."""

    assert EXPECTED_FACTS == tuple(name for name, _ in prompts.FACT_SCHEMA)


def test_every_fact_is_described_once() -> None:
    names = [name for name, _ in prompts.FACT_SCHEMA]
    assert len(names) == len(set(names))
    assert all(description.strip() for _, description in prompts.FACT_SCHEMA)


# --------------------------------------------------------------------------- #
# Consent and safeguarding
# --------------------------------------------------------------------------- #


def test_the_interview_prompt_opens_with_consent() -> None:
    interview = ALL_PROMPTS["interview"]
    assert "answering is voluntary and they may stop at any time" in interview
    assert "ask whether they are willing to answer some questions" in interview
    assert "Do not persuade them." in interview


def test_the_interview_prompt_asks_age_first() -> None:
    """Age has to come first or a child gets asked the rest of the questions."""

    interview = ALL_PROMPTS["interview"]
    assert "FIRST QUESTION, ALWAYS" in interview
    assert "Ask their age before anything else" in interview
    assert interview.index("Ask their age before anything else") < interview.index("WHAT TO FIND OUT")


def test_a_child_ends_the_call_without_being_told_why() -> None:
    """Explaining the stop is the one thing that could expose them.

    A child who is told their age disqualified them can be coached — or
    pressured — before anyone follows up.
    """

    interview = ALL_PROMPTS["interview"]
    assert "do not ask a single further\nquestion" in interview
    assert "Do not explain why you are stopping, do not mention any age rule" in interview
    assert "do not tell them their answer was a problem" in interview


def test_a_refusal_is_accepted_immediately() -> None:
    interview = ALL_PROMPTS["interview"]
    assert "accept it immediately" in interview
    assert "Never ask twice" in interview


def test_vague_dates_get_bounded_follow_ups_that_name_no_span() -> None:
    interview = ALL_PROMPTS["interview"]
    assert "Make at most three such attempts." in interview
    assert "without naming any span of months" in interview
    assert "Do not guess on their behalf" in interview


# --------------------------------------------------------------------------- #
# Language
# --------------------------------------------------------------------------- #


def test_the_worker_language_beats_the_programme_language() -> None:
    """The programme language is a cohort default; the record is about a person."""

    prompt = prompts.build_interview_prompt(_worker(preferred_language="om"), PROGRAMME)
    assert "Speak Afaan Oromo for the whole call." in prompt
    assert "Amharic" not in prompt


def test_the_programme_language_applies_when_the_worker_has_none() -> None:
    assert "Speak Amharic for the whole call." in ALL_PROMPTS["interview"]


def test_an_unknown_language_code_is_passed_through_untranslated() -> None:
    """Better to name the code than to silently conduct the call in Amharic."""

    prompt = prompts.build_interview_prompt(_worker(preferred_language="zz"), PROGRAMME)
    assert "Speak zz for the whole call." in prompt


def test_the_agent_follows_the_respondent_into_another_language() -> None:
    assert "switch to theirs and stay there" in ALL_PROMPTS["interview"]


def test_evidence_must_be_quoted_in_the_language_spoken() -> None:
    """Translating a quote makes it unverifiable against the transcript."""

    extraction = ALL_PROMPTS["extraction"]
    assert "in the language they\nspoke" in extraction
    assert "Do not translate, tidy, shorten, or paraphrase." in extraction
    assert "Quote the respondent, not\nthe interviewer." in extraction


# --------------------------------------------------------------------------- #
# Worker and programme identity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "worker",
    [
        {"worker_id": "W015", "preferred_language": "am"},
        pytest.param(None, id="none"),
    ],
)
def test_the_prompt_builds_for_any_worker_shape(worker: object) -> None:
    """Called with a dict, an ORM row, or nothing at all, it must still build."""

    prompt = prompts.build_interview_prompt(worker, PROGRAMME)
    assert PROGRAMME.programme_name in prompt
    assert "Interview reference:" in prompt


def test_an_orm_style_object_is_read_by_attribute() -> None:
    class Row:
        worker_id = "W007"
        preferred_language = "ti"

    prompt = prompts.build_interview_prompt(Row(), PROGRAMME)
    assert "W007" in prompt
    assert "Speak Tigrinya for the whole call." in prompt


def test_a_missing_worker_id_does_not_produce_a_none_in_the_prompt() -> None:
    prompt = prompts.build_interview_prompt({}, PROGRAMME)
    assert "None" not in prompt
    assert "unknown" in prompt


# --------------------------------------------------------------------------- #
# Extraction request
# --------------------------------------------------------------------------- #


def test_the_extraction_system_prompt_is_constant_and_cacheable() -> None:
    """No transcript, no claim, no threshold — so it never varies per call."""

    assert prompts.build_extraction_prompt() == prompts.build_extraction_prompt()


def test_the_transcript_travels_in_the_user_turn_only() -> None:
    transcript = "Interviewer: How old are you?\nWorker: I am twenty four years old."
    request = prompts.build_extraction_request(transcript)
    assert transcript in request
    assert transcript not in prompts.build_extraction_prompt()


def test_parser_prompt_contains_the_canonical_real_valued_record_example() -> None:
    extraction = prompts.build_extraction_prompt()
    assert '"record_id": "CW-014"' in extraction
    assert '"seasonal_over_6m"' in extraction
    assert '"monthly_take_home_etb": 5200' in extraction
    assert '"status": "not_met"' in extraction
    assert '"summary_en"' in extraction


def test_the_extraction_request_carries_no_instructions_of_its_own() -> None:
    """Keeping instructions in the system turn is what makes the prompt cacheable."""

    request = prompts.build_extraction_request("Worker: Yes.")
    assert request == "Transcript:\n\nWorker: Yes."
