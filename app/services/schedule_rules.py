"""Validating a time the model resolved. No model calls, no database.

The model reads "tomorrow at 3" and returns a local wall-clock time and a zone.
Everything after that is arithmetic and policy, so it lives here: a time that is
timezone-aware, in the future, and inside working hours, echoed back in full so
the administrator can see exactly what was agreed before twenty telephones ring.

The split matters because the two halves fail differently. The model
misunderstanding "3" is a conversation; code storing a naive timestamp is a class
of bug nobody notices until the calls go out at the wrong hour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Local hours during which it is acceptable to telephone somebody at work.
#: Outside this the model has to have the administrator confirm, because 3am is
#: almost always a misread hour rather than an instruction.
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 20

#: Retries for a call that does not connect, matching
#: :class:`~app.models.interview.InterviewSchedule`. Said out loud whenever the
#: administrator did not choose it, so a default is never silent.
DEFAULT_RETRIES = 2

#: More than this and a single unanswered number occupies the queue all day.
MAX_RETRIES = 5

_ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H",
)


def clean_retries(value: Any) -> tuple[int, bool]:
    """Return ``(retries, defaulted)``. Anything unusable becomes the default."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_RETRIES, True
    number = int(value)
    if number < 0 or number > MAX_RETRIES:
        return DEFAULT_RETRIES, True
    return number, False


def load_zone(name: str) -> ZoneInfo | None:
    """The zone, or ``None`` if it is not a real IANA name."""

    if not name or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def parse_local(when_iso: str) -> datetime | None:
    """Read a naive local wall-clock time out of the model's string.

    An offset in the string is discarded rather than honoured. The model has no
    clock, so any offset it produced is a guess, and the zone it named separately
    is the thing that was actually agreed.
    """

    text = (when_iso or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    # Strip a trailing +03:00 / -0500 without letting a date's own dashes go.
    for index in range(len(text) - 1, 9, -1):
        if text[index] in "+-":
            text = text[:index]
            break
    text = text.strip()
    for fmt in _ISO_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def now_in(zone_name: str) -> datetime | None:
    """The current local time in ``zone_name``, or ``None`` for an unknown zone."""

    zone = load_zone(zone_name)
    return datetime.now(zone) if zone is not None else None


def describe_now(zone_name: str) -> str:
    """The current time as the model is shown it, or an honest admission."""

    local = now_in(zone_name)
    if local is None:
        return (
            f"{datetime.now(UTC):%A %-d %B %Y, %H:%M} UTC "
            "(the company's timezone is not known, so this is UTC)"
        )
    return f"{local:%A %-d %B %Y, %H:%M} ({zone_name})"


def describe(moment: datetime, zone_name: str, retries: int, *, defaulted: bool) -> str:
    """The agreed time in words, absolute and unambiguous.

    Deliberately spells out the weekday, the date, and the zone. "Tomorrow at 3"
    read back as "tomorrow at 3" confirms nothing; read back as "Sunday 31 August
    2026, 3:00 PM (Africa/Addis_Ababa)" it is checkable.
    """

    zone = load_zone(zone_name)
    local = moment.astimezone(zone) if zone is not None else moment
    attempts = f"{retries} retry attempt{'' if retries == 1 else 's'}"
    return (
        f"{local:%A %-d %B %Y, %-I:%M %p} ({zone_name or 'UTC'}), "
        f"{attempts}{' (default)' if defaulted else ''}"
    )


def validate(
    when_iso: str,
    zone_name: str,
    *,
    now: datetime | None = None,
    allow_outside_window: bool = False,
) -> dict[str, Any]:
    """Turn a local time and a zone into a UTC instant, or say why not.

    Returns ``{"ok", "at_utc", "local", "error"}``. ``now`` is injectable so the
    tests do not depend on the wall clock; production passes nothing.
    """

    result: dict[str, Any] = {"ok": False, "at_utc": None, "local": None, "error": None}

    zone = load_zone(zone_name)
    if zone is None:
        result["error"] = "unknown_timezone"
        return result

    naive = parse_local(when_iso)
    if naive is None:
        result["error"] = "unparseable"
        return result

    local = naive.replace(tzinfo=zone)
    result["local"] = local
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    if local <= reference:
        result["error"] = "past"
        return result

    if not (WINDOW_START_HOUR <= local.hour < WINDOW_END_HOUR) and not allow_outside_window:
        result["error"] = "outside_window"
        return result

    result["ok"] = True
    result["at_utc"] = local.astimezone(UTC)
    return result


def window_text() -> str:
    return f"{WINDOW_START_HOUR:02d}:00 and {WINDOW_END_HOUR:02d}:00"


__all__ = [
    "DEFAULT_RETRIES",
    "MAX_RETRIES",
    "WINDOW_END_HOUR",
    "WINDOW_START_HOUR",
    "clean_retries",
    "describe",
    "describe_now",
    "load_zone",
    "now_in",
    "parse_local",
    "validate",
    "window_text",
]
