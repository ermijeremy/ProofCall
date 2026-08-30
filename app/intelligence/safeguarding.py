"""Deterministic action on a model-supplied age label.

The product decision is that the model categorizes everything, including age, so
no threshold lives in code. That decision governs the *label*. It does not govern
what happens next: a respondent the model reports as a child is flagged,
excluded, and absent from every count, and that part is code, unconditionally.

The split matters because the alternative is a model deciding, per call, whether
a child's interview counts. A label can be wrong and be corrected. An action
taken inconsistently cannot be audited at all.
"""

from __future__ import annotations

from typing import Any

CHILD = "CHILD"

#: Reason recorded on an excluded record. Stored, shown to the admin, and never
#: shown to the respondent.
CHILD_EXCLUSION_REASON = "Respondent reported as under the lawful working age; interview stopped and not counted."
NO_CONSENT_REASON = "Respondent did not consent to the interview; not counted."


def exclusion_for(extraction: dict[str, Any]) -> str | None:
    """The reason this record must not be counted, or ``None``.

    Age first: it is the only exclusion that also means the call should have
    ended early, and it outranks consent because a child's consent is not the
    question.
    """

    if extraction.get("age_assessment") == CHILD:
        return CHILD_EXCLUSION_REASON
    if extraction.get("stop_reason") == "UNDER_MINIMUM_AGE":
        return CHILD_EXCLUSION_REASON
    if not extraction.get("consent", False):
        return NO_CONSENT_REASON
    return None


def apply(extraction: dict[str, Any]) -> dict[str, Any]:
    """Set ``excluded`` and ``exclusion_reason`` on one extraction, in place."""

    reason = exclusion_for(extraction)
    extraction["excluded"] = reason is not None
    extraction["exclusion_reason"] = reason
    extraction["safeguarding_flag"] = reason == CHILD_EXCLUSION_REASON
    return extraction


def is_child(extraction: dict[str, Any]) -> bool:
    """True when this record is excluded for age rather than for consent."""

    return exclusion_for(extraction) == CHILD_EXCLUSION_REASON


__all__ = [
    "CHILD",
    "CHILD_EXCLUSION_REASON",
    "NO_CONSENT_REASON",
    "apply",
    "exclusion_for",
    "is_child",
]
