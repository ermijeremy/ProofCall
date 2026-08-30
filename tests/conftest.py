"""Shared pytest configuration and offline fixtures."""

from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import settings
from app.db.base import Base
from app.intelligence.batch_engine import BatchIntelligence
from tests.fakes import FakeBatchProvider, FakeToolProvider


@pytest.fixture(autouse=True)
def offline_telephony(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force demo-mode dialing for every test, whatever the developer's ``.env``.

    ``app.core.config`` calls ``load_dotenv()`` at import, so a real
    ``TELEXPERT_BASE_URL`` in a developer's ``.env`` reaches the test process and
    ``submit_teleexpert_call`` stops short-circuiting to a ``demo_call_`` id. Three
    integration tests then dialed a live LAN service and failed on its 400 — a
    result that had nothing to do with the code under test.

    Blanked here rather than in each test, because the failure mode is a test
    suite that behaves differently on two machines, and any test added later
    inherits the same hazard.
    """

    monkeypatch.setattr(settings, "teleexpert_base_url", "", raising=False)
    monkeypatch.setattr(settings, "teleexpert_api_key", "", raising=False)
    monkeypatch.setattr(settings, "teleexpert_webhook_url", "", raising=False)


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
def provider() -> FakeToolProvider:
    """The default fake: it answers the JSON calls *and* the router.

    Tool-calling by default because every administrator message goes through the
    router now, so a provider that cannot choose an action cannot drive the thread
    at all. ``json_provider`` is the narrower one, for the tests that check what
    happens when a provider can only extract.
    """

    return FakeToolProvider()


@pytest.fixture()
def json_provider() -> FakeBatchProvider:
    return FakeBatchProvider()


@pytest.fixture()
def intelligence(provider: FakeToolProvider) -> BatchIntelligence:
    return BatchIntelligence(provider)
