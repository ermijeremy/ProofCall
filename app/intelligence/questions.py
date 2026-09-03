"""Turn the questions an admin typed into an interview and an extraction schema.

The clause path in :mod:`app.intelligence.prompts` asks a fixed set of fourteen
facts and thresholds them. A batch asks whatever the admin typed, which carries
no pass condition, so there is nothing to threshold. What is left is to ask the
questions faithfully and report the answers faithfully.

Two things are ours rather than the admin's, and they come first in every
interview regardless of what was typed:

* **Consent.** Nobody is interviewed who has not agreed to be.
* **Age.** It is asked first, and an answer meaning the respondent is a child
  ends the call immediately. Code cannot judge that without a threshold, and the
  product decision was that no thresholds live in code, so the extraction model
  reports a label in a closed set and :mod:`app.intelligence.safeguarding` acts
  on it.

The no-threshold rule still binds everything we write. It does not bind the
admin's own question text, which is passed through verbatim: if they choose to
ask "do you work more than eight hours?", that is their question to ask.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.intelligence.prompts import LANGUAGE_NAMES, _language_name, _worker_field
from app.intelligence.providers.base import ProviderError

logger = logging.getLogger(__name__)

#: Reserved slug for the age question, which every batch asks first.
AGE_SLUG = "age_years"

#: The age question, in our words, prepended to every question set.
AGE_QUESTION_TEXT = "How old are you?"

#: Labels the extraction may return for age. A closed set, so code can act on it.
AGE_ASSESSMENTS = ("CHILD", "ADULT", "UNKNOWN")

# The Callwise pilot has only two supported spoken languages. A CSV value or
# inherited company setting must never make the voice agent speak a third one.
SUPPORTED_SPOKEN_LANGUAGES = {"am", "en"}

#: States an answer may be in. Same vocabulary as the clause path, so the
#: dashboard and the chat describe a refusal the same way.
ANSWER_STATES = ("STATED", "REFUSED", "VAGUE", "NOT_ASKED")

# A concrete record shape for the extraction model to imitate. The model must
# still return the smaller question-driven shape below; the good-job decision
# is deliberately not delegated to it and is added by deterministic code.
CALLWISE_RECORD_EXAMPLE = r'''{
  "record_id": "CW-014",
  "beneficiary_id": "BSG-2026-0143",
  "training_cohort_id": "COH-2026-04",
  "language": "am",
  "channel": "voice",
  "interview_date": "2026-09-18",
  "call": {
    "attempts": 2,
    "disposition": "completed",
    "duration_seconds": 331,
    "language_switched": false,
    "cost_usd": 0.28
  },
  "consent": {
    "state": "granted_no_name",
    "name": false,
    "quote": true,
    "voice": false,
    "photo": false,
    "voided_at_turn": null,
    "vulnerable_group_script": false
  },
  "employment": {
    "status": "working",
    "type": "employee",
    "sales_related": true,
    "employer_name": null,
    "start_date": "2026-04-01",
    "months_since_start": 5,
    "hours_per_week": 44,
    "weeks_per_year": 52,
    "monthly_take_home_etb": 5200,
    "deductions_reported": "transport, 300 birr",
    "continuous": true
  },
  "clauses": {
    "age_ok": {"status": "met", "confidence": 0.9, "evidence_turn": 4, "source": "worker"},
    "hours_ok": {"status": "met", "confidence": 0.8, "evidence_turn": 12, "source": "worker"},
    "tenure_ok": {"status": "met", "confidence": 0.75, "evidence_turn": 10, "source": "worker"},
    "wage_ok": {"status": "met", "confidence": 0.7, "evidence_turn": 14, "source": "worker"},
    "no_child_labour": {"status": "met", "confidence": 0.9, "evidence_turn": 4, "source": "worker"},
    "no_forced_labour": {"status": "met", "confidence": 0.8, "evidence_turn": 16, "source": "worker"},
    "no_discrimination": {"status": "unclear", "confidence": 0.3, "evidence_turn": null, "source": "none"},
    "association_ok": {"status": "not_met", "confidence": 0.7, "evidence_turn": 18, "source": "worker"},
    "seasonal_over_6m": {"status": "met", "confidence": 0.6, "evidence_turn": 10, "source": "worker"}
  },
  "counted": false,
  "unresolved_clause_count": 1,
  "training": {
    "months_to_first_placement": 2,
    "training_helped_placement": "a_lot",
    "satisfaction_1_5": 4,
    "skills_used": "handling a customer who says no",
    "other_changes": "pays her own rent since June"
  },
  "aggregation_key": {"age_band": "25+", "gender": "F"},
  "quotes": [{"lang": "am", "text": "..."}],
  "summary_en": "Employed in a shop since April 2026, sales role, 44 hours a week, 5,200 birr take-home after a transport deduction. No worker representation. Credits the training with the placement.",
  "flags": ["small_cell_risk"]
}'''

# The Callwise pilot questionnaire is fixed.  These are deliberately stored as
# data rather than reconstructed by the chat model so every batch has the same
# KPI meaning and the exported columns remain stable.
FIXED_QUESTIONNAIRE: tuple[dict[str, str], ...] = (
    {"slug": "age_years", "text": "How old are you?", "topic": "age"},
    {"slug": "employment_status", "text": "Are you working at the moment, in any kind of work, paid by someone or on your own?", "topic": "employment status"},
    {"slug": "sales_related", "text": "Is that work in sales or dealing with customers?", "topic": "sales work"},
    {"slug": "employment_type", "text": "Who pays you: a company, your own business, or is it day by day or by season?", "topic": "employment type"},
    {"slug": "start_date", "text": "Which month and year did you start this work?", "topic": "start date"},
    {"slug": "employment_continuity", "text": "Since you started, has it run without a break, or were there times with no work?", "topic": "continuity"},
    {"slug": "working_hours", "text": "In a normal week, how many days do you work, and about how many hours on a day?", "topic": "working hours"},
    {"slug": "monthly_pay", "text": "In a normal month, how much do you take home? Is anything taken off before you get it?", "topic": "monthly pay"},
    {"slug": "freedom_to_leave", "text": "Are you free to leave this work whenever you want, with nothing owed and nobody holding your papers?", "topic": "freedom to leave"},
    {"slug": "equal_treatment", "text": "Are you paid and treated the same as other people doing the same work there?", "topic": "equal treatment"},
    {"slug": "worker_representation", "text": "Can workers raise a problem together, and is there someone who speaks for them?", "topic": "worker representation"},
    {"slug": "time_to_first_work", "text": "After the training ended, how long was it until your first work?", "topic": "time to work"},
    {"slug": "training_help", "text": "How much did the training help you get that work: a lot, some, a little, or not at all?", "topic": "training contribution"},
    {"slug": "skills_used", "text": "Which part of the training do you use most in your work today?", "topic": "skills used"},
    {"slug": "satisfaction", "text": "From 1 to 5, how satisfied are you with the training?", "topic": "satisfaction"},
    {"slug": "other_changes", "text": "What else has changed for you since the training?", "topic": "other changes"},
)


def fixed_question_set() -> list[dict[str, Any]]:
    """Return a fresh, ordered copy of the pilot KPI questionnaire."""

    return [
        {"index": index, **question, "original": question["text"]}
        for index, question in enumerate(FIXED_QUESTIONNAIRE)
    ]

# Amharic suffixes attach to the noun ("ዕድሜዎ" is "your age"), so a trailing word
# boundary would only ever match the bare stem. The Latin alternatives keep their
# boundaries so "average" and "umrika" do not count as age questions.
_AGE_PATTERN = re.compile(r"\b(age|how old|umri)\b|ዕድሜ", re.IGNORECASE)
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "question") -> str:
    """Make a JSON field name out of a question.

    Keeps the first few words so a reader of the raw payload can still tell which
    question a field belongs to. Non-Latin scripts leave nothing to keep, so
    those fall back to the positional name the caller supplies.
    """

    words = [word for word in _SLUG_STRIP.sub(" ", text.lower()).split() if word]
    slug = "_".join(words[:6]).strip("_")
    return slug or fallback


def is_age_question(text: str) -> bool:
    """True when the admin already asked about age, so we do not ask twice."""

    return bool(_AGE_PATTERN.search(text))


def parse_questions(text: str) -> list[str]:
    """Split a typed message into questions.

    Admins number them ("1 - what is ur age?"), bullet them, or put one per line.
    All three land here, and the numbering is stripped so it does not end up read
    aloud to the respondent.
    """

    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    parts: list[str] = []
    for line in lines:
        if not line:
            continue
        # "1 - text", "1. text", "1) text", "- text", "* text"
        cleaned = re.sub(r"^\s*(?:\d+\s*[-.)\]:]|[-*•])\s*", "", line).strip()
        if cleaned:
            # Admins often type a natural instruction instead of a numbered
            # list (for example, "ask about age and salary"). Keep the
            # question-set contract one question at a time for the voice agent.
            command = re.search(r"\b(?:ask(?:\s+him|\s+her|\s+them)?\s+about|questions?\s+about|ask)\s+(.+)$", cleaned, re.IGNORECASE)
            candidate = command.group(1).strip() if command else cleaned
            if re.search(r"\bage\b", candidate, re.IGNORECASE) and re.search(
                r"\b(?:salary|pay|wage|compensation|income)\b", candidate, re.IGNORECASE
            ):
                split = re.split(r"\s+and\s+", candidate, maxsplit=1, flags=re.IGNORECASE)
                if len(split) == 2:
                    parts.extend(piece.strip().rstrip("?.") + "?" for piece in split if piece.strip())
                    continue
            parts.append(candidate)
    if len(parts) == 1:
        # A single line may still hold several numbered questions.
        inline = re.split(r"(?:^|\s)\d+\s*[-.)\]:]\s*", parts[0])
        inline = [piece.strip() for piece in inline if piece.strip()]
        if len(inline) > 1:
            return inline
    return parts


def build_refinement_prompt() -> str:
    """System instructions for rewriting typed questions into askable ones."""

    return """You rewrite an administrator's typed questions so they can be read aloud
