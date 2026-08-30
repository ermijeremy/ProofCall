"""One phone number, in the only form the telephony API accepts.

TeleExpert rejects a number without a leading ``+``: ``251933325080`` comes back
as a failed call, so the administrator sees a person who could not be reached
rather than a number that was never dialable in the first place. Every number is
therefore put into E.164 here, once, immediately before it goes out — and the same
function is used at import, so the list on screen shows exactly what will be
dialed.

The default country code exists because a spreadsheet of Ethiopian staff is
usually written ``0933325080`` or ``933325080``, and a trunk prefix is not a
country code. It is a setting rather than a constant so a programme in another
country can change it without touching this file.
"""

from __future__ import annotations

import re

from app.core.config import settings

_NOT_DIGITS = re.compile(r"[^0-9]")

#: Shortest and longest an E.164 subscriber number can be, minus the ``+``. Used
#: only to leave obvious rubbish alone rather than to hand the API a number it
#: will certainly reject.
MIN_DIGITS = 7
MAX_DIGITS = 15


def to_e164(raw: str, *, country_code: str | None = None) -> str:
    """The number as ``+<country><subscriber>``, or ``""`` if there is no number.

    The cases, in the order they are decided:

    * written with a ``+`` already — kept, with the punctuation removed;
    * written with the international prefix ``00`` — that becomes the ``+``;
    * written with a national trunk ``0`` — replaced by the country code;
    * already starting with the country code — given the missing ``+``;
    * eleven digits or more — assumed to carry its own country code, so it only
      gains the ``+``. This is what keeps a foreign number in the list from being
      relabelled as a local one;
    * anything shorter — a bare subscriber number, so the country code is added.

    Ethiopian mobile numbers start ``9`` or ``7`` and never ``251``, so the
    country-code test above cannot misread a local number. A programme whose
    numbers do begin with their own country code should set the trunk form in the
    file instead.
    """

    text = (raw or "").strip()
    digits = _NOT_DIGITS.sub("", text)
    if not digits:
        return ""

    code = (country_code or settings.default_country_code or "").strip().lstrip("+")
    if text.startswith("+"):
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith("0"):
        return f"+{code}{digits[1:]}" if code else f"+{digits[1:]}"
    if code and digits.startswith(code):
        return f"+{digits}"
    if len(digits) >= 11 or not code:
        return f"+{digits}"
    return f"+{code}{digits}"


def is_dialable(number: str) -> bool:
    """Whether E.164 would accept this at all. Length only; nothing about routing."""

    digits = _NOT_DIGITS.sub("", number or "")
    return MIN_DIGITS <= len(digits) <= MAX_DIGITS


__all__ = ["MAX_DIGITS", "MIN_DIGITS", "is_dialable", "to_e164"]
