"""Deterministic rule engine: facts in, clause statuses and a verdict out.

Nothing in this module calls a model, and nothing here is allowed to. The
extraction step reports what the respondent said; this module decides what it
means by comparing values against :mod:`app.intelligence.criteria`. That split
is the point of the whole design: the same transcript always produces the same
verdict, and every verdict can be re-derived from the recorded facts.

Four rules deserve stating outright, because each one is a decision that could
reasonably have gone another way.

**Under the minimum age is STOPPED, not NOT_MET.** A child working is a
safeguarding event, not a failed condition. It is reported separately and
excluded from good-job counts rather than being counted as a bad job.

**A refusal is REFUSED, not NOT_MET.** Declining to state pay is the
respondent's right. It blocks confirmation, because pay must be established,
but it is never held against them and the employer's figure is never
substituted for the answer they did not give.

**Salary has no floor.** The programme defines none, so any stated amount
satisfies the clause. A large gap against the employer's figure is recorded as a
contradiction, which is a separate axis from whether the job qualifies.

**A contradiction is recorded, never adjudicated.** Both values are stored with
the respondent's own words. CallProof does not decide who is lying.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.contracts.integration import (
    ClauseEvidence,
    ClauseStatus,
    Confidence,
    Contradiction,
    Verdict,
    WorkerEvidenceResult,
)
from app.intelligence.criteria import (
    DECENT_WORK_CRITERIA,
    RECORDED_CLAUSES,
    REQUIRED_CLAUSES,
)

#: Separator used when a derived clause quotes two different turns. Each part
#: remains a verbatim span of the transcript.
EVIDENCE_JOIN = " ... "

#: Statuses meaning "no usable value was established".
UNRESOLVED_STATUSES: frozenset[str] = frozenset({"UNCLEAR", "REFUSED", "NOT_ASKED"})

_CONFIDENCE_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


@dataclass(frozen=True)
class Fact:
    """One extracted fact, normalized. Never constructed from employer data."""

    value: Any = None
    state: str = "VAGUE"
    confidence: str = "LOW"
    evidence: str | None = None


def _coerce_confidence(value: Any) -> Confidence:
    text = str(value or "").upper()
    return text if text in _CONFIDENCE_RANK else "LOW"  # type: ignore[return-value]


def _weakest(*confidences: str) -> Confidence:
    present = [c for c in confidences if c in _CONFIDENCE_RANK]
    if not present:
        return "LOW"
    return min(present, key=lambda c: _CONFIDENCE_RANK[c])  # type: ignore[return-value]


def _as_number(value: Any) -> float | int | None:
    """Numbers only. ``bool`` is an ``int`` in Python and is rejected here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _fact(facts: dict[str, Any], name: str, *, stopped: bool) -> Fact:
    """Read one fact, defaulting safely when the extraction is incomplete.

    A missing fact is not evidence of anything, so it resolves to a state that
    can never produce a confirmation: NOT_ASKED after a stopped interview, and
    VAGUE (which becomes UNCLEAR) otherwise.
    """

    raw = facts.get(name)
    if not isinstance(raw, dict):
        return Fact(state="NOT_ASKED" if stopped else "VAGUE")

    state = str(raw.get("state") or "").upper()
    if state not in {"STATED", "REFUSED", "VAGUE", "NOT_ASKED"}:
        state = "NOT_ASKED" if stopped else "VAGUE"

    evidence = raw.get("evidence")
    return Fact(
        value=raw.get("value"),
        state=state,
        confidence=_coerce_confidence(raw.get("confidence")),
        evidence=evidence if isinstance(evidence, str) and evidence.strip() else None,
    )


def _unresolved_status(state: str) -> ClauseStatus:
    if state == "REFUSED":
        return "REFUSED"
    if state == "NOT_ASKED":
        return "NOT_ASKED"
    return "UNCLEAR"


def _unresolved(fact: Fact) -> ClauseEvidence:
    """A clause with no value. Confidence is LOW unless the refusal was clear.

    A refusal keeps its confidence because we are certain of what happened: the
    respondent declined. Vagueness and silence do not earn that certainty.
    """

    status = _unresolved_status(fact.state)
    confidence = fact.confidence if status == "REFUSED" else "LOW"
    return ClauseEvidence(
        value=None, status=status, confidence=_coerce_confidence(confidence), evidence=fact.evidence
    )