down a telephone to an employee. The administrator types quickly, in shorthand,
sometimes mid-sentence. The respondent hears only what you produce.

Return JSON only, with exactly this shape:

{"questions": [{"original": "...", "text": "...", "topic": "..."}]}

RULES
Produce exactly one entry per question you were given, in the order given. Never
merge two questions into one, never split one into two, and never add a question
the administrator did not ask.
"text" is the question as it should be spoken: a whole, polite, unambiguous
sentence. Expand shorthand ("ur" is "your", "u" is "you"), fix spelling, and make
it a direct question.
Do not change what is being asked. "how much do they pay u" asks about pay, and
must not become a question about whether the pay is fair, whether it arrives on
time, or how the respondent feels about it. If the typed question is vague, keep
it vague rather than inventing the missing detail.
Ask for one thing per question. If the typed question genuinely asks for two
things, keep the administrator's first thing and leave the second alone.
Never introduce a leading question, a suggested answer, or a judgement.
"topic" is two or three words naming the subject, lowercase, for labelling
counts later: "monthly pay", "training", "working hours".
Keep the respondent's dignity: no question implies they have done anything wrong.
"""


def normalize_refinement(
    payload: dict[str, Any],
    raw: list[str],
) -> list[dict[str, str]]:
    """Accept a refinement only if it still asks the administrator's questions.

    The count and the order are the contract. A response with a different number
    of questions has either merged two or invented one, and both change what the
    respondent is asked, so the whole refinement is discarded in favour of the
    typed text. Rewriting is a convenience; asking what the administrator asked
    is not.
    """

    entries = payload.get("questions")
    if not isinstance(entries, list) or len(entries) != len(raw):
        return [{"original": text, "text": text, "topic": ""} for text in raw]

    refined: list[dict[str, str]] = []
    for original, entry in zip(raw, entries):
        if not isinstance(entry, dict):
            refined.append({"original": original, "text": original, "topic": ""})
            continue
        text = entry.get("text")
        text = text.strip() if isinstance(text, str) and text.strip() else original
        topic = entry.get("topic")
        refined.append(
            {
                "original": original,
                "text": text,
                "topic": topic.strip().lower() if isinstance(topic, str) else "",
            }
        )
    return refined


def refine_questions(provider: Any, raw: list[str]) -> list[dict[str, str]]:
    """Rewrite typed questions into askable ones, or keep them exactly as typed.

    A provider failure is not allowed to stop a batch: the typed text is used
    unchanged, which is what the old behaviour was for every question.
    """

    texts = [text.strip() for text in raw if text and text.strip()]
    if not texts:
        return []
    numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(texts, start=1))
    try:
        payload = provider.complete_json(
            system=build_refinement_prompt(),
            user=f"Questions as typed:\n\n{numbered}",
        )
    except ProviderError:
        logger.exception("Question refinement failed; using the questions as typed")
        return [{"original": text, "text": text, "topic": ""} for text in texts]
    return normalize_refinement(payload, texts)


def build_question_set(texts: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
    """Number and slug the questions, with age first and never duplicated.

    Takes either plain typed strings or the ``{"original", "text", "topic"}``
    entries from :func:`refine_questions`. The slug is derived from the *topic*
    when one is present and from the question text otherwise, so a field name
    stays short and readable — ``monthly_pay`` rather than
    ``how_much_are_you_paid_each_month``.
    """

    admin: list[dict[str, Any]] = []
    for entry in texts:
        if isinstance(entry, dict):
            text = (entry.get("text") or entry.get("original") or "").strip()
            if text:
                admin.append(
                    {
                        "text": text,
                        "original": (entry.get("original") or text).strip(),
                        "topic": (entry.get("topic") or "").strip(),
                    }
                )
        elif entry and entry.strip():
            admin.append({"text": entry.strip(), "original": entry.strip(), "topic": ""})

    age_entry = {"text": AGE_QUESTION_TEXT, "original": AGE_QUESTION_TEXT, "topic": "age"}
    ordered = [age_entry] + [item for item in admin if not is_age_question(item["text"])]

    questions: list[dict[str, Any]] = []
    used = {AGE_SLUG}
    for index, item in enumerate(ordered):
        if index == 0:
            questions.append(
                {"index": 0, "text": item["text"], "slug": AGE_SLUG, "original": item["original"], "topic": "age"}
            )
            continue
        # Keep the field name stable for the batch and for later answer records.
        # A model-supplied topic is descriptive metadata, not an identifier:
        # changing it would make the same admin question address a different
        # column between model responses.
        source = item["original"] or item["text"]
        slug = slugify(source, fallback=f"question_{index}")
        if slug in used:
            slug = f"{slug}_{index}"
        used.add(slug)
        questions.append(
            {
                "index": index,
                "text": item["text"],
                "slug": slug,
                "original": item["original"],
                "topic": item["topic"],
            }
        )
    return questions


def build_interview_prompt(
    worker: Any,
    questions: list[dict[str, Any]],
    *,
    language: str = "am",
    programme_name: str = "an internal workforce review",
) -> str:
    """Operating instructions for the voice agent conducting one batch call.

    The worker's own ``preferred_language`` wins over the batch default, because
    the batch language is a fallback for the cohort while the record is about
    this person.
    """

    worker_id = _worker_field(worker, "worker_id", "unknown")
    spoken = _worker_field(worker, "preferred_language") or language
    if str(spoken).lower() not in SUPPORTED_SPOKEN_LANGUAGES:
        spoken = "am"
    language_name = _language_name(spoken)
    # Bulleted, not numbered: a bare number in the prompt is indistinguishable
    # from a threshold to the test that guards against leaking one, and the order
    # is already carried by the list itself.
    listed = "\n".join(
        f"  - {question['text']}"
        for question in questions
        if question["slug"] != AGE_SLUG
    )

    return f"""You are conducting a short, voluntary telephone interview for
{programme_name}. Interview reference: {worker_id}.

