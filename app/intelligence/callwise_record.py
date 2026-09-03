"""Build the Callwise section-6 output record from one extracted interview.

The interview is parsed once, by the model, in
:meth:`app.intelligence.batch_engine.BatchIntelligence.extract_answers`. It
returns each answer twice: the respondent's own words, and the typed value those
words resolve to. :mod:`app.intelligence.callwise_rules` compares the typed
values against the thresholds. This module is the last step: it arranges that
result into the record the pilot specification asks for and ImpactProtocol
imports.

Nothing here reads the respondent's words to decide anything, and that is the
point rather than an omission. The answers are Amharic. A keyword list would
have to know that "የራሴ አይደለም" contains the word for "my own" and means its
opposite, that "አላቋረጥኩም" is grammatically negative and affirms continuity, and
that "13 ሺ" is thirteen thousand rather than thirteen. Those are readings of a
language, so they are asked of the model, and this module consumes the typed
result and the closed vocabularies that :data:`app.intelligence.questions.
NORMALIZED_FIELDS` declares.

What is left is arithmetic, and two properties of it are deliberate:

* **No model call, and no judgement.** ``counted`` is a conjunction over nine
  clause statuses. Criterion 5 makes a single false ``met`` a hard fail of the
  whole pilot, so an unresolved field resolves to ``unclear`` -- never to ``met``
  and never to ``not_met``. A record that leaves a clause open is worth more to
  the programme than one that guesses it shut.
* **Evidence points at a turn, not at prose.** Criterion 4 has a bilingual
  reviewer hand-check twenty calls, and several answers here are the single word
  "አዎ", so searching the transcript for a quoted fragment cannot tell the fourth
  "yes" from the first. The model cites the numbered line it copied from; this
  module checks that citation against the turn array and drops it if it does not
  hold, because a wrong turn number is worse than none.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

#: The nine clauses of the output record, in specification order.
CLAUSES: tuple[str, ...] = (
    "age_ok",
    "hours_ok",
    "tenure_ok",
    "wage_ok",
    "no_child_labour",
    "no_forced_labour",
    "no_discrimination",
    "association_ok",
    "seasonal_over_6m",
)

#: Section 6 admits three statuses. The internal engine additionally
#: distinguishes a refusal from a question never asked; both are *open* rather
#: than failed, so both land on ``unclear`` here. Collapsing them loses no
#: information that matters downstream: the answer states are stored separately.
_STATUS = {
    "MET": "met",
    "NOT_MET": "not_met",
    "UNCLEAR": "unclear",
    "REFUSED": "unclear",
    "NOT_ASKED": "unclear",
    "STOPPED": "not_met",
}

#: Section 6 wants a number, extraction reports a level. These are the midpoints
#: of what each level means, not a calibration: a reviewer comparing twenty
#: records needs them ordered and stable, not precise.
_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}

#: ``counted`` requires every clause met. Anything still open is counted so the
#: administrator can see how far a record is from resolvable, which is why a
#: ``not_met`` does not count as unresolved: it is a determination, not a gap.
_UNRESOLVED = "unclear"
_SUMMARY_LIMIT = 500

#: Answers that can serve as a quote. Both are open questions the respondent
#: answered in their own words, which is what a quote is for. The rest of the
#: questionnaire is yes/no and numbers: "አዎ" is not testimony, and carrying every
#: answer here would rebuild the transcript inside a record that is meant to
#: summarize it. Consent to be quoted is not consent to be reprinted.
_QUOTABLE: tuple[str, ...] = ("skills_used", "other_changes")
#: prompt forbids quoting the interviewer, and enforcing it here means a prompt
#: regression shows up as a missing turn rather than as the agent's own words
#: presented to a reviewer as worker evidence.
_RESPONDENT_ROLES = frozenset({"caller", "respondent", "worker", "user"})

_WHITESPACE = re.compile(r"\s+")


def _text(value: Any) -> str:
    """A comparable rendering of a transcript line, for matching lines only.

    Used to compare one string against another when checking a cited turn. It
    never interprets what the string says.
    """

    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip().lower()


def _entry(answers: dict[str, Any], slug: str) -> dict[str, Any]:
    """One answer entry, or an empty one."""

    entry = answers.get(slug)
    return entry if isinstance(entry, dict) else {}


def _typed(answers: dict[str, Any], slug: str) -> Any:
    """The model's typed reading of one answer, or ``None``.

    ``None`` covers three cases that are the same case downstream: the question
    was not answered, the answer could not be resolved without guessing, and the
    resolved value failed the vocabulary check in
    :func:`app.intelligence.questions.coerce_normalized`. All three mean the
    field is not established, and none of them may become a ``met``.
    """

    entry = _entry(answers, slug)
    return entry.get("normalized") if entry.get("state") == "STATED" else None


def _typed_key(answers: dict[str, Any], slug: str, key: str) -> Any:
    """One key of an object-valued typed answer, such as ``amount_etb``."""

    normalized = _typed(answers, slug)
    return normalized.get(key) if isinstance(normalized, dict) else None


def _integer(value: Any) -> int | None:
    """A stored integer for a field the record types as a whole number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


