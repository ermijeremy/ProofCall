"""Contract validation for the transcript fixtures.

These tests do not exercise the intelligence engine. They assert that every
fixture in ``tests/fixtures/transcripts`` is a legal, self-consistent instance
of the frozen Member A/B contract, so that a fixture can never be the reason an
integration test fails. Concretely, each fixture must satisfy:

* ``expected_result`` validates as a :class:`WorkerEvidenceResult`, and the
  whole fixture validates as a :class:`CompletedCallResult`;
* every ``evidence`` string is a verbatim substring of that fixture's own
  transcript, so no evidence is ever invented;
* every extracted fact records how definite the answer was, and no clause claims
  more confidence than the facts behind it;
* clause statuses agree with the thresholds in :mod:`app.intelligence.criteria`,
  and the verdict follows from the clause statuses by verdict precedence;
* the hard-case rules hold: a refusal never adopts the employer's number, an
  under-age interview asks nothing further, and contradiction materiality
  respects the salary tolerance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts.integration import (
    ClauseEvidence,
    CompletedCallResult,
    WorkerEvidenceResult,
)
from app.intelligence.criteria import (
    DECENT_WORK_CRITERIA,
    FACT_STATES,
    RECORDED_CLAUSES,
    REQUIRED_CLAUSES,
)
from app.intelligence.rules import EVIDENCE_JOIN

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"

# The six cases the specification requires. Named explicitly so that deleting or
# renaming one fails loudly rather than silently shrinking coverage.
REQUIRED_FIXTURES = frozenset(
    {
        "normal_worker",
        "ambiguous_duration",
        "salary_refusal",
        "under_15_stopped",
        "salary_contradiction",
        "employment_status_contradiction",
    }
)

# Statuses that mean "no usable value was established".
UNRESOLVED_STATUSES = frozenset({"UNCLEAR", "REFUSED", "NOT_ASKED"})

# Confidence levels, weakest first, so they can be compared by rank.
CONFIDENCE_LEVELS = ("LOW", "MEDIUM", "HIGH")

# Employer claim key backing each contradiction type.
EMPLOYER_CLAIM_KEY = {"salary": "salary", "employment_status": "currently_employed"}

# Clause fed by each extracted fact, for the clauses that map one to one.
CLAUSE_FOR_FACT = {
    "age_years": "age",
    "currently_employed": "employment_status",
    "employment_duration_months": "employment_duration",
    "salary_amount": "salary",
    "forced_labour_present": "forced_labour",
    "discrimination_present": "discrimination",
    "freedom_of_association_restricted": "freedom_of_association",
    "training_participation": "training_participation",
}


def _load_fixtures() -> dict[str, dict[str, Any]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    assert paths, f"no transcript fixtures found in {FIXTURE_DIR}"
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in paths}


FIXTURES = _load_fixtures()
ALL_CASES = sorted(FIXTURES)
# Variants restate an existing case in another language, so they share a
# worker id and are excluded from the one-fixture-per-worker check.
PRIMARY_CASES = sorted(name for name in FIXTURES if "variant_of" not in FIXTURES[name])


def _clauses(fixture: dict[str, Any]) -> dict[str, ClauseEvidence]:
    return {
        name: ClauseEvidence.model_validate(payload)
        for name, payload in fixture["expected_result"]["clauses"].items()
    }


def _rank(confidence: str) -> int:
    return CONFIDENCE_LEVELS.index(confidence)


def _derive_verdict(clauses: dict[str, ClauseEvidence]) -> str:
    """Apply verdict precedence to the required clause statuses.

    Mirrors ``VERDICT_PRECEDENCE``: a stop outranks a known failure, and a
    known failure outranks missing evidence.
    """

    statuses = {clauses[name].status for name in REQUIRED_CLAUSES}
    if "STOPPED" in statuses:
        return "STOPPED"
    if "NOT_MET" in statuses:
        return "NOT_CONFIRMED"
    if statuses & UNRESOLVED_STATUSES:
        return "UNCLEAR"
    return "CONFIRMED_GOOD_JOB"


def test_all_required_fixtures_present() -> None:
    missing = REQUIRED_FIXTURES - set(FIXTURES)
    assert not missing, f"missing required fixtures: {sorted(missing)}"


def test_one_primary_fixture_per_worker() -> None:
    seen: dict[str, str] = {}
    for name in PRIMARY_CASES:
        worker_id = FIXTURES[name]["worker_id"]
        assert worker_id not in seen, (
            f"{name} and {seen[worker_id]} both claim {worker_id}; "
            "add variant_of if one is an alternative rendering of the other"
        )
        seen[worker_id] = name


@pytest.mark.parametrize("case", ALL_CASES)
def test_fixture_validates_as_completed_call_result(case: str) -> None:
    """The fixture is directly usable as Member B's handoff object."""

    fixture = FIXTURES[case]
    expected = fixture["expected_result"]
    result = CompletedCallResult.model_validate(
        {
            "worker_id": fixture["worker_id"],
            "call_id": fixture["call_id"],
            "transcript": fixture["transcript"],
            "transcript_turns": fixture["transcript_turns"],
            "language": fixture["language"],
            "consent": expected["consent"],
            "clauses": expected["clauses"],
            "contradictions": expected["contradictions"],
            "safeguarding_flag": expected["safeguarding_flag"],
            "overall_verdict": expected["overall_verdict"],
        }
    )
    assert result.worker_id == fixture["worker_id"]
    assert result.overall_verdict == expected["overall_verdict"]


