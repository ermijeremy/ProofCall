"""Runtime registration point for Member A's intelligence implementation."""

from app.intelligence.interface import IntelligenceEngine

_engine: IntelligenceEngine | None = None


def configure_engine(engine: IntelligenceEngine) -> None:
    global _engine
    _engine = engine


def get_engine() -> IntelligenceEngine:
    if _engine is None:
        raise RuntimeError("Member A intelligence engine has not been configured")
    return _engine

