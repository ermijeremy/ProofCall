"""The deterministic rule engine, driven by the transcript fixtures.

Every fixture asserts an exact round trip: the recorded extraction, fed through
:func:`evaluate_clauses` and :func:`compare_with_employer`, must reproduce the
fixture's ``expected_result`` field for field. That is the strongest statement
available about the rules — it pins statuses, values, confidences, quotes,
contradictions, materiality, and the verdict all at once.

The remaining tests cover behaviour no fixture exercises: malformed extractions,
mixed-state derived clauses, wiring the wrong object into the employer
comparison, and the boundary values on each threshold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts.integration import ClauseEvidence, WorkerEvidenceResult
from app.intelligence.criteria import DECENT_WORK_CRITERIA
from app.intelligence.rules import (
    EVIDENCE_JOIN,
    compare_with_employer,
    derive_verdict,
    employer_claims,
    evaluate_clauses,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"
FIXTURES: dict[str, dict[str, Any]] = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(FIXTURE_DIR.glob("*.json"))
}
ALL_CASES = sorted(FIXTURES)


def _run(fixture: dict[str, Any]) -> WorkerEvidenceResult:
    """Extraction payload in, final result out, exactly as the engine chains it."""

    evidence = dict(fixture["expected_extraction"])
    evidence["transcript"] = fixture["transcript"]
    result = evaluate_clauses(evidence)
    return compare_with_employer(result, fixture["employer_claims"])


def _clause(**kwargs: Any) -> ClauseEvidence:
    return ClauseEvidence(**kwargs)


def _facts(**overrides: Any) -> dict[str, Any]:
    """A fully-answered, qualifying interview, with named facts replaced."""

    base = {
        "age_years": {"value": 30, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "currently_employed": {"value": True, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "employment_duration_months": {"value": 12, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "working_days_per_week": {"value": 5, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "working_hours_per_day": {"value": 8, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "salary_amount": {"value": 8500, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "training_participation": {"value": True, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "forced_labour_present": {"value": False, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "discrimination_present": {"value": False, "state": "STATED", "confidence": "HIGH", "evidence": None},
        "freedom_of_association_restricted": {"value": False, "state": "STATED", "confidence": "HIGH", "evidence": None},
    }
    base.update(overrides)
    return {"worker_id": "T001", "consent": True, "interview_stopped": False, "facts": base}


# --------------------------------------------------------------------------- #
# Fixture round trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_engine_reproduces_the_fixture_exactly(case: str) -> None:
    fixture = FIXTURES[case]
    expected = WorkerEvidenceResult.model_validate(fixture["expected_result"])
    assert _run(fixture) == expected


@pytest.mark.parametrize("case", ALL_CASES)
def test_engine_is_deterministic(case: str) -> None:
    fixture = FIXTURES[case]
    assert _run(fixture) == _run(fixture)


@pytest.mark.parametrize("case", ALL_CASES)
def test_clause_evidence_stays_grounded_in_the_transcript(case: str) -> None:
    fixture = FIXTURES[case]
    for name, clause in _run(fixture).clauses.items():
        if clause.evidence is None:
            continue
        for part in clause.evidence.split(EVIDENCE_JOIN):
            assert part in fixture["transcript"], f"{case}: {name} quotes {part!r}"


def test_material_contradiction_count_matches_the_specification() -> None:
    """Three of the twenty demo workers must carry a material contradiction.

    Member B's aggregation counts *workers* with at least one material
    contradiction. Two of the three come from fixtures here; the third is W016
    in the seed data. If this drops to two, the dashboard headline stops
    matching the specification.
    """

    with_material = {
        FIXTURES[case]["worker_id"]
        for case in ALL_CASES
        if any(item.material for item in _run(FIXTURES[case]).contradictions)
    }
    assert with_material == {"W002", "W015"}


# --------------------------------------------------------------------------- #
# Thresholds at the boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("age", "expected_status", "expected_verdict"),
    [(14, "STOPPED", "STOPPED"), (15, "MET", "CONFIRMED_GOOD_JOB"), (16, "MET", "CONFIRMED_GOOD_JOB")],
)
def test_age_boundary(age: int, expected_status: str, expected_verdict: str) -> None:
    assert DECENT_WORK_CRITERIA["minimum_age"] == 15
    result = evaluate_clauses(_facts(age_years={"value": age, "state": "STATED", "confidence": "HIGH"}))
    assert result.clauses["age"].status == expected_status
    assert result.overall_verdict == expected_verdict
    # The stop is a safeguarding outcome, not a failed condition.
    assert result.safeguarding_flag is (expected_status == "STOPPED")


@pytest.mark.parametrize(
    ("months", "expected_status"), [(5, "NOT_MET"), (6, "MET"), (7, "MET")]
)
def test_duration_boundary(months: int, expected_status: str) -> None:
    assert DECENT_WORK_CRITERIA["minimum_duration_months"] == 6
    result = evaluate_clauses(
        _facts(employment_duration_months={"value": months, "state": "STATED", "confidence": "HIGH"})
    )
    assert result.clauses["employment_duration"].status == expected_status


@pytest.mark.parametrize(
    ("days", "hours", "weekly", "expected_status"),
    [(2, 9, 18, "NOT_MET"), (4, 5, 20, "MET"), (5, 8, 40, "MET")],
)
def test_weekly_hours_boundary(days: int, hours: int, weekly: int, expected_status: str) -> None:
    assert DECENT_WORK_CRITERIA["minimum_weekly_hours"] == 20
    result = evaluate_clauses(
        _facts(
            working_days_per_week={"value": days, "state": "STATED", "confidence": "HIGH"},
            working_hours_per_day={"value": hours, "state": "STATED", "confidence": "HIGH"},
        )
    )
    clause = result.clauses["working_hours"]
    assert clause.value == weekly
    assert clause.status == expected_status


def test_salary_has_no_floor() -> None:
    """Any stated amount establishes pay. Smallness is not a disqualification."""

    assert DECENT_WORK_CRITERIA["salary_floor"] is None
    result = evaluate_clauses(_facts(salary_amount={"value": 1, "state": "STATED", "confidence": "HIGH"}))
    assert result.clauses["salary"].status == "MET"
    assert result.overall_verdict == "CONFIRMED_GOOD_JOB"


# --------------------------------------------------------------------------- #
# Unresolved facts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [("REFUSED", "REFUSED"), ("VAGUE", "UNCLEAR"), ("NOT_ASKED", "NOT_ASKED")],
)
def test_unresolved_states_never_become_not_met(state: str, expected_status: str) -> None:
    """Missing evidence is not a failed condition. It blocks confirmation only."""

    result = evaluate_clauses(
        _facts(salary_amount={"value": None, "state": state, "confidence": "HIGH", "evidence": None})
    )
    clause = result.clauses["salary"]
    assert clause.status == expected_status
    assert clause.value is None
    assert result.overall_verdict == "UNCLEAR"


def test_refusal_keeps_high_confidence_but_vagueness_does_not() -> None:
    refused = evaluate_clauses(
        _facts(salary_amount={"value": None, "state": "REFUSED", "confidence": "HIGH"})
    )
    assert refused.clauses["salary"].confidence == "HIGH"

    vague = evaluate_clauses(
        _facts(employment_duration_months={"value": None, "state": "VAGUE", "confidence": "HIGH"})
    )
    # We are not confident about a value we do not have, whatever the model said.
    assert vague.clauses["employment_duration"].confidence == "LOW"


def test_a_stated_value_is_ignored_when_the_state_says_otherwise() -> None:
    """The state is authoritative. A leftover value must not be used."""

    result = evaluate_clauses(
        _facts(salary_amount={"value": 8500, "state": "REFUSED", "confidence": "HIGH"})
    )
    assert result.clauses["salary"].value is None
    assert result.clauses["salary"].status == "REFUSED"


@pytest.mark.parametrize(
    ("days_state", "hours_state", "expected_status"),
    [
        ("STATED", "REFUSED", "REFUSED"),
        ("REFUSED", "NOT_ASKED", "REFUSED"),
        ("REFUSED", "VAGUE", "REFUSED"),
        ("VAGUE", "NOT_ASKED", "UNCLEAR"),
        ("STATED", "NOT_ASKED", "UNCLEAR"),
        ("NOT_ASKED", "NOT_ASKED", "NOT_ASKED"),
    ],
)
def test_derived_hours_reports_the_most_informative_failure(
    days_state: str, hours_state: str, expected_status: str
) -> None:
    """Weekly hours need both halves. The status names the better-known reason.

    A refusal is the most informative outcome and outranks the rest. NOT_ASKED
    survives only when neither half was put to the respondent: once one half was
    answered the clause *was* asked, so a half-answered pair is UNCLEAR — the
    question was covered and no weekly figure came out of it.
    """

    def half(state: str, value: int) -> dict[str, Any]:
        # A state of STATED must carry a real value, or the fact is malformed
        # rather than answered — a different case, covered separately below.
        return {"value": value if state == "STATED" else None, "state": state, "confidence": "HIGH"}

    result = evaluate_clauses(
        _facts(
            working_days_per_week=half(days_state, 6),
            working_hours_per_day=half(hours_state, 8),
        )
    )
    assert result.clauses["working_hours"].status == expected_status


def test_a_malformed_half_makes_derived_hours_unclear_not_unasked() -> None:
    """A fact claiming STATED with no number is malformed, not unanswered.

    Reporting NOT_ASKED here would assert something false — that neither
    question was put — and would hide the broken fact behind ordinary silence.
    """

    result = evaluate_clauses(
        _facts(
            working_days_per_week={"value": None, "state": "STATED", "confidence": "HIGH"},
            working_hours_per_day={"value": None, "state": "NOT_ASKED", "confidence": "HIGH"},
        )
    )
    assert result.clauses["working_hours"].status == "UNCLEAR"


def test_derived_hours_take_the_weaker_confidence() -> None:
    result = evaluate_clauses(
        _facts(
            working_days_per_week={"value": 6, "state": "STATED", "confidence": "HIGH"},
            working_hours_per_day={"value": 8, "state": "STATED", "confidence": "MEDIUM"},
        )
    )
    assert result.clauses["working_hours"].confidence == "MEDIUM"


def test_two_different_quotes_are_joined_and_stay_verbatim() -> None:
    transcript = "Worker: I work six days.\nWorker: Eight hours each day."
    result = evaluate_clauses(
        _facts(
            working_days_per_week={"value": 6, "state": "STATED", "confidence": "HIGH", "evidence": "I work six days."},
            working_hours_per_day={"value": 8, "state": "STATED", "confidence": "HIGH", "evidence": "Eight hours each day."},
        )
        | {"transcript": transcript}
    )
    evidence = result.clauses["working_hours"].evidence
    assert evidence == f"I work six days.{EVIDENCE_JOIN}Eight hours each day."
    assert all(part in transcript for part in evidence.split(EVIDENCE_JOIN))


# --------------------------------------------------------------------------- #
# Malformed extractions must fail safe
# --------------------------------------------------------------------------- #


def test_missing_facts_become_unclear_not_confirmed() -> None:
    result = evaluate_clauses({"worker_id": "T001", "consent": True, "facts": {}})
    assert result.overall_verdict == "UNCLEAR"
    assert all(clause.value is None for clause in result.clauses.values())


def test_an_empty_extraction_is_unclear() -> None:
    result = evaluate_clauses({})
    assert result.overall_verdict == "UNCLEAR"
    assert result.consent is False
    assert result.worker_id == ""


def test_an_unrecognised_state_is_treated_as_unknown() -> None:
    result = evaluate_clauses(
        _facts(salary_amount={"value": 8500, "state": "PROBABLY", "confidence": "HIGH"})
    )
    assert result.clauses["salary"].status == "UNCLEAR"
    assert result.overall_verdict == "UNCLEAR"


def test_a_boolean_where_a_number_belongs_is_rejected() -> None:
    """``isinstance(True, int)`` is True in Python, so this needs a real guard."""

    result = evaluate_clauses(
        _facts(age_years={"value": True, "state": "STATED", "confidence": "HIGH"})
    )
    assert result.clauses["age"].value is None
    assert result.clauses["age"].status == "UNCLEAR"


def test_a_number_where_a_boolean_belongs_is_rejected() -> None:
    result = evaluate_clauses(
        _facts(currently_employed={"value": 1, "state": "STATED", "confidence": "HIGH"})
    )
    assert result.clauses["employment_status"].value is None
    assert result.clauses["employment_status"].status == "UNCLEAR"


def test_a_fabricated_quote_is_discarded() -> None:
    result = evaluate_clauses(
        _facts(
            salary_amount={
                "value": 8500,
                "state": "STATED",
                "confidence": "HIGH",
                "evidence": "I earn eight thousand five hundred birr.",
            }
        )
        | {"transcript": "Worker: I would rather not say."}
    )
    clause = result.clauses["salary"]
    assert clause.evidence is None, "a quote absent from the transcript must not survive"
    # The value still stands; only the unverifiable quote is dropped.
    assert clause.value == 8500


def test_grounding_is_skipped_when_no_transcript_is_supplied() -> None:
    result = evaluate_clauses(
        _facts(salary_amount={"value": 8500, "state": "STATED", "confidence": "HIGH", "evidence": "anything"})
    )
    assert result.clauses["salary"].evidence == "anything"


# --------------------------------------------------------------------------- #
# Consent
# --------------------------------------------------------------------------- #


def test_without_consent_nothing_can_be_confirmed() -> None:
    result = evaluate_clauses(_facts() | {"consent": False})
    assert result.consent is False
    assert result.overall_verdict == "UNCLEAR"


def test_without_consent_a_safeguarding_stop_still_stands() -> None:
    result = evaluate_clauses(
        _facts(age_years={"value": 13, "state": "STATED", "confidence": "HIGH"}) | {"consent": False}
    )
    assert result.overall_verdict == "STOPPED"
    assert result.safeguarding_flag is True


# --------------------------------------------------------------------------- #
# Verdict precedence
# --------------------------------------------------------------------------- #


def test_an_established_failure_outranks_missing_evidence() -> None:
    result = evaluate_clauses(
        _facts(
            currently_employed={"value": False, "state": "STATED", "confidence": "HIGH"},
            salary_amount={"value": None, "state": "REFUSED", "confidence": "HIGH"},
        )
    )
    assert result.overall_verdict == "NOT_CONFIRMED"


def test_a_safeguarding_stop_outranks_an_established_failure() -> None:
    result = evaluate_clauses(
        _facts(
            age_years={"value": 13, "state": "STATED", "confidence": "HIGH"},
            currently_employed={"value": False, "state": "STATED", "confidence": "HIGH"},
        )
    )
    assert result.overall_verdict == "STOPPED"


def test_training_participation_never_changes_the_verdict() -> None:
    """Training is an employer-comparison axis, not a condition of a good job."""

    result = evaluate_clauses(
        _facts(training_participation={"value": False, "state": "STATED", "confidence": "HIGH"})
    )
    assert result.clauses["training_participation"].status == "NOT_MET"
    assert result.overall_verdict == "CONFIRMED_GOOD_JOB"


def test_derive_verdict_is_unclear_when_clauses_are_absent() -> None:
    assert derive_verdict({}) == "UNCLEAR"


# --------------------------------------------------------------------------- #
# Employer comparison
# --------------------------------------------------------------------------- #


def test_claims_read_from_a_plain_dict() -> None:
    assert employer_claims({"currently_employed": True, "salary": 8500}) == {
        "currently_employed": True,
        "salary": 8500,
    }


def test_claims_read_from_a_beneficiary_row() -> None:
    class FakeBeneficiary:
        employer_claims = {"currently_employed": True, "salary": 8500}

    assert employer_claims(FakeBeneficiary())["currently_employed"] is True


def test_claims_from_a_company_row_cannot_see_employment_status() -> None:
    """Documents the wiring limitation rather than papering over it.

    ``currently_employed`` is a per-worker claim on ``Beneficiary.employer_claims``.
    A company-level ``Employer`` row has no such column, so passing one makes the
    employment-status contradiction undetectable. ``average_salary`` is a company
    average and is used only as a fallback.
    """

    class FakeEmployer:
        average_salary = 8500

    claims = employer_claims(FakeEmployer())
    assert claims == {"salary": 8500}
    assert "currently_employed" not in claims

    left_the_job = evaluate_clauses(
        _facts(currently_employed={"value": False, "state": "STATED", "confidence": "HIGH"})
    )
    compared = compare_with_employer(left_the_job, FakeEmployer())
    assert [item.type for item in compared.contradictions] == []


def test_no_claims_means_no_contradictions() -> None:
    result = compare_with_employer(evaluate_clauses(_facts()), None)
    assert result.contradictions == []


def test_comparison_leaves_clauses_and_verdict_untouched() -> None:
    """Materiality is a separate axis from qualification."""

    before = evaluate_clauses(_facts(salary_amount={"value": 4000, "state": "STATED", "confidence": "HIGH"}))
    after = compare_with_employer(before, {"currently_employed": True, "salary": 8500})
    assert after.clauses == before.clauses
    assert after.overall_verdict == before.overall_verdict == "CONFIRMED_GOOD_JOB"
    assert [item.material for item in after.contradictions] == [True]


@pytest.mark.parametrize(
    ("worker_salary", "material"),
    [(8500, None), (7650, False), (7649, True), (9350, False), (9351, True)],
)
def test_salary_materiality_follows_the_tolerance(worker_salary: int, material: bool | None) -> None:
    """8500 +/- exactly 10% is inside the tolerance; a birr further is outside."""

    assert DECENT_WORK_CRITERIA["salary_contradiction_tolerance"] == 0.10
    result = compare_with_employer(
        evaluate_clauses(_facts(salary_amount={"value": worker_salary, "state": "STATED", "confidence": "HIGH"})),
        {"currently_employed": True, "salary": 8500},
    )
    if material is None:
        assert result.contradictions == [], "identical figures are not a contradiction"
        return
    assert [item.material for item in result.contradictions] == [material]


def test_a_refusal_produces_no_salary_contradiction() -> None:
    """The employer's number is never substituted for an answer not given."""

    result = compare_with_employer(
        evaluate_clauses(_facts(salary_amount={"value": None, "state": "REFUSED", "confidence": "HIGH"})),
        {"currently_employed": True, "salary": 8500},
    )
    assert result.contradictions == []
    assert result.clauses["salary"].value is None


