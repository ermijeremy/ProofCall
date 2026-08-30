"""Beneficiary sampling for verification campaigns.

The draw itself lives here, separated from the database query that produces the
candidates, because a batch selects from a roster the admin described in words
("half of them", "the ones we have not reached yet") rather than from a whole
company.

``SystemRandom`` is deliberate and unseeded: a verification sample must not be
predictable from outside. Reproducibility comes from persisting the drawn list,
not from replaying the draw.
"""

from secrets import SystemRandom
from typing import TypeVar

random_source = SystemRandom()

ItemT = TypeVar("ItemT")


def sample_from(candidates: list[ItemT], sample_size: int) -> list[ItemT]:
    """Draw ``sample_size`` candidates uniformly, or all of them when it is 0.

    Raises ``ValueError`` when more are requested than exist, rather than
    quietly returning a short sample: a caller asking for twenty interviews and
    silently getting eleven would report a coverage it never had.
    """

    requested = len(candidates) if sample_size == 0 else sample_size
    if requested > len(candidates):
        raise ValueError(
            f"Requested sample of {requested}, but only {len(candidates)} active beneficiaries exist"
        )
    if requested < 0:
        raise ValueError(f"Requested sample of {requested}, which is not a size")
    return random_source.sample(candidates, requested)
