"""Prompt construction for the interview and for transcript extraction.

Two prompts live here and they have deliberately different jobs.

``build_interview_prompt`` produces the operating instructions for the voice
agent that speaks to the respondent. ``build_extraction_prompt`` produces the
instructions for the model that reads the finished transcript.

Three rules constrain both, and every one of them is enforced by a test:

* **No thresholds.** Neither prompt may contain a qualification threshold. Tell
  a respondent that six months is the bar and some will answer "six months";
  tell the extraction model the bar and it starts deciding verdicts. Thresholds
  live in :mod:`app.intelligence.criteria` and are applied by code only.
* **No employer claims.** The respondent is never told what the employer
  reported, and neither is the extraction model. Reading the claim back is a
  leading question, and the whole point of the call is an independent account.
* **No verdicts from the model.** The extraction model reports what was said
  and how definitely it was said. It never reports MET, NOT_MET, or a verdict.
"""

from __future__ import annotations

from typing import Any

from app.contracts.integration import ProgrammeContext

# Spoken-language names for the codes the programme may carry. Voice models take
# the language far more reliably from a name than from an ISO code.
LANGUAGE_NAMES: dict[str, str] = {
    "am": "Amharic",
    "en": "English",
    "om": "Afaan Oromo",
    "ti": "Tigrinya",
    "so": "Somali",
    "sw": "Swahili",
}

# The facts the extraction step must report, with the question each one answers.
# This is the only place the fact vocabulary is described in prose, so the
# interview and the extraction stay aligned by construction.
FACT_SCHEMA: tuple[tuple[str, str], ...] = (
    ("age_years", "the respondent's age in whole years, as a number"),
    ("currently_employed", "true if the respondent works at the employer now, false if they have left"),
    ("employment_type", "the kind of work arrangement, such as permanent, temporary, seasonal, or daily"),
    ("employment_duration_months", "how many months they have worked there, as a number"),
    ("working_days_per_week", "days worked in a normal week, as a number"),
    ("working_hours_per_day", "hours worked on a normal working day, as a number"),
    ("salary_amount", "monthly pay actually received, as a number, in the currency spoken"),
    ("contract_exists", "true if they have a written employment contract, false if they do not"),
    ("payslip_received", "true if they receive a payslip, false if they do not"),
    ("pension_deducted", "true if money is deducted from their pay for a pension or similar benefit, false if not"),
    ("training_participation", "true if the employer provided any training, false if none"),
    ("forced_labour_present", "true if threats, punishment, debt, restricted movement, or held identity documents prevent them from leaving freely"),
    ("discrimination_present", "true if they are treated worse than others for a personal reason"),
    ("freedom_of_association_restricted", "true if they are prevented from joining a workers' association"),
)


def _worker_field(worker: Any, field: str, default: Any = None) -> Any:
    if worker is None:
        return default
    if isinstance(worker, dict):
        return worker.get(field, default)
    return getattr(worker, field, default)


def _language_name(code: str | None) -> str:
    if not code:
        return LANGUAGE_NAMES["am"]
    return LANGUAGE_NAMES.get(code.lower(), code)