def test_employment_status_disagreement_is_always_material() -> None:
    result = compare_with_employer(
        evaluate_clauses(_facts(currently_employed={"value": False, "state": "STATED", "confidence": "HIGH"})),
        {"currently_employed": True, "salary": 8500},
    )
    assert [(item.type, item.material) for item in result.contradictions] == [
        ("employment_status", True)
    ]


def test_the_reverse_employment_disagreement_is_also_recorded() -> None:
    result = compare_with_employer(
        evaluate_clauses(_facts()), {"currently_employed": False, "salary": 8500}
    )
    contradiction = result.contradictions[0]
    assert contradiction.type == "employment_status"
    assert contradiction.employer_value is False
    assert contradiction.worker_value is True


def test_material_contradictions_are_listed_first() -> None:
    result = compare_with_employer(
        evaluate_clauses(
            _facts(
                currently_employed={"value": False, "state": "STATED", "confidence": "HIGH"},
                salary_amount={"value": 8000, "state": "STATED", "confidence": "HIGH"},
            )
        ),
        {"currently_employed": True, "salary": 8500},
    )
    assert [item.type for item in result.contradictions] == ["employment_status", "salary"]
    assert [item.material for item in result.contradictions] == [True, False]


def test_a_zero_claim_is_not_compared() -> None:
    """Guards the percentage division and treats 0 as "not reported"."""

    result = compare_with_employer(
        evaluate_clauses(_facts()), {"currently_employed": True, "salary": 0}
    )
    assert result.contradictions == []


def test_contradictions_quote_the_worker_not_the_employer() -> None:
    result = compare_with_employer(
        evaluate_clauses(
            _facts(
                salary_amount={
                    "value": 4200,
                    "state": "STATED",
                    "confidence": "HIGH",
                    "evidence": "Four thousand two hundred birr.",
                }
            )
        ),
        {"currently_employed": True, "salary": 8500},
    )
    contradiction = result.contradictions[0]
    assert contradiction.evidence == "Four thousand two hundred birr."
    assert contradiction.worker_value == 4200
    assert contradiction.employer_value == 8500
    # Recording, not adjudicating.
    assert "does not adjudicate" in contradiction.description