@pytest.mark.parametrize("case", ALL_CASES)
def test_expected_result_validates_and_is_complete(case: str) -> None:
    fixture = FIXTURES[case]
    result = WorkerEvidenceResult.model_validate(fixture["expected_result"])

    assert result.worker_id == fixture["worker_id"]
    assert set(result.clauses) == set(REQUIRED_CLAUSES) | set(RECORDED_CLAUSES)


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_evidence_string_is_quoted_verbatim(case: str) -> None:
    """Evidence must be lifted from the transcript, never paraphrased."""

    fixture = FIXTURES[case]
    transcript = fixture["transcript"]

    for name, clause in _clauses(fixture).items():
        if clause.evidence is None:
            continue
        # A derived clause may quote two turns, joined by EVIDENCE_JOIN. Each
        # part must still be verbatim; the separator itself is ours, not spoken.
        for part in clause.evidence.split(EVIDENCE_JOIN):
            assert part in transcript, (
                f"{case}: clause {name!r} quotes evidence that is not in the transcript: "
                f"{part!r}"
            )

    for fact, payload in fixture["expected_extraction"]["facts"].items():
        evidence = payload["evidence"]
        if evidence is None:
            continue
        assert EVIDENCE_JOIN not in evidence, (
            f"{case}: fact {fact!r} joins two quotes; a single fact quotes one turn"
        )
        assert evidence in transcript, (
            f"{case}: fact {fact!r} quotes evidence that is not in the transcript: "
            f"{evidence!r}"
        )

    for turn in fixture["transcript_turns"]:
        assert turn["text"] in transcript, (
            f"{case}: turn text is absent from the normalized transcript: {turn['text']!r}"
        )


@pytest.mark.parametrize("case", ALL_CASES)
def test_extraction_vocabulary_is_threshold_free(case: str) -> None:
    """The model reports what was said; it never reports a status or verdict."""

    extraction = FIXTURES[case]["expected_extraction"]
    assert extraction["worker_id"] == FIXTURES[case]["worker_id"]

    for fact, payload in extraction["facts"].items():
        assert payload["state"] in FACT_STATES, f"{case}: {fact} has state {payload['state']!r}"
        if payload["state"] in {"REFUSED", "NOT_ASKED"}:
            assert payload["value"] is None, f"{case}: {fact} reports a value it never obtained"
        if payload["state"] == "NOT_ASKED":
            assert payload["evidence"] is None, f"{case}: {fact} was not asked but cites evidence"


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_fact_records_how_definite_the_answer_was(case: str) -> None:
    """Confidence belongs to extraction: it judges wording, not thresholds.

    The rule engine can lower a confidence but never raise one, so a missing or
    invented level here would silently weaken or overstate the whole result.
    """

    for fact, payload in FIXTURES[case]["expected_extraction"]["facts"].items():
        assert "confidence" in payload, f"{case}: fact {fact!r} records no confidence"
        assert payload["confidence"] in CONFIDENCE_LEVELS, (
            f"{case}: fact {fact!r} has confidence {payload['confidence']!r}"
        )
        if payload["state"] == "NOT_ASKED":
            # Nothing was said, so there is nothing to be confident about.
            assert payload["confidence"] == "LOW", (
                f"{case}: fact {fact!r} was never asked but claims "
                f"{payload['confidence']} confidence"
            )
        if payload["state"] == "REFUSED":
            # A clear refusal is a definite outcome: we know they declined.
            assert payload["confidence"] == "HIGH", (
                f"{case}: fact {fact!r} is a refusal but claims "
                f"{payload['confidence']} confidence"
            )