def _evaluate_age(fact: Fact, minimum_age: float) -> ClauseEvidence:
    value = _as_number(fact.value)
    if fact.state != "STATED" or value is None:
        return _unresolved(fact)
    # Below the minimum the interview stops. This is a safeguarding outcome, so
    # it is STOPPED rather than NOT_MET and never counts as a bad job.
    status: ClauseStatus = "MET" if value >= minimum_age else "STOPPED"
    return ClauseEvidence(
        value=value, status=status, confidence=fact.confidence, evidence=fact.evidence
    )


def _evaluate_minimum(fact: Fact, minimum: float) -> ClauseEvidence:
    value = _as_number(fact.value)
    if fact.state != "STATED" or value is None:
        return _unresolved(fact)
    status: ClauseStatus = "MET" if value >= minimum else "NOT_MET"
    return ClauseEvidence(
        value=value, status=status, confidence=fact.confidence, evidence=fact.evidence
    )


def _evaluate_flag(fact: Fact, *, met_when: bool) -> ClauseEvidence:
    """Boolean clause. ``met_when`` is the value that satisfies the condition.

    The three labour-rights facts record whether a violation is *present*, so
    they are satisfied by ``False``. Employment status and training records are
    satisfied by ``True``.
    """

    value = _as_bool(fact.value)
    if fact.state != "STATED" or value is None:
        return _unresolved(fact)
    status: ClauseStatus = "MET" if value is met_when else "NOT_MET"
    return ClauseEvidence(
        value=value, status=status, confidence=fact.confidence, evidence=fact.evidence
    )


def _evaluate_working_hours(days: Fact, hours: Fact, minimum: float) -> ClauseEvidence:
    """Weekly hours, derived by code from days per week and hours per day.

    Respondents answer in days and hours, not in weekly totals, so the
    multiplication belongs here rather than in the extraction step: it is
    arithmetic, and arithmetic is not the model's job.
    """

    days_value = _as_number(days.value)
    hours_value = _as_number(hours.value)

    if days.state != "STATED" or hours.state != "STATED" or days_value is None or hours_value is None:
        states = {days.state, hours.state}
        # A refusal is the most informative failure, silence the least.
        if "REFUSED" in states:
            status: ClauseStatus = "REFUSED"
        elif states - {"NOT_ASKED"}:
            status = "UNCLEAR"
        else:
            status = "NOT_ASKED"
        return ClauseEvidence(
            value=None,
            status=status,
            confidence=_weakest(days.confidence, hours.confidence) if status == "REFUSED" else "LOW",
            evidence=_join_evidence(days.evidence, hours.evidence),
        )

    weekly = days_value * hours_value
    return ClauseEvidence(
        value=weekly,
        status="MET" if weekly >= minimum else "NOT_MET",
        confidence=_weakest(days.confidence, hours.confidence),
        evidence=_join_evidence(days.evidence, hours.evidence),
    )


def _evaluate_salary(fact: Fact, floor: Any) -> ClauseEvidence:
    """Salary must be established. A floor is honoured only if one is defined."""

    value = _as_number(fact.value)
    if fact.state != "STATED" or value is None:
        return _unresolved(fact)
    minimum = _as_number(floor)
    status: ClauseStatus = "MET" if minimum is None or value >= minimum else "NOT_MET"
    return ClauseEvidence(
        value=value, status=status, confidence=fact.confidence, evidence=fact.evidence
    )


def _drop_ungrounded(clause: ClauseEvidence, transcript: str) -> ClauseEvidence:
    """Discard a quote that is not actually in the transcript.

    A fabricated quote is worse than no quote: the dashboard presents evidence
    as the respondent's own words, and a reviewer must be able to find them.
    """

    if clause.evidence is None:
        return clause
    parts = clause.evidence.split(EVIDENCE_JOIN)
    if all(part in transcript for part in parts):
        return clause
    return clause.model_copy(update={"evidence": None})


def _join_evidence(*quotes: str | None) -> str | None:
    """Keep each quote verbatim; join only when the turns actually differ."""

    seen: list[str] = []
    for quote in quotes:
        if quote and quote not in seen:
            seen.append(quote)
    if not seen:
        return None
    return EVIDENCE_JOIN.join(seen)


