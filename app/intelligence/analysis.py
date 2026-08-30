"""The counts behind a finished round. Code counts; the model narrates.

Every number the administrator reads is computed here, in Python, from stored
answers, and handed to the router already calculated. The model is never asked to
tally twenty records, because that is exactly where invented numbers come from —
and a monitoring figure nobody can reproduce is worse than no figure at all.

There is deliberately no model call in this module. The router
(:mod:`app.intelligence.agent`) is shown the summary in its context block and
answers questions about it with the ``answer`` tool, so the counts reach the
administrator through one model call rather than two, and there is only one place
where the words "quote these and add nothing" are said.
"""

from __future__ import annotations

from typing import Any

#: Quotes handed over per question. Enough to ground an answer, few enough that
#: the model is not tempted to start counting them.
MAX_QUOTES_PER_QUESTION = 8


def summarize(
    questions: list[dict[str, Any]],
    records: list[dict[str, Any]],
    categories: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Count one batch.

    ``records`` are ``{"worker_id", "name", "answers", "excluded",
    "exclusion_reason", "consent"}``. Excluded records — a respondent reported as
    a child, or one who did not consent — are counted nowhere except in the
    ``excluded`` figures, which is the whole point of excluding them.
    """

    counted = [record for record in records if not record.get("excluded")]
    excluded = [record for record in records if record.get("excluded")]
    sets = categories or {}

    per_question: dict[str, Any] = {}
    for question in questions:
        slug = question["slug"]
        counts: dict[str, int] = {}
        states: dict[str, int] = {}
        quotes: list[dict[str, Any]] = []
        for record in counted:
            entry = record["answers"].get(slug) or {}
            state = entry.get("state") or "NOT_ASKED"
            states[state] = states.get(state, 0) + 1
            category = entry.get("category") or sets.get(slug, {}).get("assignments", {}).get(record["worker_id"])
            if category:
                counts[category] = counts.get(category, 0) + 1
            if entry.get("evidence") and len(quotes) < MAX_QUOTES_PER_QUESTION:
                quotes.append(
                    {
                        "name": record.get("name") or record["worker_id"],
                        "category": category,
                        "said": entry["evidence"],
                    }
                )
        per_question[slug] = {
            "question": question["text"],
            "category_counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
            "state_counts": states,
            "answered": states.get("STATED", 0),
            "quotes": quotes,
        }

    return {
        "interviews_counted": len(counted),
        "records_total": len(records),
        "excluded_total": len(excluded),
        "excluded": [
            {
                "name": record.get("name") or record["worker_id"],
                "reason": record.get("exclusion_reason"),
            }
            for record in excluded
        ],
        "consented": sum(1 for record in counted if record.get("consent")),
        "questions": per_question,
    }


def headline(summary: dict[str, Any]) -> str:
    """A one-line, model-free summary. Safe to show before any chat happens."""

    counted = summary["interviews_counted"]
    excluded = summary["excluded_total"]
    tail = f", {excluded} excluded and not counted" if excluded else ""
    return f"{counted} interview(s) counted{tail}."


__all__ = [
    "MAX_QUOTES_PER_QUESTION",
    "headline",
    "summarize",
]
