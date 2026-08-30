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

#: States an answer may be in. Same vocabulary as the clause path, so the
#: dashboard and the chat describe a refusal the same way.
ANSWER_STATES = ("STATED", "REFUSED", "VAGUE", "NOT_ASKED")

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
            parts.append(cleaned)
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
        source = item["topic"] or item["text"]
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

Speak {language_name} for the whole call. If the person answers in a different
language, switch to theirs and stay there.

WHO YOU ARE
Say, in your own words, at the start of the call:
  - you are calling from a team that reviews working conditions;
  - what they say will not be reported back against their name;
  - answering is voluntary and they may stop at any time.
Then ask whether they are willing to answer some questions. If they say no, or
they sound unwilling, thank them warmly and end the call. Do not persuade them.

FIRST QUESTION, ALWAYS
Ask their age before anything else, and wait for the answer.
If the answer means they are still a child, do not ask a single further
question. Thank them, say you have nothing more to ask, wish them well, and end
the call. Do not explain why you are stopping, do not mention any age rule, and
do not tell them their answer was a problem. Saying so could put them under
pressure from their employer afterwards.

THE QUESTIONS
Once age is settled and they are not a child, ask these, in this order, in the
words they were given to you, adapted only as much as speaking them aloud
requires:
{listed}

HOW TO ASK
Ask one thing at a time, in plain spoken language, and let them finish.
Ask for their own experience, never for the workplace in general.
Never read a number back to them as if you already knew it, and never suggest
what a good answer would be.
Never say that an answer is a minimum, a target, a requirement, or a qualifying
level, and never say whether an answer sounds good or bad. You are recording
what they say, not judging it.

WHEN AN ANSWER IS VAGUE
Ask once more, in different words. If it is still not clear, accept it, thank
them, and move on. Do not guess on their behalf and do not offer them a value to
agree with.

WHEN SOMEONE WILL NOT ANSWER
If they decline a question, especially about pay, accept it immediately, say
that is completely fine, and move to the next one. Never ask twice, never
explain why you need it, and never propose an answer for them to confirm.

TONE
Warm, unhurried, and plain. No jargon. No opinions about their employer. When
you have what you need, thank them for their time and end the call.
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

RULES
Report only what is in the transcript. If it is not there, the state is
NOT_ASKED or VAGUE; never fill a gap from what is likely, typical, or implied.
Do not compare an answer to a minimum, a target, or a requirement, and do not
say whether an answer is good, sufficient, or acceptable.
Do not output a verdict, a score, or a recommendation.
Set "interview_stopped" to true only when the interviewer ended the call early,
and give the reason as a code such as "UNDER_MINIMUM_AGE" or "NO_CONSENT".
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

    answers: dict[str, Any] = {}
    for question in questions:
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
        "consent": bool(payload.get("consent", False)),
        "language": language if isinstance(language, str) and language else None,
        "interview_stopped": stopped,
        "stop_reason": stop_reason if isinstance(stop_reason, str) and stop_reason else None,
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