def evaluate_clauses(
    evidence: dict[str, Any],
    criteria: dict[str, Any] | None = None,
) -> WorkerEvidenceResult:
    """Turn one extraction payload into clause statuses and a verdict."""

    active = dict(DECENT_WORK_CRITERIA if criteria is None else criteria)
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), dict) else {}
    stopped = bool(evidence.get("interview_stopped", False))
    consent = bool(evidence.get("consent", False))

    read = {name: _fact(facts, name, stopped=stopped) for name in (
        "age_years",
        "currently_employed",
        "employment_duration_months",
        "working_days_per_week",
        "working_hours_per_day",
        "salary_amount",
        "training_participation",
        "forced_labour_present",
        "discrimination_present",
        "freedom_of_association_restricted",
    )}

    clauses: dict[str, ClauseEvidence] = {
        "age": _evaluate_age(read["age_years"], active["minimum_age"]),
        "employment_status": _evaluate_flag(read["currently_employed"], met_when=True),
        "employment_duration": _evaluate_minimum(
            read["employment_duration_months"], active["minimum_duration_months"]
        ),
        "working_hours": _evaluate_working_hours(
            read["working_days_per_week"],
            read["working_hours_per_day"],
            active["minimum_weekly_hours"],
        ),
        # No floor: pay must be established, not large. A stated amount satisfies
        # the clause however small, and the gap against the employer is recorded
        # as a contradiction instead.
        "salary": _evaluate_salary(read["salary_amount"], active.get("salary_floor")),
        "forced_labour": _evaluate_flag(read["forced_labour_present"], met_when=False),
        "discrimination": _evaluate_flag(read["discrimination_present"], met_when=False),
        "freedom_of_association": _evaluate_flag(
            read["freedom_of_association_restricted"], met_when=False
        ),
        "training_participation": _evaluate_flag(read["training_participation"], met_when=True),
    }

    transcript = evidence.get("transcript")
    if isinstance(transcript, str) and transcript:
        clauses = {name: _drop_ungrounded(clause, transcript) for name, clause in clauses.items()}

    verdict = derive_verdict(clauses)
    # An interview nobody agreed to cannot confirm anything, however complete
    # the answers look. It can still raise a safeguarding stop.
    if not consent and verdict == "CONFIRMED_GOOD_JOB":
        verdict = "UNCLEAR"

    return WorkerEvidenceResult(
        worker_id=str(evidence.get("worker_id") or ""),
        consent=consent,
        clauses=clauses,
        contradictions=[],
        safeguarding_flag=clauses["age"].status == "STOPPED",
        overall_verdict=verdict,
    )


def derive_verdict(clauses: dict[str, ClauseEvidence]) -> Verdict:
    """Apply verdict precedence to the required clauses.

    Order matters: a safeguarding stop outranks everything, and an established
    failure outranks missing evidence, because a failure is a determination
    while ambiguity is an absence of one.
    """

    statuses = {clauses[name].status for name in REQUIRED_CLAUSES if name in clauses}
    if "STOPPED" in statuses:
        return "STOPPED"
    if "NOT_MET" in statuses:
        return "NOT_CONFIRMED"
    if statuses & UNRESOLVED_STATUSES or len(statuses) == 0:
        return "UNCLEAR"
    return "CONFIRMED_GOOD_JOB"


# --------------------------------------------------------------------------- #
# Employer comparison
# --------------------------------------------------------------------------- #

#: Employer claim key backing each comparison axis.
CLAIM_KEYS = {"salary": "salary", "employment_status": "currently_employed"}


def employer_claims(employer: Any) -> dict[str, Any]:
    """Normalize whatever the caller passes into per-worker claims.

    Accepts a plain dict of claims, a ``Beneficiary`` row (whose
    ``employer_claims`` JSON column holds the per-worker figures), or an
    ``Employer`` row (company-level, so it can only supply an average salary).

    The company-level row is the awkward case and worth being explicit about:
    ``currently_employed`` is a per-worker claim and simply does not exist on it,
    so passing an ``Employer`` makes the employment-status contradiction
    undetectable. That is a wiring question, not a rules question, and this
    function reports what it can rather than inventing the rest.
    """

    if employer is None:
        return {}
    if isinstance(employer, dict):
        claims = dict(employer)
    else:
        nested = getattr(employer, "employer_claims", None)
        claims = dict(nested) if isinstance(nested, dict) else {}
        for field in ("currently_employed", "salary"):
            if field not in claims and getattr(employer, field, None) is not None:
                claims[field] = getattr(employer, field)
        if "salary" not in claims and getattr(employer, "average_salary", None) is not None:
            claims["salary"] = employer.average_salary

    if "salary" not in claims and claims.get("average_salary") is not None:
        claims["salary"] = claims["average_salary"]
    return claims