@pytest.mark.parametrize("case", ALL_CASES)
def test_clause_confidence_is_no_stronger_than_its_facts(case: str) -> None:
    """A clause inherits the weakest confidence of the facts behind it."""

    fixture = FIXTURES[case]
    facts = fixture["expected_extraction"]["facts"]
    clauses = _clauses(fixture)

    for fact, clause_name in CLAUSE_FOR_FACT.items():
        clause = clauses[clause_name]
        if clause.status in UNRESOLVED_STATUSES and clause.status != "REFUSED":
            continue  # No value established, so confidence is floored to LOW.
        assert _rank(clause.confidence) <= _rank(facts[fact]["confidence"]), (
            f"{case}: clause {clause_name!r} claims {clause.confidence} from a "
            f"{facts[fact]['confidence']} fact"
        )

    hours = clauses["working_hours"]
    if hours.status not in UNRESOLVED_STATUSES:
        weakest = min(
            _rank(facts[name]["confidence"])
            for name in ("working_days_per_week", "working_hours_per_day")
        )
        assert _rank(hours.confidence) == weakest, (
            f"{case}: working_hours must take the weaker of its two facts"
        )


@pytest.mark.parametrize("case", ALL_CASES)
def test_clause_values_follow_the_extracted_facts(case: str) -> None:
    fixture = FIXTURES[case]
    facts = fixture["expected_extraction"]["facts"]
    clauses = _clauses(fixture)

    for fact, clause_name in CLAUSE_FOR_FACT.items():
        assert clauses[clause_name].value == facts[fact]["value"], (
            f"{case}: clause {clause_name!r} value disagrees with extracted {fact!r}"
        )

    # Weekly hours are derived by code from days and hours per day, not asked for
    # directly, because respondents answer in days and hours.
    days = facts["working_days_per_week"]["value"]
    hours = facts["working_hours_per_day"]["value"]
    if days is None or hours is None:
        assert clauses["working_hours"].value is None
    else:
        assert clauses["working_hours"].value == days * hours, (
            f"{case}: working_hours should be {days} x {hours} = {days * hours}"
        )


@pytest.mark.parametrize("case", ALL_CASES)
def test_clause_statuses_agree_with_the_thresholds(case: str) -> None:
    clauses = _clauses(fixture := FIXTURES[case])
    minimum_age = DECENT_WORK_CRITERIA["minimum_age"]
    minimum_hours = DECENT_WORK_CRITERIA["minimum_weekly_hours"]
    minimum_months = DECENT_WORK_CRITERIA["minimum_duration_months"]

    for name, clause in clauses.items():
        if clause.value is None:
            assert clause.status in UNRESOLVED_STATUSES, (
                f"{case}: clause {name!r} has no value but status {clause.status!r}"
            )
            assert clause.confidence == "LOW" or clause.status == "REFUSED", (
                f"{case}: clause {name!r} has no value but confidence {clause.confidence!r}"
            )

    age = clauses["age"]
    if age.value is not None:
        # Under the minimum age the interview stops; it is a safeguarding event,
        # not a failed condition, so the status is STOPPED rather than NOT_MET.
        expected_age_status = "MET" if age.value >= minimum_age else "STOPPED"
        assert age.status == expected_age_status, (
            f"{case}: age {age.value} against minimum {minimum_age} should be "
            f"{expected_age_status}, not {age.status!r}"
        )

    duration = clauses["employment_duration"]
    if duration.value is not None:
        expected = "MET" if duration.value >= minimum_months else "NOT_MET"
        assert duration.status == expected, (
            f"{case}: duration {duration.value} months against minimum {minimum_months} "
            f"should be {expected}"
        )

    hours = clauses["working_hours"]
    if hours.value is not None:
        expected = "MET" if hours.value >= minimum_hours else "NOT_MET"
        assert hours.status == expected, (
            f"{case}: {hours.value} weekly hours against minimum {minimum_hours} "
            f"should be {expected}"
        )

    status_clause = clauses["employment_status"]
    if status_clause.value is not None:
        assert status_clause.status == ("MET" if status_clause.value else "NOT_MET")

    # For the three labour-rights clauses the stored value is "violation
    # present", so False is the compliant state.
    for name in ("forced_labour", "discrimination", "freedom_of_association"):
        clause = clauses[name]
        if clause.value is not None:
            assert clause.status == ("NOT_MET" if clause.value else "MET"), (
                f"{case}: clause {name!r} value {clause.value} should map to "
                f"{'NOT_MET' if clause.value else 'MET'}"
            )

    # There is no wage floor. Salary only has to be established, so any stated
    # amount is MET regardless of size.
    salary = clauses["salary"]
    assert DECENT_WORK_CRITERIA["salary_floor"] is None
    if salary.value is not None:
        assert salary.status == "MET", (
            f"{case}: salary {salary.value} is established, so the clause is MET; "
            "the shortfall against the employer belongs in a contradiction"
        )

    assert fixture["expected_result"]["overall_verdict"] == _derive_verdict(clauses)


