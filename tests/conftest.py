"""Shared pytest configuration and offline fixtures."""

from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base
from app.intelligence.batch_engine import BatchIntelligence
from tests.fakes import FakeBatchProvider


@pytest.fixture
def anyio_backend() -> str:
    """Run AnyIO tests only on asyncio; Trio is not a project dependency."""

    return "asyncio"


@pytest.fixture()
def engine() -> Engine:
    """One in-memory database per test.

    ``StaticPool`` keeps every connection on the same in-memory database, which
    matters because the webhook path opens its own session rather than using the
    request's.
    """

    instance = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(instance)
    return instance


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db(session_factory: sessionmaker) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture()
def provider() -> FakeBatchProvider:
    return FakeBatchProvider()


@pytest.fixture()
def intelligence(provider: FakeBatchProvider) -> BatchIntelligence:
    return BatchIntelligence(provider)
