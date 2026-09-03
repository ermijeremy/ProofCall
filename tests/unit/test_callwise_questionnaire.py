from types import SimpleNamespace

from app.intelligence.questions import build_interview_prompt, fixed_question_set


def test_callwise_questionnaire_is_fixed_and_ordered() -> None:
    questions = fixed_question_set()

    assert len(questions) == 16
    assert questions[0]["slug"] == "age_years"
    assert questions[-1]["slug"] == "other_changes"
    assert [question["index"] for question in questions] == list(range(16))
    assert len({question["slug"] for question in questions}) == 16


def test_prompt_places_consent_before_age_and_uses_exact_closing() -> None:
    prompt = build_interview_prompt(
        SimpleNamespace(worker_id="w1", preferred_language="am"),
        fixed_question_set(),
        language="am",
        programme_name="beSingularity pilot",
    )

    assert prompt.index("SPOKEN CONSENT") < prompt.index("FIRST SUBSTANTIVE QUESTION")
    assert "May I begin?" in prompt
    assert "without your name" in prompt
    assert "Do not ask any extra question after the closing." in prompt
    assert prompt.index("SPOKEN CONSENT") < prompt.index("FIRST SUBSTANTIVE QUESTION")
    assert "Do not ask age, employment, salary, training, or any other interview question" in prompt


def test_prompt_makes_language_check_the_first_turn_and_limits_spoken_languages() -> None:
    prompt = build_interview_prompt(
        SimpleNamespace(worker_id="w1", preferred_language="am"),
        fixed_question_set(),
        language="am",
    )

    assert prompt.index("LANGUAGE CHECK — THIS MUST BE THE FIRST SPOKEN TURN") < prompt.index("WHO YOU ARE")
    assert "ሰላም። አማርኛ ወይስ English?" in prompt
    assert "You may speak only Amharic or English." in prompt
    assert "wait silently for up to three seconds" in prompt
    assert "ይህን ጥያቄ መመለስ" in prompt