def _salary_contradiction(
    clause: ClauseEvidence, claimed: Any, tolerance: float
) -> Contradiction | None:
    worker_value = _as_number(clause.value)
    claimed_value = _as_number(claimed)
    if clause.status in UNRESOLVED_STATUSES or worker_value is None:
        # Silence is not a contradiction, and the employer's figure is never
        # substituted for an answer the worker did not give.
        return None
    if claimed_value is None or claimed_value == 0 or worker_value == claimed_value:
        return None

    gap = (worker_value - claimed_value) / claimed_value
    direction = "below" if gap < 0 else "above"
    material = abs(gap) > tolerance
    verdict_clause = (
        f"Recorded as material because the gap exceeds the {tolerance:.0%} tolerance."
        if material
        else f"Within the {tolerance:.0%} tolerance, so recorded but not treated as material."
    )
    return Contradiction(
        type="salary",
        description=(
            f"Employer reports a salary of {_plain(claimed_value)} for this worker; "
            f"the worker states {_plain(worker_value)}, {abs(gap):.1%} {direction} the "
            f"employer figure. {verdict_clause} "
            "CallProof does not adjudicate which figure is correct."
        ),
        material=material,
        employer_value=claimed_value,
        worker_value=worker_value,
        evidence=clause.evidence,
    )


def _employment_status_contradiction(clause: ClauseEvidence, claimed: Any) -> Contradiction | None:
    worker_value = _as_bool(clause.value)
    claimed_value = _as_bool(claimed)
    if clause.status in UNRESOLVED_STATUSES or worker_value is None or claimed_value is None:
        return None
    if worker_value is claimed_value:
        return None

    claim_text = "currently employed" if claimed_value else "not currently employed"
    worker_text = "they are" if worker_value else "they are not"
    return Contradiction(
        type="employment_status",
        description=(
            f"Employer reports this worker as {claim_text}; the worker states {worker_text}. "
            "CallProof records both positions and does not determine which is correct. "
            "Whether someone holds the job at all is never a rounding difference, so this "
            "is always material."
        ),
        material=True,
        employer_value=claimed_value,
        worker_value=worker_value,
        evidence=clause.evidence,
    )


def _plain(value: float | int) -> str:
    """Render 8500.0 as ``8500`` so descriptions read like the source data."""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def compare_with_employer(
    worker_result: WorkerEvidenceResult,
    employer: Any,
    criteria: dict[str, Any] | None = None,
) -> WorkerEvidenceResult:
    """Attach contradictions between the worker's account and the claims.

    Clause statuses and the verdict are left untouched. A contradiction is a
    finding about the employer's report, not about whether the job qualifies:
    a worker can hold a genuinely good job while the employer's salary figure
    for them is wrong by half.
    """

    active = dict(DECENT_WORK_CRITERIA if criteria is None else criteria)
    tolerance = float(active.get("salary_contradiction_tolerance") or 0.0)
    claims = employer_claims(employer)

    found: list[Contradiction] = []
    salary_clause = worker_result.clauses.get("salary")
    if salary_clause is not None and CLAIM_KEYS["salary"] in claims:
        contradiction = _salary_contradiction(salary_clause, claims["salary"], tolerance)
        if contradiction is not None:
            found.append(contradiction)

    status_clause = worker_result.clauses.get("employment_status")
    if status_clause is not None and CLAIM_KEYS["employment_status"] in claims:
        contradiction = _employment_status_contradiction(
            status_clause, claims["currently_employed"]
        )
        if contradiction is not None:
            found.append(contradiction)

    return worker_result.model_copy(
        # Material findings first, then a stable order by axis, so the dashboard
        # leads with what matters and two runs never reorder the list.
        update={"contradictions": sorted(found, key=lambda item: (not item.material, item.type))}
    )
