"""Pass two: derive one category set per question, then assign everyone into it.

Admin-typed questions have no pass condition, so an answer cannot be scored. It
can still be *grouped*, and a group is countable, which is what a monitoring
sheet needs. "How old are you?" becomes bands; "Do you get enough
compensation?" becomes something like yes / no / mixed.

Two passes rather than one, deliberately. If each answer were labelled on its
own, twenty calls would produce twenty near-synonyms and nothing would add up.
Deriving the set across the whole batch first, then assigning into it, keeps the
buckets few, stable, and countable.

Refusals and unasked questions never reach the model: code puts them in reserved
buckets. Asking a model to categorize a refusal invites it to guess what the
person would have said.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.intelligence.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

#: Categories code assigns without asking, from the answer state alone.
REFUSED_CATEGORY = "Declined to answer"
NOT_ASKED_CATEGORY = "Not asked"
UNCATEGORIZED = "Uncategorized"
RESERVED_CATEGORIES = (REFUSED_CATEGORY, NOT_ASKED_CATEGORY, UNCATEGORIZED)

#: Upper bound on derived categories per question. Small on purpose: a bucket
#: holding one person out of twenty is a quote, not a category.
MAX_CATEGORIES = 6


def build_categorization_prompt(questions: list[dict[str, Any]]) -> str:
    """System instructions for deriving and assigning categories for one batch."""

    listed = "\n".join(f'  - "{question["slug"]}": {question["text"]}' for question in questions)
    return f"""You group answers from a set of telephone interviews so they can be counted.
You are not judging, scoring, or ranking anybody.

For each question you are given every respondent's answer. Do two things:
  1. Decide a small set of categories that describes how the answers to that
     question actually differ. At most {MAX_CATEGORIES} per question, ideally
     fewer. Name each one in plain English, in two or three words.
  2. Put every respondent into exactly one of the categories you just named for
     that question.

Return JSON only, with exactly this shape:

{{
  "<question name>": {{
    "categories": ["...", "..."],
    "assignments": {{"<worker id>": "one of the categories above"}}
  }}
}}

The questions, by name:
{listed}

RULES
Derive the categories from the answers you were given, not from what you expect
such answers to be. If everyone answered the same way, one category is correct.
Categories must describe what was said. Do not name a category "good",
"acceptable", "compliant", "sufficient", "at risk", or anything else that
implies a verdict, and do not order them from best to worst.
Every assignment must be one of the strings you listed in "categories" for that
same question, copied exactly.
Assign every worker id you were given for a question, and no others.
When the answers are numbers or amounts, group them into plain ranges drawn from
the answers themselves, so a category holds several people. One category per
distinct figure counts nothing. Age is the same: use bands.
Never invent an answer for somebody who did not give one.
"""


def build_categorization_request(
    questions: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> str:
    """The user turn: every answer that is actually categorizable, per question.

    ``records`` are ``{"worker_id": str, "answers": {slug: entry}}``. Only
    ``STATED`` and ``VAGUE`` answers are sent; the rest are handled by code.
    """

    payload: dict[str, dict[str, Any]] = {}
    for question in questions:
        slug = question["slug"]
        answers = {}
        for record in records:
            entry = record["answers"].get(slug) or {}
            if entry.get("state") not in {"STATED", "VAGUE"}:
                continue
            answers[record["worker_id"]] = {
                "answer": entry.get("value"),
                "said": entry.get("evidence"),
            }
        if answers:
            payload[slug] = {"question": question["text"], "answers": answers}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _reserved_for(state: Any) -> str | None:
    if state == "REFUSED":
        return REFUSED_CATEGORY
    if state == "NOT_ASKED":
        return NOT_ASKED_CATEGORY
    return None


def categorize_batch(
    provider: LLMProvider,
    questions: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Derive and assign categories for one batch in a single provider call.

    Returns ``{slug: {"categories": [...], "assignments": {worker_id: category}}}``.
    Every record gets an assignment for every question: from the model when the
    answer was categorizable, from the reserved set when it was not, and
    ``Uncategorized`` when the model named a category it had not declared.
    """

    request = build_categorization_request(questions, records)
    raw: dict[str, Any] = {}
    if any(record["answers"] for record in records):
        try:
            raw = provider.complete_json(
                system=build_categorization_prompt(questions),
                user=request,
            )
        except ProviderError:
            # Losing the buckets must not lose the answers. Everything falls into
            # the reserved set, which still counts refusals correctly, and the
            # batch can be re-categorized later.
            logger.exception("Categorization failed; falling back to reserved categories only")
            raw = {}

    result: dict[str, dict[str, Any]] = {}
    for question in questions:
        slug = question["slug"]
        section = raw.get(slug) if isinstance(raw.get(slug), dict) else {}
        declared = [
            name for name in (section.get("categories") or [])
            if isinstance(name, str) and name.strip()
        ][:MAX_CATEGORIES]
        proposed = section.get("assignments") if isinstance(section.get("assignments"), dict) else {}

        assignments: dict[str, str] = {}
        for record in records:
            worker_id = record["worker_id"]
            entry = record["answers"].get(slug) or {}
            reserved = _reserved_for(entry.get("state"))
            if reserved is not None:
                assignments[worker_id] = reserved
                continue
            label = proposed.get(worker_id)
            if isinstance(label, str) and label in declared:
                assignments[worker_id] = label
            else:
                if isinstance(label, str) and label:
                    logger.warning(
                        "Rejected category %r for %s on %s: not in the derived set",
                        label, worker_id, slug,
                    )
                assignments[worker_id] = UNCATEGORIZED

        used = [name for name in declared if name in set(assignments.values())]
        used += [name for name in RESERVED_CATEGORIES if name in set(assignments.values())]
        result[slug] = {"categories": used, "assignments": assignments}
    return result


def apply_categories(
    records: list[dict[str, Any]],
    categories: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write each assigned category back onto its answer entry, in place."""

    for record in records:
        for slug, section in categories.items():
            entry = record["answers"].get(slug)
            if isinstance(entry, dict):
                entry["category"] = section["assignments"].get(record["worker_id"])
    return records


__all__ = [
    "MAX_CATEGORIES",
    "NOT_ASKED_CATEGORY",
    "REFUSED_CATEGORY",
    "RESERVED_CATEGORIES",
    "UNCATEGORIZED",
    "apply_categories",
    "build_categorization_prompt",
    "build_categorization_request",
    "categorize_batch",
]