LANGUAGE CHECK — THIS MUST BE THE FIRST SPOKEN TURN
Do not greet, explain the study, mention the training, or ask for consent before
this sentence. Say exactly:
  ሰላም። አማርኛ ወይስ English? Amharic or English, whichever is easier for you.
Then wait for the respondent's choice. If they choose Amharic, speak Amharic
for every remaining spoken turn. If they choose English, speak English for
every remaining spoken turn. The planned language ({language_name}) is only a
fallback when the choice cannot be understood; it is never permission to use a
third language.

LANGUAGE SAFETY
You may speak only Amharic or English. Do not repeat, translate, or answer in
Oromo, Tigrinya, Somali, Arabic, French, Portuguese, Korean, Hindi, or any other
language, even if the speech recognizer produces words that look like one of
those languages. If the choice is unclear, repeat the language-choice sentence
once, in the same Amharic-and-English form, then use the understood choice.
Never infer a language from a noisy or unrelated caller answer.

WHO YOU ARE
After the language choice, say, in your own words:
  - you are an independent team calling about the beSingularity training;
  - the call takes about five minutes and does not affect training or employment;
  - what they say will not be reported back against their name.
Do not ask for their name.

SPOKEN CONSENT — BEFORE ANY QUESTION
After the language choice, speak the consent items in that chosen language.
Translate the meaning naturally; do not read both language versions. Say each
item separately, then ask the final question and wait:
  One. I do not need your name. I can write your answers without it.
  Two. You can stop at any time. Say stop and I stop. Say do not record this and I
  delete this whole call, including what you already told me.
  Three. You can answer some questions and leave others. Saying nothing to a
  question is a normal answer.
  May I begin?
