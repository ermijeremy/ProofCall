"""Shared pytest configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run AnyIO tests only on asyncio; Trio is not a project dependency."""

    return "asyncio"