@pytest.mark.parametrize("case", ALL_CASES)
def test_stopped_interviews_ask_nothing_further(case: str) -> None:
    fixture = FIXTURES[case]
    result = fixture["expected_result"]
    clauses = _clauses(fixture)

    if result["overall_verdict"] != "STOPPED":
        assert result["safeguarding_flag"] is False, (
            f"{case}: safeguarding flag is raised without a stopped interview"
        )
        assert fixture["expected_extraction"]["interview_stopped"] is False
        return

    assert result["safeguarding_flag"] is True, f"{case}: a stopped interview must be flagged"
    assert fixture["expected_extraction"]["interview_stopped"] is True
    assert clauses["age"].status == "STOPPED"

    for name in REQUIRED_CLAUSES:
        if name == "age":
            continue
        assert clauses[name].status == "NOT_ASKED", (
            f"{case}: clause {name!r} is {clauses[name].status!r}; a stopped interview "
            "asks no working-conditions questions, so the remaining clauses are NOT_ASKED"
        )

    assert result["contradictions"] == [], (
        f"{case}: nothing was established, so there is nothing to contradict"
    )


@pytest.mark.parametrize("case", ALL_CASES)
def test_refusal_never_adopts_the_employer_value(case: str) -> None:
    fixture = FIXTURES[case]
    clauses = _clauses(fixture)
    salary = clauses["salary"]

    if salary.status != "REFUSED":
        return

    assert salary.value is None, (
        f"{case}: salary was refused, so the clause must carry no value; "
        f"found {salary.value!r}"
    )
    assert not any(item["type"] == "salary" for item in fixture["expected_result"]["contradictions"]), (
        f"{case}: a refusal is not a contradiction, there is no worker value to compare"
    )
    # The employer's figure must not have leaked into the evidence either.
    employer_salary = fixture["employer_claims"]["salary"]
    assert str(employer_salary) not in (salary.evidence or "")


@pytest.mark.parametrize("case", ALL_CASES)
def test_contradictions_are_grounded_and_correctly_scored(case: str) -> None:
    fixture = FIXTURES[case]
    clauses = _clauses(fixture)
    employer_claims = fixture["employer_claims"]
    tolerance = DECENT_WORK_CRITERIA["salary_contradiction_tolerance"]

    result = WorkerEvidenceResult.model_validate(fixture["expected_result"])

    for contradiction in result.contradictions:
        claim_key = EMPLOYER_CLAIM_KEY.get(contradiction.type)
        assert claim_key is not None, f"{case}: unknown contradiction type {contradiction.type!r}"
        assert contradiction.employer_value == employer_claims[claim_key], (
            f"{case}: {contradiction.type} contradiction cites an employer value that is "
            "not the recorded claim"
        )
        assert contradiction.employer_value != contradiction.worker_value, (
            f"{case}: {contradiction.type} contradiction records two identical values"
        )
        assert contradiction.worker_value is not None, (
            f"{case}: {contradiction.type} contradiction has no worker value; "
            "silence is not a contradiction"
        )
        assert contradiction.evidence is not None
        assert contradiction.evidence in fixture["transcript"]

        if contradiction.type == "salary":
            assert clauses["salary"].value == contradiction.worker_value
            shortfall = abs(
                contradiction.employer_value - contradiction.worker_value
            ) / contradiction.employer_value
            assert contradiction.material is (shortfall > tolerance), (
                f"{case}: salary gap of {shortfall:.1%} against a {tolerance:.0%} tolerance "
                f"should be material={shortfall > tolerance}"
            )

        if contradiction.type == "employment_status":
            assert clauses["employment_status"].value == contradiction.worker_value
            # Whether the worker is employed at all is never a rounding
            # difference, so this disagreement is always material.
            assert contradiction.material is True