Treat silence or an unclear reply as no consent. If they say no, thank them and
end the call. Never persuade them.

FIRST SUBSTANTIVE QUESTION — ONLY AFTER CONSENT
Do not ask age, employment, salary, training, or any other interview question
until the respondent has clearly answered yes to "May I begin?". Ask age as the
first substantive question after consent, and wait for the answer. Ask for
consent only once; never repeat the consent request after age or any other
question.
If the answer means they are still a child, do not ask a single further
question. Thank them, say you have nothing more to ask, wish them well, and end
the call. Do not explain why you are stopping, do not mention any age rule, and
do not tell them their answer was a problem. Saying so could put them under
pressure from their employer afterwards.

THE QUESTIONS
Once consent is granted and age is settled and the person is not a child, ask the
fixed Callwise questionnaire in order. Ask exactly one question, wait for its
answer, then ask the next. Do not add, remove, merge, or reorder questions:
{listed}

HOW TO ASK
Ask one thing at a time, in plain spoken language, and let them finish.
When the respondent gives a clear answer, do not comment on it, repeat it,
confirm it, praise it, or say thank you. Move directly to the next question.
The required rhythm is: ask one question -> wait for the answer -> ask the
next question. For example, do not say "Thank you, you are twenty-one" after asking
their age; simply continue with the next question in the selected language.
After every question, wait silently for up to three seconds. If there is no
answer, say in the selected language: "If you do not want to answer this
question, we can continue to the next question." In Amharic say:
"ይህን ጥያቄ መመለስ ካልፈለጉ ወደሚቀጥለው ጥያቄ መሄድ እንችላለን።"
Wait silently for up to three more seconds. If there is still no answer, record
NOT_ASKED and continue. Never treat silence as consent, agreement, refusal, or
evidence, and never fill a silent answer from context.
Ask for their own experience, never for the workplace in general.
Never read a number back to them as if you already knew it, and never suggest
what a good answer would be.
Never say that an answer is a minimum, a target, a requirement, or a qualifying
level, and never say whether an answer sounds good or bad. You are recording
what they say, not judging it.

