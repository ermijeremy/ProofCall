"""Who gets called: the model names a shape, code draws the sample.

The router (:mod:`app.intelligence.agent`) reads the administrator's sentence and
calls ``select_people`` with a shape — everyone, a number, a share, specific
worker ids, or whoever is left. This module turns that shape into actual people.

The division is the point. A model asked to pick ten of twenty produces a sample
that is neither uniform nor auditable, and can name somebody who is not on the
list at all. So a random draw is always made here, in Python, against the real
roster; the model is only ever allowed to say *how many*. Named people are the
one exception, and even then the model passes worker ids copied from the roster
it was shown rather than names it typed out, so there is nothing left to match.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

logger = logging.getLogger(__name__)

MODES = ("all", "count", "fraction", "explicit", "remaining", "unclear")

Sampler = Callable[[list[dict[str, Any]], int], list[dict[str, Any]]]


def normalize_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce ``select_people`` arguments into a spec, refusing anything unusable.

    A malformed spec becomes ``unclear`` rather than a default of "everybody":
    defaulting to everybody would turn a misread instruction into the largest
    possible number of telephone calls.
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

    worker_ids = payload.get("worker_ids")
    worker_ids = (
        [str(item).strip() for item in worker_ids if str(item).strip()]
        if isinstance(worker_ids, (list, tuple))
        else []
    )

    if mode == "count" and count is None:
        mode = "unclear"
    if mode == "fraction" and fraction is None:
        mode = "unclear"
    if mode == "explicit" and not worker_ids:
        mode = "unclear"

    return {
        "mode": mode,
        "count": count,
        "fraction": fraction,
        "worker_ids": worker_ids,
        "exclude_already_called": bool(payload.get("exclude_already_called", False)) or mode == "remaining",
    }


def resolve(
    spec: dict[str, Any],
    roster: list[dict[str, Any]],
    *,
    already_called: set[str] | None = None,
    sampler: Sampler | None = None,
) -> dict[str, Any]:
    """Turn a spec into the people to call, plus everything it could not resolve.

    ``roster`` items are ``{"worker_id", "name", ...}``. Returns
    ``{"selected", "unknown_ids", "skipped_already_called", "error"}``. A returned
    ``error`` means nothing was selected and the administrator must be asked
    again — never that a smaller sample should be called instead.
    """

    # Imported here, not at module scope: the intelligence layer does not depend
    # on the service layer, and the sampler is injectable for tests anyway.
    from app.services.sampling_service import sample_callwise

    draw = sampler or sample_callwise
    reached = already_called or set()
    candidates = [person for person in roster if not person.get("excluded")]
    skipped: list[str] = []
    if spec.get("exclude_already_called"):
        skipped = [person["worker_id"] for person in candidates if person["worker_id"] in reached]
        candidates = [person for person in candidates if person["worker_id"] not in reached]

    result: dict[str, Any] = {
        "selected": [],
        "unknown_ids": [],
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
        by_id = {person["worker_id"]: person for person in candidates}
        chosen: dict[str, dict[str, Any]] = {}
        for worker_id in spec["worker_ids"]:
            person = by_id.get(worker_id)
            if person is None:
                # Either the model invented an id or it named somebody already
                # called or excluded. Reported rather than dropped: calling four
                # people when five were named is a failure that looks like success.
                result["unknown_ids"].append(worker_id)
            else:
                chosen[worker_id] = person
        result["selected"] = list(chosen.values())
        if not result["selected"]:
            result["error"] = "nothing_matched"
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


__all__ = ["MODES", "normalize_spec", "resolve"]