# -- turns ------------------------------------------------------------------ #


def _respondent_turns(turns: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """The respondent's lines, with the 1-based positions the model was shown."""

    return [
        (index, _text(turn.get("text")))
        for index, turn in enumerate(turns, 1)
        if isinstance(turn, dict)
        and turn.get("role") in _RESPONDENT_ROLES
        and _text(turn.get("text"))
    ]


def resolve_turn(entry: dict[str, Any], turns: list[dict[str, Any]]) -> int | None:
    """The transcript line this answer rests on, if it can be trusted.

    The model cites the number printed on the line it copied. That citation is
    accepted only when it survives three checks: the line exists, it is the
    respondent speaking, and it contains the evidence the model says it copied.
    Models do miscount, and an index that points a reviewer at the wrong line
    would make a correct record look fabricated.

    When the citation fails, the evidence text is matched against the respondent's
    lines instead, and only an unambiguous single match is used. Ambiguity gives
    up: this questionnaire has six answers that are the bare word "አዎ", and
    offering the first of them as evidence for the fourth question would be a
    fabricated citation, which is worse than an honest blank.
    """

    evidence = _text(entry.get("evidence"))
    if not evidence:
        return None
    candidates = _respondent_turns(turns)
    by_index = dict(candidates)

    claimed = entry.get("evidence_turn")
    if isinstance(claimed, int) and not isinstance(claimed, bool):
        line = by_index.get(claimed)
        if line and (evidence in line or line in evidence):
            return claimed

    exact = [index for index, line in candidates if line == evidence]
    if len(exact) == 1:
        return exact[0]
    contains = [index for index, line in candidates if evidence in line or line in evidence]
    return contains[0] if len(contains) == 1 else None


# -- clauses ---------------------------------------------------------------- #


def _clause(status: str, confidence: str | None, turn: int | None) -> dict[str, Any]:
    """One clause in section-6 form.

    ``source`` is "worker" only when a respondent turn actually backs the status.
    A clause with no turn behind it reports "none", which is what tells a reviewer
    the difference between an answered "no" and an unanswered question.
    """

    resolved = _STATUS.get(str(status).upper(), "unclear")
    grounded = turn is not None and resolved != "unclear"
    return {
        "status": resolved,
        "confidence": _CONFIDENCE.get(str(confidence or "LOW").upper(), 0.3) if grounded else 0.3,
        "evidence_turn": turn if grounded else None,
        "source": "worker" if grounded else "none",
    }


def _seasonal_clause(answers: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """``seasonal_over_6m``, from the continuity answer.

    The specification asks question 6 of daily, seasonal and gig workers only, but
    the record carries all nine clauses for every call. Where the respondent
    states the work ran without a break, continuity is established and the clause
    is met. Where the question was skipped or the answer did not resolve, it stays
    open rather than being waved through on the grounds that it was not really
    meant for this person.
    """

    entry = _entry(answers, "employment_continuity")
    continuous = _typed(answers, "employment_continuity")
    if not isinstance(continuous, bool):
        return _clause("UNCLEAR", entry.get("confidence"), None)
    return _clause(
        "MET" if continuous else "NOT_MET", entry.get("confidence"), resolve_turn(entry, turns)
    )


def build_clauses(
    kpi: dict[str, Any], answers: dict[str, Any], turns: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Translate the internal clause result into the record's nine clauses.

    The engine reports eight clauses under its own names; section 6 asks for
    nine. Two differences are not renames. ``age`` backs both ``age_ok`` and
    ``no_child_labour``, because one stated age answers both questions.
    ``employment_status`` backs no section-6 clause at all -- it routes the
    questionnaire rather than qualifying the job -- and ``seasonal_over_6m`` has
    no engine equivalent, so it comes from the continuity answer.
    """

    internal = kpi.get("clauses") or {}

    def translate(name: str, slug: str) -> dict[str, Any]:
        clause = internal.get(name) or {}
        return _clause(
            clause.get("status", "UNCLEAR"),
            clause.get("confidence"),
            resolve_turn(_entry(answers, slug), turns),
        )

    age = internal.get("age") or {}
    age_turn = resolve_turn(_entry(answers, "age_years"), turns)
    # A safeguarding stop is the one place a clause is failed without an answer
    # behind it: the record must never report a child's labour as unresolved.
    child_status = "NOT_MET" if kpi.get("safeguarding_flag") else age.get("status", "UNCLEAR")

    clauses = {
        "age_ok": _clause(age.get("status", "UNCLEAR"), age.get("confidence"), age_turn),
        "hours_ok": translate("working_hours", "working_hours"),
        "tenure_ok": translate("employment_duration", "start_date"),
        "wage_ok": translate("salary", "monthly_pay"),
        "no_child_labour": _clause(child_status, age.get("confidence"), age_turn),
        "no_forced_labour": translate("forced_labour", "freedom_to_leave"),
        "no_discrimination": translate("discrimination", "equal_treatment"),
        "association_ok": translate("freedom_of_association", "worker_representation"),
        "seasonal_over_6m": _seasonal_clause(answers, turns),
    }
    return {name: clauses[name] for name in CLAUSES}


# -- blocks ----------------------------------------------------------------- #


def build_employment(answers: dict[str, Any], kpi: dict[str, Any]) -> dict[str, Any]:
    """The employment block, from the model's typed answers.

    ``hours_per_week`` and ``months_since_start`` are read back off the clause
    engine rather than taken from the answers again, so the number in the record
    and the number its clause was decided on cannot disagree.
    """

    internal = kpi.get("clauses") or {}
    status = _typed(answers, "employment_status") or "unknown"
    kind = _typed(answers, "employment_type")
    if status != "working":
        # Nobody who is not working has an employment type, and "not_working" is
        # a positive statement about that where an unestablished status is not.
        kind = "none" if status == "not_working" else None

    return {
        "status": status,
        "type": kind,
        "sales_related": _typed(answers, "sales_related"),
        "employer_name": None,
        "start_date": _typed(answers, "start_date"),
        "months_since_start": _integer((internal.get("employment_duration") or {}).get("value")),
        "hours_per_week": _integer((internal.get("working_hours") or {}).get("value")),
        "weeks_per_year": None,
        "monthly_take_home_etb": _integer(_typed_key(answers, "monthly_pay", "amount_etb")),
        "deductions_reported": _typed_key(answers, "monthly_pay", "deductions_reported"),
        "continuous": _typed(answers, "employment_continuity"),
    }


def build_training(answers: dict[str, Any]) -> dict[str, Any]:
    """Zenawi's intents Z2 to Z6, as stated."""

    return {
        "months_to_first_placement": _integer(_typed(answers, "time_to_first_work")),
        "training_helped_placement": _typed(answers, "training_help"),
        "satisfaction_1_5": _integer(_typed(answers, "satisfaction")),
        "skills_used": _typed(answers, "skills_used"),
        "other_changes": _typed(answers, "other_changes"),
    }


def build_call(
    turns: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    attempts: int = 1,
    disposition: str = "completed",
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """The call block. Duration comes from the transcript, cost from telephony."""

    offsets = [
        float(turn["offset_seconds"])
        for turn in turns
        if isinstance(turn, dict) and isinstance(turn.get("offset_seconds"), (int, float))
    ]
    detected = payload.get("detected_languages")
    detected = detected if isinstance(detected, list) else []
    return {
        "attempts": attempts,
        "disposition": disposition,
        "duration_seconds": int(round(max(offsets))) if offsets else None,
        "language_switched": len({str(code).split("-")[0] for code in detected}) > 1,
        "cost_usd": cost_usd,
    }


def build_flags(
    extraction: dict[str, Any], call: dict[str, Any], *, cohort_size: int | None = None
) -> list[str]:
    """The record's flags, in specification order.

    ``wage_complaint`` is reported by the extraction rather than found here. It
    is an escalation for the programme office -- the agent is told to raise it and
    stop that line of questioning -- and recognising a complaint about withheld
    pay in Amharic is exactly the reading that belongs with the model.
    """

    flags: list[str] = []
    if extraction.get("safeguarding_flag") or extraction.get("stop_reason") == "UNDER_MINIMUM_AGE":
        flags.append("under_age_stop")
    if (extraction.get("consent_details") or {}).get("state") in {"declined", "voided"}:
        flags.append("consent_withdrawn")
    if extraction.get("wage_complaint"):
        flags.append("wage_complaint")
    if cohort_size is not None and cohort_size < 5:
        flags.append("small_cell_risk")
    if call.get("language_switched") or extraction.get("language") == "en":
        flags.append("language_fallback_used")
    return flags


def build_summary(extraction: dict[str, Any], clauses: dict[str, dict[str, Any]]) -> str:
    """The model's English summary of the work, with the open clauses appended.

    The prose is written by the model, in :mod:`app.intelligence.questions`, and
    checked for shape there. It has to be: the interview is Amharic, and the one
    reader of it is the model. Assembling English here from the typed fields would
    produce the same clause in the same order for every call, which is a form
    letter and not a summary of anything.

    The tail is code's, for the same reason the prose is not: clause statuses are
    decided in this module against thresholds the model is never shown, so it
    cannot say which of them are still open. Naming them is what makes the summary
    honest to a reviewer -- a record that reads as complete while resting on two
    unresolved clauses is the kind of overstatement Criterion 5 fails a pilot for.
    """

    text = extraction.get("summary_en")
    text = text.strip() if isinstance(text, str) else ""
    open_clauses = [name for name, clause in clauses.items() if clause["status"] == _UNRESOLVED]
    if open_clauses:
        tail = f"Unresolved: {', '.join(open_clauses)}."
        text = f"{text} {tail}" if text else tail
    return text[:_SUMMARY_LIMIT]


# -- record ----------------------------------------------------------------- #


def build_record(
    extraction: dict[str, Any],
    kpi: dict[str, Any],
    payload: dict[str, Any],
    *,
    record_id: str,
    beneficiary_id: str,
    training_cohort_id: str | None = None,
    interview_date: date | str | None = None,
    attempts: int = 1,
    disposition: str = "completed",
    cost_usd: float | None = None,
    age_band: str | None = None,
    gender: str | None = None,
    cohort_size: int | None = None,
) -> dict[str, Any]:
    """Assemble one section-6 record.

    ``extraction`` is the normalized, safeguarded result from
    :meth:`app.intelligence.batch_engine.BatchIntelligence.extract_answers`;
    ``kpi`` is :func:`app.intelligence.callwise_rules.evaluate` over its answers;
    ``payload`` is the raw provider transcript document, needed for the turn array
    and the detected languages.
    """

    turns = payload.get("turns") if isinstance(payload, dict) else None
    turns = turns if isinstance(turns, list) else []
    answers = extraction.get("answers") or {}
    consent = extraction.get("consent_details") or {}

    clauses = build_clauses(kpi, answers, turns)
    employment = build_employment(answers, kpi)
    training = build_training(answers)
    call = build_call(turns, payload, attempts=attempts, disposition=disposition, cost_usd=cost_usd)

    # A decline and a voided call both carry the call block, an empty employment
    # block and every clause unclear. Rebuilding them here rather than trusting
    # the extraction to have blanked them keeps that promise in one place, where
    # it can be tested. A safeguarding stop is not in this set: its clauses are
    # already failed rather than open, and blanking them would hide the reason.
    withheld = consent.get("state") in {"declined", "voided"}
    if withheld:
        clauses = {name: _clause("UNCLEAR", "LOW", None) for name in CLAUSES}
        employment = dict.fromkeys(employment)
        employment["status"] = "unknown"
        training = dict.fromkeys(training)

    flags = build_flags(extraction, call, cohort_size=cohort_size)
    statuses = [clause["status"] for clause in clauses.values()]

    quotes: list[dict[str, str]] = []
    if consent.get("quote") and not withheld:
        for slug in _QUOTABLE:
            evidence = _entry(answers, slug).get("evidence")
            if isinstance(evidence, str) and evidence.strip():
                quotes.append({"lang": extraction.get("language") or "am", "text": evidence.strip()})

    if isinstance(interview_date, date):
        interview_date = interview_date.isoformat()

    return {
        "record_id": record_id,
        "beneficiary_id": beneficiary_id,
        "training_cohort_id": training_cohort_id,
        "language": extraction.get("language") or "am",
        "channel": "voice",
        "interview_date": interview_date,
        "call": call,
        "consent": {
            "state": consent.get("state", "declined"),
            "name": bool(consent.get("name", False)),
            "quote": bool(consent.get("quote", False)),
            "voice": bool(consent.get("voice", False)),
            "photo": False,
            "voided_at_turn": consent.get("voided_at_turn"),
            "vulnerable_group_script": bool(consent.get("vulnerable_group_script", False)),
        },
        "employment": employment,
        "clauses": clauses,
        "counted": all(status == "met" for status in statuses),
        "unresolved_clause_count": statuses.count(_UNRESOLVED),
        "training": training,
        "aggregation_key": None
        if withheld
        else ({"age_band": age_band, "gender": gender} if age_band or gender else None),
        "quotes": quotes,
        # A withheld call keeps no summary of the work either: the prose was
        # written from answers the respondent asked us not to keep.
        "summary_en": build_summary({} if withheld else extraction, clauses),
        "flags": flags,
    }


__all__ = [
    "CLAUSES",
    "build_call",
    "build_clauses",
    "build_employment",
    "build_flags",
    "build_record",
    "build_summary",
    "build_training",
    "resolve_turn",
]