NON-WORKING BRANCH
If the person says they are not working or are searching, do not ask the
working-condition questions. Ask these three instead, one at a time: what
happened after the training; whether anyone from beSingularity or a company
contacted them; and what would have needed to be different for them to be
working now. Then continue with the training and satisfaction questions.

TIME CONTROL
Keep the complete call under six minutes. If time is running short, omit the
last question, then the sales-work question, then the time-to-work question, and
record each omitted answer as NOT_ASKED. Never omit the questions about freedom
to leave, equal treatment, or workers raising problems.

WHEN AN ANSWER IS VAGUE
Ask once more, in different words. If it is still not clear, do not guess on
their behalf and do not offer them a value to agree with; move to the next
question without evaluating the answer.

WHEN SOMEONE WILL NOT ANSWER
If they decline a question, especially about pay, accept it immediately and
move to the next one. Never ask twice, never explain why you need it, and never
propose an answer for them to confirm.

TONE
Warm, unhurried, and plain. No jargon. No opinions about their employer. After
the final available question, say exactly:

Thank you. I wrote down what you said about your work and about the training,
without your name. beSingularity sees the summary of the whole group. If you want
your answers removed later, tell beSingularity and they will be deleted. Good luck.

Then end the call. Do not ask any extra question after the closing.
"""


def build_extraction_prompt(questions: list[dict[str, Any]]) -> str:
    """System instructions for reading one transcript against this question set.

    Built from the batch's own questions, the way
    :func:`app.intelligence.prompts.build_extraction_prompt` is built from
    ``FACT_SCHEMA``. Contains no transcript and no thresholds of ours, so it is
    constant for a batch and safe to cache.
    """

    fields = "\n".join(
        f'  - "{question["slug"]}": the answer to "{question["text"]}"'
        for question in questions
    )
    states = "\n".join(
        f"  {state!r:<12} {description}"
        for state, description in (
            ("STATED", "they gave an answer you can record."),
            ("REFUSED", "they declined to answer, or would rather not say."),
            ("VAGUE", "they answered, but it cannot be resolved into a value."),
            ("NOT_ASKED", "the question never came up, including everything after an interview that was cut short."),
        )
    )
    return f"""You read one telephone interview transcript and report what the respondent
