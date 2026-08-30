"""The question set an admin types, and what the model is asked to do with it.

Two invariants matter here and both are enforced below. Our own age question is
always asked first, because a child must not be interviewed and the interview
cannot stop early if age arrives late. And neither prompt may contain a number
that could read as a qualifying level: the same rule the clause path is held to
in ``tests/unit/test_prompts.py``, applied to prompts built from typed questions.
"""

from __future__ import annotations

import re

import pytest

from app.intelligence import questions as questions_module

ADMIN_QUESTIONS = [
    "Do you get enough compensation?",
    "How many hours do you work?",
]


def test_slugify_makes_a_field_name_from_a_typed_question():
    assert questions_module.slugify("Do you get enough compensation?") == "do_you_get_enough_compensation"
    assert questions_module.slugify("  Hours / week  ") == "hours_week"


def test_slugify_falls_back_when_nothing_survives():
    assert questions_module.slugify("???", fallback="question_2") == "question_2"


@pytest.mark.parametrize(
    "text",
    ["What is your age?", "how old are you", "ዕድሜዎ ስንት ነው?", "Umri wako?"],
)
def test_age_questions_are_recognized_in_several_languages(text: str):
    assert questions_module.is_age_question(text) is True


def test_parse_questions_strips_numbering_and_bullets():
    parsed = questions_module.parse_questions("1 - what is ur age?\n2. Do u get enough compensation?\n- Hours?")
    assert parsed == ["what is ur age?", "Do u get enough compensation?", "Hours?"]


def test_parse_questions_splits_one_line_holding_several_numbered_questions():
    parsed = questions_module.parse_questions("1 - what is ur age? 2 - Do u get enough compensation?")
    assert parsed == ["what is ur age?", "Do u get enough compensation?"]


def test_age_is_always_question_one():
    question_set = questions_module.build_question_set(ADMIN_QUESTIONS)
    assert question_set[0]["slug"] == questions_module.AGE_SLUG
    assert question_set[0]["index"] == 0
    assert [item["slug"] for item in question_set[1:]] == [
        "do_you_get_enough_compensation",
        "how_many_hours_do_you_work",
    ]


def test_an_admin_age_question_does_not_become_a_second_age_question():
    question_set = questions_module.build_question_set(["what is ur age?", "Do u get enough compensation?"])
    slugs = [item["slug"] for item in question_set]
    assert slugs.count(questions_module.AGE_SLUG) == 1
    assert slugs == [questions_module.AGE_SLUG, "do_u_get_enough_compensation"]


def test_interview_prompt_asks_consent_first_and_stops_silently_for_a_child():
    prompt = questions_module.build_interview_prompt(
        {"worker_id": "w1", "preferred_language": None},
        questions_module.build_question_set(ADMIN_QUESTIONS),
    )
    consent = prompt.index("voluntary")
    assert consent < prompt.index("FIRST QUESTION, ALWAYS")
    assert "do not ask a single further" in prompt
    assert "do not mention any age rule" in prompt


def test_interview_prompt_contains_no_number_that_could_read_as_a_threshold():
    # The worker id is echoed into the prompt as an interview reference, so this
    # one deliberately has no digits: the assertion is about our own numbers.
    prompt = questions_module.build_interview_prompt(
        {"worker_id": "abebe"},
        questions_module.build_question_set(ADMIN_QUESTIONS + ["How many days per week?"]),
    )
    assert re.findall(r"\d+", prompt) == []


def test_worker_language_beats_the_batch_default():
    prompt = questions_module.build_interview_prompt(
        {"worker_id": "w1", "preferred_language": "sw"},
        questions_module.build_question_set(ADMIN_QUESTIONS),
        language="am",
    )
    assert "Swahili" in prompt
    assert "Amharic" not in prompt


def test_batch_language_is_used_when_the_worker_has_none():
    prompt = questions_module.build_interview_prompt(
        {"worker_id": "w1", "preferred_language": ""},
        questions_module.build_question_set(ADMIN_QUESTIONS),
        language="ti",
    )
    assert "Tigrinya" in prompt


def test_extraction_prompt_names_every_question_and_asks_only_for_an_age_label():
    question_set = questions_module.build_question_set(ADMIN_QUESTIONS)
    prompt = questions_module.build_extraction_prompt(question_set)
    for question in question_set:
        assert f'"{question["slug"]}"' in prompt
    assert '"CHILD" | "ADULT" | "UNKNOWN"' in prompt
    assert "MET" not in prompt
    assert "verdict" in prompt  # only ever as a prohibition
    assert "Do not output a verdict" in prompt


def test_normalize_extraction_fills_every_question_even_when_the_model_skips_some():
    question_set = questions_module.build_question_set(ADMIN_QUESTIONS)
    extraction = questions_module.normalize_extraction(
        {
            "consent": True,
            "language": "am",
            "age_assessment": "ADULT",
            "answers": {"age_years": {"value": "32", "state": "STATED", "evidence": "32"}},
        },
        worker_id="w1",
        transcript="agent: ...",
        questions=question_set,
    )
    assert set(extraction["answers"]) == {item["slug"] for item in question_set}
    assert extraction["answers"]["do_you_get_enough_compensation"]["state"] == "VAGUE"
    assert extraction["answers"]["age_years"]["value"] == "32"


def test_normalize_extraction_drops_fields_the_model_invented():
    question_set = questions_module.build_question_set(["Hours?"])
    extraction = questions_module.normalize_extraction(
        {"answers": {"salary_amount": {"value": "5000", "state": "STATED"}}, "worker_id": "somebody_else"},
        worker_id="w1",
        transcript="",
        questions=question_set,
    )
    assert "salary_amount" not in extraction["answers"]
    assert extraction["worker_id"] == "w1"


def test_normalize_extraction_rejects_an_age_label_outside_the_closed_set():
    extraction = questions_module.normalize_extraction(
        {"age_assessment": "PROBABLY_FINE"},
        worker_id="w1",
        transcript="",
        questions=questions_module.build_question_set([]),
    )
    assert extraction["age_assessment"] == "UNKNOWN"


def test_a_stopped_interview_marks_the_rest_not_asked_rather_than_vague():
    question_set = questions_module.build_question_set(ADMIN_QUESTIONS)
    extraction = questions_module.normalize_extraction(
        {"interview_stopped": True, "stop_reason": "UNDER_MINIMUM_AGE", "answers": {}},
        worker_id="w1",
        transcript="",
        questions=question_set,
    )
    states = {entry["state"] for entry in extraction["answers"].values()}
    assert states == {"NOT_ASKED"}