@pytest.mark.parametrize("case", ALL_CASES)
def test_stated_worker_values_that_disagree_are_recorded(case: str) -> None:
    """No silent drops: a stated value differing from the claim must appear."""

    fixture = FIXTURES[case]
    clauses = _clauses(fixture)
    employer_claims = fixture["employer_claims"]
    recorded = {item["type"] for item in fixture["expected_result"]["contradictions"]}

    for contradiction_type, claim_key in EMPLOYER_CLAIM_KEY.items():
        clause_name = "salary" if contradiction_type == "salary" else "employment_status"
        clause = clauses[clause_name]
        if clause.value is None or clause.status in UNRESOLVED_STATUSES:
            continue
        if clause.value != employer_claims[claim_key]:
            assert contradiction_type in recorded, (
                f"{case}: worker states {clause.value!r} against the employer's "
                f"{employer_claims[claim_key]!r} but no {contradiction_type} contradiction "
                "is recorded"
            )


@pytest.mark.parametrize("case", ALL_CASES)
def test_consent_precedes_every_answer(case: str) -> None:
    fixture = FIXTURES[case]
    assert fixture["expected_extraction"]["consent"] is True
    assert fixture["expected_result"]["consent"] is True

    turns = fixture["transcript_turns"]
    assert turns[0]["speaker"] == "interviewer", f"{case}: the call must open with the script"
    assert turns[1]["speaker"] == "worker", f"{case}: consent is the respondent's first turn"


@pytest.mark.parametrize("case", ALL_CASES)
def test_interview_never_discloses_a_threshold(case: str) -> None:
    """Naming a threshold coaches the respondent, so the script may not."""

    fixture = FIXTURES[case]
    thresholds = [
        str(DECENT_WORK_CRITERIA["minimum_age"]),
        str(DECENT_WORK_CRITERIA["minimum_weekly_hours"]),
        str(DECENT_WORK_CRITERIA["minimum_duration_months"]),
    ]
    spelled = ("fifteen", "twenty hours", "six months", "at least")

    for turn in fixture["transcript_turns"]:
        if turn["speaker"] != "interviewer":
            continue
        text = turn["text"].lower()
        probe = f"{text} {turn.get('english_gloss', '').lower()}"
        for threshold in thresholds:
            assert threshold not in text, (
                f"{case}: interviewer turn names the threshold {threshold}: {turn['text']!r}"
            )
        for phrase in spelled:
            assert phrase not in probe, (
                f"{case}: interviewer turn discloses a threshold ({phrase!r}): {turn['text']!r}"
            )


@pytest.mark.parametrize("case", ALL_CASES)
def test_variants_match_the_case_they_restate(case: str) -> None:
    fixture = FIXTURES[case]
    parent_name = fixture.get("variant_of")
    if parent_name is None:
        return

    assert parent_name in FIXTURES, f"{case}: variant_of names an unknown fixture {parent_name!r}"
    parent = FIXTURES[parent_name]

    assert fixture["worker_id"] == parent["worker_id"]
    assert fixture["call_id"] != parent["call_id"], f"{case}: variant reuses the parent call id"
    assert fixture["language"] != parent["language"], (
        f"{case}: a variant exists to change the language"
    )

    # The point of the variant: identical structured outcome, different words.
    for field in ("overall_verdict", "safeguarding_flag", "contradictions"):
        assert fixture["expected_result"][field] == parent["expected_result"][field], (
            f"{case}: {field} diverges from {parent_name}; the rule engine must be "
            "language-independent"
        )
    for name in set(REQUIRED_CLAUSES) | set(RECORDED_CLAUSES):
        variant_clause = fixture["expected_result"]["clauses"][name]
        parent_clause = parent["expected_result"]["clauses"][name]
        assert variant_clause["status"] == parent_clause["status"], (
            f"{case}: clause {name!r} status diverges from {parent_name}"
        )
        assert variant_clause["value"] == parent_clause["value"], (
            f"{case}: clause {name!r} value diverges from {parent_name}"
        )