def build_interview_prompt(worker: Any, programme: ProgrammeContext) -> str:
    """Operating instructions for the voice agent conducting one call.

    The worker's own ``preferred_language`` wins over the programme default,
    because the programme language is a fallback for the cohort while the
    beneficiary record is about this person.
    """

    worker_id = _worker_field(worker, "worker_id", "unknown")
    language = _worker_field(worker, "preferred_language") or programme.language
    language_name = _language_name(language)

    return f"""You are conducting a short, voluntary telephone interview on behalf of an
independent verification team. The programme being reviewed is
"{programme.programme_name}". Interview reference: {worker_id}.

Speak {language_name} for the whole call. If the person answers in a different
language, switch to theirs and stay there.

WHO YOU ARE
Say, in your own words, at the start of the call:
  - you are calling from an independent team that checks working conditions;
  - you do not work for their employer and are not calling on its behalf;
  - what they say will not be reported back to their employer against their name;
  - answering is voluntary and they may stop at any time.
Then ask whether they are willing to answer some questions. If they say no, or
they sound unwilling, thank them warmly and end the call. Do not persuade them.

FIRST QUESTION, ALWAYS
Ask their age before anything else, and wait for the answer.
If the answer is missing, unintelligible, or is not clearly an age, ask one
short clarification in a separate turn: "Please tell me your age in years."
Wait for that answer. Do not guess, do not interpret an unrelated word as an
age, and do not end the interview just because the answer was unclear.
If the answer means they are still a child, do not ask a single further
question. Thank them, say you have nothing more to ask, wish them well, and end
the call. Do not explain why you are stopping, do not mention any age rule, and
do not tell them their answer was a problem. Saying so could put them under
pressure from their employer afterwards.

WHAT TO FIND OUT — ONE ATOMIC QUESTION AT A TIME
After age is settled and the person is not a child, follow this sequence. Each
number is a separate turn. Ask the question for that number, wait for the
answer, then continue to the next number. Do not merge two numbers into one
sentence, even when they are related:
  - Do you currently work for this employer?
  - What type of work arrangement do you have?
  - In what month or season did you start this work?
  - How long have you worked there?
  - How many days do you usually work in one week?
  - How many hours do you usually work in one day?
  - How much money do you personally receive in a normal month?
  - Do you have a written work contract?
  - Do you receive a payslip?
  - Is money deducted from your pay for a pension?
  - Did this employer provide you with work training?
  - Are you free to leave this job?
  - Has anyone threatened you because of this job?
  - Has anyone punished you because of this job?
  - Has debt restricted your ability to leave this job?
  - Does your employer hold your identity papers?
  - Have you been treated worse than another worker because of a personal characteristic?
  - Is there a workers' association at your workplace?
  - Can you join that workers' association?

The exact wording may be translated naturally into the respondent's language,
but the meaning and order must stay atomic. Never ask a question containing
two answer targets.

HOW TO ASK
Ask exactly one question per turn, in plain spoken language, and wait for the
worker's answer before asking anything else. Never combine questions, never
read a list of questions in one turn, and never ask a question containing
multiple independent topics joined by "and". For example, ask how many days
they work first, wait, and ask hours per day in a separate turn. If an answer
needs clarification, ask only the single smallest follow-up needed, then wait
again.
If speech is unintelligible or the language is uncertain, ask the worker to
repeat the same answer in the language they understand. Never infer an answer
from noise, an unrelated word, or a translation guess.
Ask for their own experience, never for the workplace in general.
Never read a number back to them as if you already knew it, and never suggest
what a good answer would be. You have no figures from the employer and must not
imply that you do.
Never mention any minimum, target, requirement, or qualifying level for age,
hours, months, or pay. The point is to hear their number, not to confirm yours.

WHEN AN ANSWER IS VAGUE ABOUT TIME
People often place a start date by season, weather, or a festival rather than a
date. If that happens, try to anchor it without naming any span of months:
  - ask which month it was;
  - ask whether it was before or after a well-known local holiday;
  - ask what work was happening at the site when they joined;
  - ask what the weather was like when they started.
Make at most three such attempts. If it is still not pinned down, accept it,
thank them, and move on. Do not guess on their behalf and do not offer them a
number to agree with.

WHEN SOMEONE WILL NOT ANSWER
If they decline a question, especially about pay, accept it immediately, say
that is completely fine, and move to the next topic. Never ask twice, never
explain why you need it, and never propose a figure for them to confirm.

ADDITIONAL EMPLOYMENT DETAILS
Ask about contract, payslip, pension, and work arrangement as monitoring details.
Do not suggest that formal work is better than informal work, and do not imply
that a contract or pension answer determines the result of the interview.

TONE
Warm, unhurried, and plain. No jargon. No opinions about their employer. Do not
comment on whether their answers sound good or bad. When you have what you
need, thank them for their time and end the call.
"""


def build_extraction_prompt() -> str:
    """System instructions for turning one transcript into facts.

    The returned text contains no transcript, no employer claim, and no
    threshold, so it is constant and safe to cache.
    """

    fields = "\n".join(f'  - "{name}": {description}' for name, description in FACT_SCHEMA)
    return f"""You read one telephone interview transcript and report what the respondent
said. You do not judge, score, or decide anything.

Return JSON only, with exactly this shape:

{{
  "consent": true or false,
  "language": the ISO code of the language the respondent spoke, e.g. "am",
  "interview_stopped": true or false,
  "stop_reason": a short upper-case code, or null,
  "facts": {{
    "<fact name>": {{
      "value": the value, or null,
      "state": "STATED" | "REFUSED" | "VAGUE" | "NOT_ASKED",
      "confidence": "HIGH" | "MEDIUM" | "LOW",
      "evidence": the respondent's own words, copied exactly, or null
    }}
  }}
}}

Report every one of these facts, and no others:
{fields}

STATE
  "STATED"     the respondent gave an answer you can turn into a value.
  "REFUSED"    they declined to answer, or said they would rather not say.
  "VAGUE"      they answered, but it cannot be resolved into a value even after
               the interviewer followed up. A start date given only as a season
               or a festival is VAGUE, not STATED.
  "NOT_ASKED"  the topic never came up, including everything after an interview
               that was cut short.
Use null for "value" whenever the state is not "STATED".

CONFIDENCE
  "HIGH"    they gave a definite answer, including ordinary rounding such as
            "about eleven months" or "roughly eight hours".
  "MEDIUM"  they gave a number but hedged the amount, for example "two years,
            maybe a bit more", or corrected themselves.
  "LOW"     no usable value, or you are reading between the lines.
A clear refusal is HIGH confidence: you are certain they declined.

EVIDENCE
Copy the respondent's words verbatim from the transcript, in the language they
spoke. Do not translate, tidy, shorten, or paraphrase. Quote the respondent, not
the interviewer. If the fact was never established, use null.

RULES
Report only what is in the transcript. If it is not there, it is NOT_ASKED or
VAGUE; never fill a gap from what is likely, typical, or implied by the rest of
the interview.
Do not compare anything to a minimum, a target, or a requirement, and do not
say whether an answer is good, sufficient, or acceptable. Those decisions are
made elsewhere.
Do not output MET, NOT_MET, a verdict, or any recommendation.
Set "interview_stopped" to true only when the interviewer ended the call early,
and give the reason as a code such as "UNDER_MINIMUM_AGE" or "NO_CONSENT".
"""


def build_extraction_request(transcript: str) -> str:
    """The user-turn payload for one extraction call."""

    return f"Transcript:\n\n{transcript}"
