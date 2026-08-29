"""Canonical decent-work qualification criteria.

This module is the single source of truth for the qualification thresholds.
Member B stores a copy on the campaign and renders it read-only on Programme
Overview; it must import ``DECENT_WORK_CRITERIA`` rather than restate the
values, so the stored copy cannot drift from the rule engine.

Two rules from the specification constrain everything here:

* thresholds are applied by code, never by the language model;
* the interview prompt must never disclose a threshold to the respondent.
"""

from typing import Final

# Thresholds. The three ``minimum_*`` keys match the keys Member B already
# stores on the campaign, so the rendered copy and the engine agree by
# construction.
DECENT_WORK_CRITERIA: Final[dict[str, object]] = {
    "minimum_age": 15,
    "minimum_weekly_hours": 20,
    "minimum_duration_months": 6,
    # The challenge defines no wage floor, so salary is required to be
    # *established* but is never compared against a threshold. A refusal
    # therefore blocks confirmation without counting against the worker.
    "salary_must_be_established": True,
    "salary_floor": None,
    "forced_labour_must_be_absent": True,
    "discrimination_must_be_absent": True,
    "freedom_of_association_required": True,
    # Fraction by which a worker-stated salary must differ from the employer's
    # claim before the resulting contradiction is treated as material.
    "salary_contradiction_tolerance": 0.10,
}

# Clauses that gate the good-job verdict.
REQUIRED_CLAUSES: Final[tuple[str, ...]] = (
    "age",
    "employment_status",
    "employment_duration",
    "working_hours",
    "salary",
    "forced_labour",
    "discrimination",
    "freedom_of_association",
)

# Collected and shown, but not gating. Training is an employer-comparison axis
# (the employer reports a participation count), not a condition of a good job.
RECORDED_CLAUSES: Final[tuple[str, ...]] = ("training_participation",)

# Verdict precedence, highest first. Evaluated in this order so that a known
# failure outranks ambiguity: if a required condition is established as not
# met, that is a determination, not missing evidence.
VERDICT_PRECEDENCE: Final[tuple[str, ...]] = (
    "STOPPED",  # age below minimum_age; safeguarding flag, excluded from counts
    "NOT_CONFIRMED",  # any required clause NOT_MET
    "UNCLEAR",  # any required clause UNCLEAR / REFUSED / NOT_ASKED
    "CONFIRMED_GOOD_JOB",  # every required clause MET
)

# States the extraction step may report for a single fact. This vocabulary is
# deliberately free of thresholds and verdicts: the model reports what was
# said, the rule engine decides what it means.
FACT_STATES: Final[tuple[str, ...]] = (
    "STATED",  # a usable value was established
    "REFUSED",  # the respondent declined to answer
    "VAGUE",  # answered, but not resolvable to a value after follow-ups
    "NOT_ASKED",  # never reached, including after a stopped interview
)