said. You do not judge, score, rank, or decide anything.

Return JSON only, with exactly this shape:

{{
  "consent": true or false,
  "consent_details": {{"state": "granted_no_name" | "declined" | "voided", "name": false, "quote": false, "voice": false, "photo": false}},
  "language": the ISO code of the language the respondent spoke, e.g. "am",
  "interview_stopped": true or false,
  "stop_reason": a short upper-case code, or null,
  "age_assessment": "CHILD" | "ADULT" | "UNKNOWN",
  "answers": {{
    "<question name>": {{
      "value": the answer in the respondent's own terms, or null,
      "state": "STATED" | "REFUSED" | "VAGUE" | "NOT_ASKED",
      "confidence": "HIGH" | "MEDIUM" | "LOW",
      "evidence": the respondent's own words, copied exactly, or null
    }}
  }}
}}

Report every one of these, and no others:
{fields}

AGE
"age_assessment" is the one judgement you are asked for, because a child must
never be interviewed and must never be counted. Answer "CHILD" when what they
said means they are below the age at which this work would be lawful for them,
"ADULT" when it does not, and "UNKNOWN" when age was never established. Say
nothing about any age rule anywhere else in your answer.

STATE
{states}
Use null for "value" whenever the state is not "STATED".

CONFIDENCE
  "HIGH"    a definite answer, including ordinary rounding.
  "MEDIUM"  they answered but hedged it, or corrected themselves.
  "LOW"     no usable answer, or you are reading between the lines.
A clear refusal is HIGH confidence: you are certain they declined.

EVIDENCE
Copy the respondent's words verbatim from the transcript, in the language they
spoke. Do not translate, tidy, shorten, or paraphrase. Quote the respondent, not
the interviewer. If the question was never answered, use null.

DATE AND DURATION SAFETY
For the start-date question, return a value only as an unambiguous Gregorian
month in YYYY-MM or date in YYYY-MM-DD form. A bare year, an Ethiopian-calendar
date, or a phrase whose month cannot be identified is VAGUE with value null.
Never put a year or a start date into a duration/months field. Only report
employment duration in months when the respondent explicitly states or clearly
establishes elapsed months.

