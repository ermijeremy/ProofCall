"""The full chain Member B asked for, end to end and offline.

    fixture transcript
      -> CallProofEngine (RecordedProvider, no network, no API key)
      -> CompletedCallResult
      -> store_completed_result  (Member B persistence)
      -> aggregate_company / aggregate_programme  (Member B aggregation)

Nothing here reaches Gemini. The recorded provider replays each fixture's
``expected_extraction``, so these tests exercise the wiring and the rule engine
rather than the model, and they produce the same numbers on every machine.

Two tests in the last section document a wiring defect rather than asserting the
behaviour we want. They are marked in their docstrings and named for the defect.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.contracts.integration import CompletedCallResult, WorkerEvidenceResult
from app.db.base import Base
from app.intelligence.engine import CallProofEngine
from app.intelligence.providers.fixture import RecordedProvider
from app.models.beneficiary import Beneficiary
from app.models.call import TeleExpertCall
from app.models.employer import Employer
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.evidence import EvidenceRepository
from app.services.aggregation_service import (
    aggregate_company,
    aggregate_programme,
    worker_evidence_detail,
)
from app.services.completion_service import process_completed_call

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"
FIXTURES: dict[str, dict[str, Any]] = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(FIXTURE_DIR.glob("*.json"))
}
#: Variants share a worker with their parent, so only primaries are seeded.
PRIMARY = sorted(name for name in FIXTURES if "variant_of" not in FIXTURES[name])
VARIANTS = sorted(name for name in FIXTURES if "variant_of" in FIXTURES[name])

COMPANY_ID = "ABC"
#: The employer's own report. The gap against it is the product's headline.
REPORTED_GOOD_JOBS = 18


@pytest.fixture(name="engine")
def engine_fixture() -> CallProofEngine:
    return CallProofEngine(RecordedProvider.from_fixture_dir(FIXTURE_DIR))


@pytest.fixture(name="db")
def db_fixture() -> Iterator[Session]:
    """A fresh in-memory database per test.

    ``StaticPool`` keeps every session on one connection, or an in-memory
    SQLite database would vanish between checkouts.
    """

    sql_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=sql_engine)
    session = sessionmaker(bind=sql_engine, autoflush=False, autocommit=False)()
    try:
        _seed(session)
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=sql_engine)


def _seed(db: Session) -> None:
    """One company, one beneficiary and one queued call per primary fixture."""

    db.add(
        Employer(
            company_id=COMPANY_ID,
            name="ABC Construction",
            reported_good_jobs=REPORTED_GOOD_JOBS,
            average_salary=8500,
            worker_count=20,
        )
    )
    for name in PRIMARY:
        fixture = FIXTURES[name]
        db.add(
            Beneficiary(
                worker_id=fixture["worker_id"],
                company_id=COMPANY_ID,
                phone_number=f"+2519000{fixture['worker_id'][-3:]}",
                preferred_language=fixture["language"],
                employer_claims=dict(fixture["employer_claims"]),
            )
        )
        db.add(
            TeleExpertCall(
                call_id=fixture["call_id"],
                worker_id=fixture["worker_id"],
                phone_number=f"+2519000{fixture['worker_id'][-3:]}",
                prompt="(recorded fixture call)",
                status="queued",
            )
        )
    db.commit()


def _process(db: Session, engine: CallProofEngine, name: str) -> CompletedCallResult:
    fixture = FIXTURES[name]
    return process_completed_call(
        db,
        call_id=fixture["call_id"],
        transcript=fixture["transcript"],
        transcript_turns=fixture["transcript_turns"],
        language=fixture["language"],
        audio_url=f"https://example.invalid/{fixture['call_id']}.mp3",
        engine=engine,
    )


def _process_all(db: Session, engine: CallProofEngine) -> dict[str, CompletedCallResult]:
    return {name: _process(db, engine, name) for name in PRIMARY}


# --------------------------------------------------------------------------- #
# One call through the whole chain
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", PRIMARY)
def test_a_transcript_produces_the_expected_verdict(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    fixture = FIXTURES[name]
    expected = WorkerEvidenceResult.model_validate(fixture["expected_result"])
    result = _process(db, engine, name)

    assert result.overall_verdict == expected.overall_verdict
    assert result.clauses == expected.clauses
    assert result.consent == expected.consent
    assert result.safeguarding_flag == expected.safeguarding_flag


@pytest.mark.parametrize("name", PRIMARY)
def test_the_identifiers_come_from_the_call_not_the_model(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    """A model must never be able to reattribute an interview to another worker."""

    fixture = FIXTURES[name]
    result = _process(db, engine, name)
    assert result.worker_id == fixture["worker_id"]
    assert result.call_id == fixture["call_id"]


@pytest.mark.parametrize("name", PRIMARY)
def test_the_transcript_survives_persistence_unaltered(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    """The transcript is the audit record; a reviewer must find the quotes in it."""

    fixture = FIXTURES[name]
    _process(db, engine, name)
    detail = worker_evidence_detail(db, fixture["worker_id"])
    assert detail["transcript"] == fixture["transcript"]
    for clause in detail["clauses"].values():
        if clause["evidence"]:
            for part in clause["evidence"].split(" ... "):
                assert part in detail["transcript"]


@pytest.mark.parametrize("name", PRIMARY)
def test_speaker_turns_and_language_are_preserved(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    """Member B asked for both shapes to survive; TeleExpert may return either."""

    fixture = FIXTURES[name]
    result = _process(db, engine, name)
    assert result.transcript_turns == fixture["transcript_turns"]
    assert result.language == fixture["language"]

    call = db.get(TeleExpertCall, fixture["call_id"])
    assert call is not None
    assert call.status == "completed"
    assert call.transcript_turns == fixture["transcript_turns"]
    assert call.language == fixture["language"]
    assert call.completed_at is not None


@pytest.mark.parametrize("name", PRIMARY)
def test_evidence_is_persisted_as_json_the_dashboard_can_read(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    fixture = FIXTURES[name]
    result = _process(db, engine, name)

    stored = EvidenceRepository(db).get(fixture["worker_id"])
    assert stored is not None
    assert stored.call_id == fixture["call_id"]
    assert stored.overall_verdict == result.overall_verdict
    assert stored.safeguarding_flag == result.safeguarding_flag
    assert set(stored.clauses) == set(result.clauses)
    # The aggregation reads contradictions as plain dicts, so the keys matter.
    for item in stored.contradictions:
        assert {"type", "description", "material", "worker_value", "employer_value"} <= set(item)


def test_reprocessing_the_same_call_is_idempotent(db: Session, engine: CallProofEngine) -> None:
    """A retried webhook must not double-count a worker in the aggregate."""

    first = _process(db, engine, "normal_worker")
    second = _process(db, engine, "normal_worker")
    assert first == second
    assert len(EvidenceRepository(db).list()) == 1


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def test_company_aggregation_matches_the_verdicts(db: Session, engine: CallProofEngine) -> None:
    results = _process_all(db, engine)
    summary = aggregate_company(db, COMPANY_ID)

    verdicts = [result.overall_verdict for result in results.values()]
    assert summary["worker_confirmed"] == verdicts.count("CONFIRMED_GOOD_JOB") == 2
    assert summary["not_confirmed"] == verdicts.count("NOT_CONFIRMED") == 1
    assert summary["unclear"] == verdicts.count("UNCLEAR") == 2
    assert summary["stopped"] == verdicts.count("STOPPED") == 1
    assert summary["completed_interviews"] == len(PRIMARY)


def test_the_verification_gap_is_the_employer_claim_minus_confirmations(
    db: Session, engine: CallProofEngine
) -> None:
    """The number the whole product exists to produce."""

    _process_all(db, engine)
    summary = aggregate_company(db, COMPANY_ID)
    assert summary["employer_claimed"] == REPORTED_GOOD_JOBS
    assert summary["verification_gap"] == REPORTED_GOOD_JOBS - summary["worker_confirmed"]


def test_a_stopped_interview_is_reported_but_never_counted_as_a_good_job(
    db: Session, engine: CallProofEngine
) -> None:
    """The safeguarding case: excluded from confirmations, surfaced separately."""

    result = _process(db, engine, "under_15_stopped")
    assert result.overall_verdict == "STOPPED"
    assert result.safeguarding_flag is True

    summary = aggregate_company(db, COMPANY_ID)
    assert summary["worker_confirmed"] == 0
    assert summary["not_confirmed"] == 0
    assert summary["stopped"] == 1
    assert summary["safeguarding_flags"] == 1


def test_a_refusal_never_shows_the_employer_figure_as_the_worker_answer(
    db: Session, engine: CallProofEngine
) -> None:
    """What the dashboard renders for W018 must be blank, not 8500."""

    _process(db, engine, "salary_refusal")
    detail = worker_evidence_detail(db, "W018")
    salary = detail["clauses"]["salary"]
    assert salary["status"] == "REFUSED"
    assert salary["value"] is None
    assert detail["contradictions"] == []
    assert detail["overall_verdict"] == "UNCLEAR"


def test_programme_aggregation_counts_the_same_workers(
    db: Session, engine: CallProofEngine
) -> None:
    _process_all(db, engine)
    company = aggregate_company(db, COMPANY_ID)
    programme = aggregate_programme(db)
    for key in ("worker_confirmed", "not_confirmed", "unclear", "stopped", "safeguarding_flags"):
        assert programme[key] == company[key]
    assert programme["beneficiaries"] == len(PRIMARY)
    assert programme["companies"] == 1


def test_an_unprocessed_worker_counts_as_nothing(db: Session, engine: CallProofEngine) -> None:
    """A call that never completed must not quietly become a confirmation."""

    summary = aggregate_company(db, COMPANY_ID)
    assert summary["worker_confirmed"] == 0
    assert summary["completed_interviews"] == 0
    assert summary["verification_gap"] == REPORTED_GOOD_JOBS


# --------------------------------------------------------------------------- #
# Language independence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", VARIANTS)
def test_a_translated_transcript_reaches_the_same_verdict(
    db: Session, engine: CallProofEngine, name: str
) -> None:
    """The rule engine reads facts, not English.

    The Amharic variant carries the same facts as its parent and must produce a
    byte-identical result, including the clause statuses and the verdict. Only
    the quoted evidence differs, because the quotes are in Amharic.
    """

    fixture = FIXTURES[name]
    parent = FIXTURES[fixture["variant_of"]]

    variant = engine.compare_with_employer(
        engine.evaluate_clauses(
            dict(fixture["expected_extraction"], transcript=fixture["transcript"])
        ),
        fixture["employer_claims"],
    )
    expected = WorkerEvidenceResult.model_validate(parent["expected_result"])

    assert variant.overall_verdict == expected.overall_verdict
    assert {name: clause.status for name, clause in variant.clauses.items()} == {
        name: clause.status for name, clause in expected.clauses.items()
    }
    assert {name: clause.value for name, clause in variant.clauses.items()} == {
        name: clause.value for name, clause in expected.clauses.items()
    }
    # Evidence is quoted in the language spoken, so it must differ.
    assert variant.clauses["age"].evidence != expected.clauses["age"].evidence


# --------------------------------------------------------------------------- #
# Provider failure
# --------------------------------------------------------------------------- #


def test_an_unknown_transcript_cannot_produce_a_confirmation(db: Session) -> None:
    """A provider failure must look like missing evidence, never like a good job."""

    empty_engine = CallProofEngine(RecordedProvider())
    db.add(
        TeleExpertCall(
            call_id="fixture-W099",
            worker_id="W001",
            phone_number="+251900001",
            prompt="(unrecorded)",
            status="queued",
        )
    )
    db.commit()

    result = process_completed_call(
        db, call_id="fixture-W099", transcript="Worker: hello?", engine=empty_engine
    )
    assert result.overall_verdict == "UNCLEAR"
    assert result.consent is False
    assert all(clause.value is None for clause in result.clauses.values())


# --------------------------------------------------------------------------- #
# Worker-specific employer-claim comparison
# --------------------------------------------------------------------------- #


def test_employment_status_contradiction_is_detected_from_beneficiary_claim(
    db: Session, engine: CallProofEngine
) -> None:
    """Worker-specific employment and salary claims are compared correctly."""

    result = _process(db, engine, "employment_status_contradiction")
    types = [item.type for item in result.contradictions]
    assert types == ["employment_status", "salary"]


def test_passing_the_beneficiary_finds_the_employment_status_contradiction(
    db: Session, engine: CallProofEngine
) -> None:
    """The one-line fix, verified. No rules change is needed.

    ``rules.employer_claims`` already accepts a dict, a ``Beneficiary``, or an
    ``Employer``, so Member B can pass whichever object suits the service.
    """

    fixture = FIXTURES["employment_status_contradiction"]
    beneficiary = BeneficiaryRepository(db).get(fixture["worker_id"])
    assert beneficiary is not None

    extracted = engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
    evaluated = engine.compare_with_employer(engine.evaluate_clauses(extracted), beneficiary)

    expected = WorkerEvidenceResult.model_validate(fixture["expected_result"])
    assert evaluated.contradictions == expected.contradictions
    assert [item.type for item in evaluated.contradictions] == ["employment_status", "salary"]


def test_dashboard_counts_worker_material_contradictions(
    db: Session, engine: CallProofEngine
) -> None:
    """Aggregation counts workers carrying at least one material contradiction."""

    _process_all(db, engine)
    assert aggregate_company(db, COMPANY_ID)["material_contradictions"] == 2

    fixture = FIXTURES["employment_status_contradiction"]
    beneficiary = BeneficiaryRepository(db).get(fixture["worker_id"])
    corrected = engine.compare_with_employer(
        engine.evaluate_clauses(
            engine.extract_evidence(fixture["transcript"], fixture["worker_id"])
        ),
        beneficiary,
    )
    assert any(item.material for item in corrected.contradictions)
