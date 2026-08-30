"""The one place in the batch path where a label produces a fixed action.

Everything else about an answer is categorized by the model. Age is too — but
what happens to a record labelled ``CHILD`` is decided here, in code, and is the
same every time: flagged, excluded, and counted nowhere.
"""

from __future__ import annotations

from typing import Any

from app.intelligence import safeguarding


def extraction(**overrides: Any) -> dict[str, Any]:
    base = {
        "worker_id": "w1",
        "consent": True,
        "age_assessment": "ADULT",
        "interview_stopped": False,
        "stop_reason": None,
        "answers": {},
    }
    base.update(overrides)
    return base


def test_a_child_label_excludes_and_flags():
    result = safeguarding.apply(extraction(age_assessment="CHILD"))
    assert result["excluded"] is True
    assert result["safeguarding_flag"] is True
    assert result["exclusion_reason"] == safeguarding.CHILD_EXCLUSION_REASON


def test_a_stopped_interview_excludes_even_when_the_label_is_missing():
    """The voice agent is told to stop the call for age without saying why, so the
    stop reason may be the only signal that reaches us."""

    result = safeguarding.apply(extraction(age_assessment="UNKNOWN", stop_reason="UNDER_MINIMUM_AGE"))
    assert result["excluded"] is True
    assert safeguarding.is_child(result) is True


def test_age_outranks_consent():
    result = safeguarding.apply(extraction(age_assessment="CHILD", consent=False))
    assert result["exclusion_reason"] == safeguarding.CHILD_EXCLUSION_REASON


def test_no_consent_excludes_without_a_safeguarding_flag():
    result = safeguarding.apply(extraction(consent=False))
    assert result["excluded"] is True
    assert result["safeguarding_flag"] is False
    assert result["exclusion_reason"] == safeguarding.NO_CONSENT_REASON
    assert safeguarding.is_child(result) is False


def test_an_unknown_age_with_consent_is_counted():
    result = safeguarding.apply(extraction(age_assessment="UNKNOWN"))
    assert result["excluded"] is False
    assert result["exclusion_reason"] is None


def test_the_reason_is_recorded_for_the_admin_and_never_addressed_to_the_respondent():
    assert "not counted" in safeguarding.CHILD_EXCLUSION_REASON
    assert "you" not in safeguarding.CHILD_EXCLUSION_REASON.lower()
