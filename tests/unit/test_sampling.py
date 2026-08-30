"""The shared sampler, and the wrapper the campaign path still calls.

``sample_workers`` was refactored to delegate so the batch path and the campaign
path draw samples the same way. Its signature and its errors are unchanged, which
is what these tests pin.
"""

from __future__ import annotations

import pytest

from app.services.campaign_service import sample_workers
from app.services.sampling_service import random_source, sample_from


def test_a_size_of_zero_means_everyone():
    candidates = list(range(5))
    assert sorted(sample_from(candidates, 0)) == candidates


def test_a_sample_is_drawn_without_replacement():
    drawn = sample_from(list(range(20)), 10)
    assert len(drawn) == 10
    assert len(set(drawn)) == 10


def test_over_requesting_raises_with_both_numbers():
    with pytest.raises(ValueError) as error:
        sample_from([1, 2], 5)
    assert "5" in str(error.value)
    assert "2" in str(error.value)


def test_a_negative_size_raises():
    with pytest.raises(ValueError):
        sample_from([1, 2], -1)


def test_the_sampler_is_the_system_random_source():
    """``SystemRandom`` cannot be seeded, which is why an auditable selection is
    persisted rather than reproduced."""

    assert not hasattr(random_source, "seed") or random_source.seed(1) is None
    assert random_source.__class__.__name__ == "SystemRandom"


def test_campaign_service_still_exposes_the_old_signature():
    assert sample_workers.__code__.co_varnames[:3] == ("db", "company_id", "sample_size")
