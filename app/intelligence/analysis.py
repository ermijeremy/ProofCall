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

import json

from app.intelligence.providers.base import ProviderError

#: Quotes handed over per question. Enough to ground an answer, few enough that
#: the model is not tempted to start counting them.
MAX_QUOTES_PER_QUESTION = 8
HISTORY_TURNS = 8


def build_analysis_prompt() -> str:
    return """You answer an administrator's question about a completed Callwise batch.
You are given deterministic counts, not raw worker records. You do not compute.
Never add, subtract, average, or estimate numbers. Excluded interviews are excluded on purpose and must never be included in category counts. Answer only from the
provided counts and say when the counts do not contain the requested information.
Return JSON only: {\"answer\": \"short factual answer\"}."""


def build_analysis_request(
    question: str,
    summary: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> str:
    safe_history = (history or [])[-HISTORY_TURNS:]
    return (
        "Counts for this batch:\n\n"
        + json.dumps(summary, ensure_ascii=False)
        + "\n\nAdministrator question: "
        + question
        + "\n\nRecent conversation:\n"
        + json.dumps(safe_history, ensure_ascii=False)
    )


def answer(provider: Any, question: str, summary: dict[str, Any], history: list[dict[str, str]] | None = None) -> str:
    """Narrate stored aggregate counts without allowing the model to calculate."""

    try:
        payload = provider.complete_json(
            system=build_analysis_prompt(),
            user=build_analysis_request(question, summary, history),
        )
    except ProviderError:
        return "The analysis service could not be reached right now."
    text = payload.get("answer") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return "The analysis service returned nothing usable."
    return text.strip()


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
        "disposition_counts": {
            disposition: sum(1 for record in records if record.get("disposition") == disposition)
            for disposition in sorted({record.get("disposition") for record in records if record.get("disposition")})
        },
        "partial_total": sum(1 for record in counted if record.get("disposition") == "partial"),
        "safeguarding_total": sum(1 for record in excluded if "under" in str(record.get("exclusion_reason", "")).lower()),
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
    "answer",
    "build_analysis_prompt",
    "build_analysis_request",
    "headline",
    "summarize",
]
