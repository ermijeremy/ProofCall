"""Every number is dialed in E.164, because anything else is not dialed at all.

TeleExpert rejects a number without a leading ``+``. The failure is quiet in the
worst way: the call comes back failed, and the thread reports somebody who could
not be reached rather than a number the API never accepted. These tests pin the
forms a spreadsheet of Ethiopian staff actually contains.
"""

from __future__ import annotations

import pytest

from app.core.phone import is_dialable, to_e164


@pytest.mark.parametrize(
    ("written", "dialed"),
    [
        # The reported failure: a full international number with no plus.
        ("251933325080", "+251933325080"),
        # A national number with the trunk prefix, which is not a country code.
        ("0933325080", "+251933325080"),
        # A bare subscriber number.
        ("933325080", "+251933325080"),
        # Already right, in the several ways a person types it.
        ("+251933325080", "+251933325080"),
        ("+251 933 325 080", "+251933325080"),
        ("+251-933-325-080", "+251933325080"),
        # The international access prefix becomes the plus.
        ("00251933325080", "+251933325080"),
        (" 251933325080 ", "+251933325080"),
    ],
)
def test_every_way_a_local_number_is_written_dials_the_same(written: str, dialed: str) -> None:
    assert to_e164(written) == dialed


def test_a_foreign_number_keeps_its_own_country_code() -> None:
    """Eleven digits or more already carry a country code, so none is added."""

    assert to_e164("447911123456") == "+447911123456"
    assert to_e164("+44 7911 123456") == "+447911123456"


def test_the_country_code_can_be_overridden_for_another_programme() -> None:
    assert to_e164("0712345678", country_code="254") == "+254712345678"
    assert to_e164("712345678", country_code="+254") == "+254712345678"


@pytest.mark.parametrize("nothing", ["", "   ", "n/a", "-", None])
def test_a_row_with_no_number_produces_no_number(nothing: str | None) -> None:
    """An empty result is what the import reports as a skipped row."""

    assert to_e164(nothing) == ""


def test_length_is_the_only_thing_dialability_claims() -> None:
    assert is_dialable("+251933325080")
    assert not is_dialable("+25193")
    assert not is_dialable("+2519333250801234567")
