"""The chat over a finished batch. Code counts; the model narrates.

Every number the admin reads is computed here, in Python, from stored answers,
and handed to the model already calculated. The model is never asked to tally
twenty records, because that is exactly where invented numbers come from — and a
monitoring figure nobody can reproduce is worse than no figure at all.

This is the same split that makes the clause path defensible (the model extracts,
code decides), applied to a conversation instead of a verdict.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.intelligence.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

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


def build_analysis_prompt() -> str:
    """System instructions for answering one admin question about a batch."""

    return """You answer an administrator's questions about a set of telephone interviews
that have already been analysed. You are given the counts. You do not compute
new ones.

RULES
Use only the figures in the data you were given. Never add, subtract, average,
or convert numbers into percentages that are not already there. If the answer
would need a number that is not present, say which number is missing instead of
working it out.
Never invent a person, an answer, or a quote. Quotes may only be repeated from
the ones supplied, and in the language they were said in.
Excluded interviews are excluded on purpose. Do not count them, and do not fold
them back into a total. If asked about them, report the reason recorded.
Do not judge the employer, score the results, or recommend an action unless the
administrator asked for an interpretation, and say plainly when something is a
reading of the data rather than a count of it.
Answer in a few sentences of plain English. Quote figures exactly as given. No
preamble.
"""


def build_analysis_request(
    admin_question: str,
    summary: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> str:
    """The user turn: the counts, the recent thread, and the question."""

    parts = [f"Counts for this batch:\n\n{json.dumps(summary, ensure_ascii=False, indent=2)}"]
    if history:
        transcript = "\n".join(f"{turn['role']}: {turn['text']}" for turn in history[-6:])
        parts.append(f"Earlier in this conversation:\n\n{transcript}")
    parts.append(f"Administrator's question:\n\n{admin_question}")
    return "\n\n".join(parts)


def answer(
    provider: LLMProvider,
    admin_question: str,
    summary: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> str:
    """Answer one question about the batch, or say why it could not be answered."""

    try:
        payload = provider.complete_json(
            system=build_analysis_prompt() + '\nReturn JSON only: {"answer": "..."}',
            user=build_analysis_request(admin_question, summary, history),
        )
    except ProviderError as exc:
        logger.exception("Analysis call failed")
        return f"The analysis model could not be reached, so I have not answered that. ({exc})"
    text = payload.get("answer")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "The analysis model returned nothing usable for that question."


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
