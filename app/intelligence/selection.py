"""Who gets called: the model parses the instruction, code draws the sample.

The admin types "call all of them", "call half of them at random", "call Abebe
and Marta", or "call the ones we haven't reached yet". Reading that is a language
problem and the model is good at it. Choosing ten of twenty is not: a model asked
to list names produces a sample that is neither uniform nor auditable, and can
name somebody who is not on the roster at all.

So the model returns a *specification* and never a list of people. Code resolves
the specification against the real roster, draws the sample, and reports anything
it could not resolve instead of quietly calling fewer people than the admin asked
for. Every call is a real phone ringing, so a wrong sample cannot be taken back.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

from app.intelligence.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

MODES = ("all", "count", "fraction", "explicit", "remaining", "unclear")

Sampler = Callable[[list[dict[str, Any]], int], list[dict[str, Any]]]


def build_selection_prompt() -> str:
    """System instructions for reading one selection instruction."""

    return """You read one instruction from an administrator about which people to
telephone, and you describe what they asked for. You do not choose the people.

Return JSON only, with exactly this shape:

{
  "mode": "all" | "count" | "fraction" | "explicit" | "remaining" | "unclear",
  "count": a whole number, or null,
  "fraction": a number between 0 and 1, or null,
  "names": ["..."] or null,
  "exclude_already_called": true or false,
  "confidence": "HIGH" | "MEDIUM" | "LOW"
}

MODES
  "all"        everybody on the list.
  "count"      a specific number of them, chosen at random. Put it in "count".
  "fraction"   a proportion of them, chosen at random. Put it in "fraction",
               as a decimal: half is 0.5, and 20 percent is 0.2.
  "explicit"   named people only. Put the names in "names", spelled as the
               administrator spelled them.
  "remaining"  everybody who has not been called yet.
  "unclear"    you cannot tell what they asked for. Use this rather than guessing.

RULES
Never return names for a random selection; that is not your decision to make.
Set "exclude_already_called" to true when they asked for people not yet reached,
or said "the rest", "the others", or "who is left".
Use "unclear" and "LOW" confidence whenever the instruction could reasonably mean
two different groups of people. A wrong guess places real telephone calls that
cannot be taken back.
"""


def normalize_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a model response into a spec, refusing anything unusable.

    A malformed spec becomes ``unclear`` rather than a default of "everybody":
    defaulting to everybody would turn a misread instruction into the largest
    possible number of phone calls.
    """

    mode = payload.get("mode")
    mode = mode if mode in MODES else "unclear"

    count = payload.get("count")
    count = int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0 else None

    fraction = payload.get("fraction")
    if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and 0 < float(fraction) <= 1:
        fraction = float(fraction)
    else:
        fraction = None

    names = payload.get("names")
    names = [name.strip() for name in names if isinstance(name, str) and name.strip()] if isinstance(names, list) else []

    if mode == "count" and count is None:
        mode = "unclear"
    if mode == "fraction" and fraction is None:
        mode = "unclear"
    if mode == "explicit" and not names:
        mode = "unclear"

    confidence = payload.get("confidence")
    return {
        "mode": mode,
        "count": count,
        "fraction": fraction,
        "names": names,
        "exclude_already_called": bool(payload.get("exclude_already_called", False)) or mode == "remaining",
        "confidence": confidence if confidence in {"HIGH", "MEDIUM", "LOW"} else "LOW",
    }


def parse_selection(provider: LLMProvider, instruction: str) -> dict[str, Any]:
    """Ask the provider what the admin asked for. Returns a normalized spec."""

    try:
        payload = provider.complete_json(
            system=build_selection_prompt(),
            user=f"Instruction:\n\n{instruction}",
        )
    except ProviderError:
        logger.exception("Selection parsing failed")
        return normalize_spec({"mode": "unclear", "confidence": "LOW"})
    return normalize_spec(payload)


def _match(name: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = name.casefold().strip()
    exact = [item for item in candidates if (item.get("name") or "").casefold().strip() == wanted]
    if exact:
        return exact
    partial = [item for item in candidates if wanted and wanted in (item.get("name") or "").casefold()]
    if partial:
        return partial
    return [item for item in candidates if (item.get("worker_id") or "").casefold() == wanted]


def resolve(
    spec: dict[str, Any],
    roster: list[dict[str, Any]],
    *,
    already_called: set[str] | None = None,
    sampler: Sampler | None = None,
) -> dict[str, Any]:
    """Turn a spec into the people to call, plus everything it could not resolve.

    ``roster`` items are ``{"worker_id", "name", ...}``. Returns
    ``{"selected", "unmatched", "ambiguous", "skipped_already_called", "error"}``.
    A returned ``error`` means nothing was selected and the admin must be asked
    again — never that a smaller sample should be called instead.
    """

    # Imported here, not at module scope: the intelligence layer does not depend
    # on the service layer, and the sampler is injectable for tests anyway.
    from app.services.sampling_service import sample_from

    draw = sampler or sample_from
    reached = already_called or set()
    candidates = [person for person in roster if not person.get("excluded")]
    skipped: list[str] = []
    if spec.get("exclude_already_called"):
        skipped = [person["worker_id"] for person in candidates if person["worker_id"] in reached]
        candidates = [person for person in candidates if person["worker_id"] not in reached]

    result: dict[str, Any] = {
        "selected": [],
        "unmatched": [],
        "ambiguous": {},
        "skipped_already_called": skipped,
        "error": None,
        "mode": spec.get("mode"),
    }

    if spec.get("mode") == "unclear":
        result["error"] = "unclear"
        return result

    if not candidates:
        result["error"] = "no_candidates"
        return result

    if spec["mode"] == "explicit":
        chosen: dict[str, dict[str, Any]] = {}
        for name in spec["names"]:
            matches = _match(name, candidates)
            if not matches:
                result["unmatched"].append(name)
            elif len(matches) > 1:
                # Two people called Abebe is a question for the admin, not a coin
                # flip: calling the wrong one cannot be undone. Both the id and
                # the name go back, because an admin reading the thread can pick
                # between "Abebe Kebede, +251911000001" and its neighbour but not
                # between two opaque worker ids.
                result["ambiguous"][name] = [
                    {
                        "worker_id": person["worker_id"],
                        "name": person.get("name") or person["worker_id"],
                        "phone_number": person.get("phone_number"),
                    }
                    for person in matches
                ]
            else:
                chosen[matches[0]["worker_id"]] = matches[0]
        result["selected"] = list(chosen.values())
        if not result["selected"]:
            # Ambiguity is not absence. Reporting "nobody matches" when two people
            # matched sends the admin looking for a missing row that is there
            # twice, so the two cases get separate errors.
            result["error"] = "ambiguous" if result["ambiguous"] else "nothing_matched"
        return result

    if spec["mode"] in {"all", "remaining"}:
        result["selected"] = list(candidates)
        return result

    if spec["mode"] == "fraction":
        # Round up, and never to zero: "half of twenty-one" should not silently
        # drop the odd person, and "10% of four" is still somebody.
        exact = len(candidates) * spec["fraction"]
        size = min(len(candidates), max(1, math.ceil(round(exact, 6))))
    else:
        size = spec["count"]

    if size > len(candidates):
        result["error"] = "too_many_requested"
        result["requested"] = size
        result["available"] = len(candidates)
        return result

    result["selected"] = draw(candidates, size)
    return result


__all__ = [
    "MODES",
    "build_selection_prompt",
    "normalize_spec",
    "parse_selection",
    "resolve",
]
