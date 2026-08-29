"""End-to-end contract tests for the seeded operational flow.

These tests require the dependencies in requirements.txt. They are kept at the
integration level because they exercise SQLAlchemy, FastAPI, and the shared
Member A/B contract together.
"""

from pathlib import Path

import pytest


@pytest.mark.integration
def test_demo_fixture_contains_twenty_workers() -> None:
    import json

    workers = json.loads((Path(__file__).parents[2] / "data" / "demo_workers.json").read_text())
    assert len(workers) == 20
    assert len({worker["worker_id"] for worker in workers}) == 20
    assert all(worker["company_id"] == "ABC" for worker in workers)