RULES
Report only what is in the transcript. If it is not there, the state is
NOT_ASKED or VAGUE; never fill a gap from what is likely, typical, or implied.
Do not compare an answer to a minimum, a target, or a requirement, and do not
say whether an answer is good, sufficient, or acceptable.
Do not output a verdict, a score, or a recommendation.
Set "interview_stopped" to true only when the interviewer ended the call early,
and give the reason as a code such as "UNDER_MINIMUM_AGE" or "NO_CONSENT".

REFERENCE OUTPUT RECORD
The following is the canonical downstream Callwise record shape. Use it to
understand how extracted facts, consent, worker evidence, quotes, and the
deterministic good-job result fit together. It is an example only: do not copy
its values, do not invent fields from it, and do not decide its clauses or
"counted" value. Those are calculated by code after extraction.

{CALLWISE_RECORD_EXAMPLE}
"""


def build_extraction_request(transcript: str) -> str:
    """The user-turn payload for one extraction call."""

    return f"Transcript:\n\n{transcript}"


def empty_answer(stopped: bool = False) -> dict[str, Any]:
    """The answer recorded for a question the transcript does not answer."""

    return {
        "value": None,
        "state": "NOT_ASKED" if stopped else "VAGUE",
        "confidence": "LOW",
        "evidence": None,
        "category": None,
    }


def normalize_extraction(
    payload: dict[str, Any],
    *,
    worker_id: str,
    transcript: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Make a model response safe to store, whatever it actually returned.

    Every question in the set gets an entry, extra fields the model invented are
    dropped, and ``worker_id`` comes from the caller: the model is not a source
    of truth about who was interviewed.
    """

    raw = payload.get("answers")
    raw = raw if isinstance(raw, dict) else {}
    stopped = bool(payload.get("interview_stopped", False))

    raw_consent_details = payload.get("consent_details")
    consent_details = raw_consent_details if isinstance(raw_consent_details, dict) else {
        "state": "granted_no_name" if bool(payload.get("consent", False)) else "declined",
        "name": False,
        "quote": False,
        "voice": False,
        "photo": False,
    }
    consent_state = str(consent_details.get("state") or "").lower()
    consent = bool(payload.get("consent", False)) and consent_state not in {"declined", "voided"}

    answers: dict[str, Any] = {}
    for question in questions:
        if not consent:
            answers[question["slug"]] = empty_answer(stopped=True)
            continue
        entry = raw.get(question["slug"])
        if not isinstance(entry, dict):
            answers[question["slug"]] = empty_answer(stopped)
            continue
        state = entry.get("state")
        answers[question["slug"]] = {
            "value": entry.get("value"),
            "state": state if state in ANSWER_STATES else "VAGUE",
            "confidence": entry.get("confidence") or "LOW",
            "evidence": entry.get("evidence"),
            "category": None,
        }

    assessment = payload.get("age_assessment")
    language = payload.get("language")
    stop_reason = payload.get("stop_reason")
    return {
        "worker_id": worker_id,
        "consent": consent,
        "consent_details": consent_details,
        "language": language if isinstance(language, str) and language else None,
        "interview_stopped": stopped or not consent,
        "stop_reason": ("NO_CONSENT" if not consent else stop_reason) if isinstance(stop_reason, str) and stop_reason else ("NO_CONSENT" if not consent else None),
        "age_assessment": assessment if assessment in AGE_ASSESSMENTS else "UNKNOWN",
        "answers": answers,
        "transcript": transcript,
        "extraction_error": bool(payload.get("extraction_error", False)),
    }


__all__ = [
    "AGE_ASSESSMENTS",
    "AGE_QUESTION_TEXT",
    "AGE_SLUG",
    "build_refinement_prompt",
    "normalize_refinement",
    "refine_questions",
    "ANSWER_STATES",
    "FIXED_QUESTIONNAIRE",
    "fixed_question_set",
    "LANGUAGE_NAMES",
    "build_extraction_prompt",
    "build_extraction_request",
    "build_interview_prompt",
    "build_question_set",
    "is_age_question",
    "normalize_extraction",
    "parse_questions",
    "slugify",
]
