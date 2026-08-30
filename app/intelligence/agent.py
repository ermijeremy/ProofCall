"""The router: one model call decides what the administrator's message meant.

Everything the administrator types goes through here, and nothing else in the
thread reads their words. The old path branched on the round's status and matched
keywords — confirmations against a frozen set, languages against a word list,
names by substring — so "yes call them now" was not a yes and "call for this
body" was not a selection. A language model is the right tool for reading a
sentence; a frozen set is not.

The division of labour is the whole design, and it is not negotiable:

* **The model interprets.** It reads the message, resolves a name against the
  roster it was handed, understands "40% of them" and "tomorrow at 3", and
  chooses one action.
* **Code acts.** The tool it chose is validated and executed here in Python. The
  random draw, the safeguarding exclusion, every count, and the dial itself never
  move into the model.

That is why :func:`decide` returns a *choice* and never a result. It cannot select
anybody, place a call, or state a number; it can only name a tool and its
arguments, and the service layer decides whether that is allowed right now.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from app.intelligence.providers.base import ProviderError, ToolCallingProvider

logger = logging.getLogger(__name__)

#: How many chained tool calls one message may produce. "Call Abebe and schedule
#: it for tomorrow at 3" is genuinely two actions, and a third is the reply that
#: reports them. The cap exists because a confused model that keeps selecting
#: people would otherwise loop against a paid API.
MAX_STEPS = 3

#: How much of the thread the model is shown. Enough to follow a conversation
#: about the results; short enough that a long thread does not push the roster
#: out of the useful part of the context.
HISTORY_TURNS = 20


def _tool(name: str, description: str, properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
    }


#: The complete set of actions available. A model that must always call something
#: can only do these nine things, which is the safety property: there is no tool
#: that dials without a confirmation and no tool that invents a count.
TOOLS: list[dict[str, Any]] = [
    _tool(
        "set_questions",
        "Record the questions to ask employees in a new round of interviews. Use "
        "this whenever the administrator gives you questions, however they are "
        "written: numbered, bulleted, one per line, or in a sentence. Pass each "
        "question separately, in the order given, in the administrator's own "
        "words. Do not add an age question; that is always asked first anyway.",
        {
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One entry per question, as the administrator wrote it.",
            }
        },
        ["questions"],
    ),
    _tool(
        "select_people",
        "Choose who gets telephoned. Nobody is called by this tool: it draws an "
        "unconfirmed selection that the administrator must then confirm. For a "
        "random share use mode 'count' or 'fraction' and do NOT list worker_ids — "
        "the draw must be random and auditable, which is not your decision. For "
        "named or hand-picked people use mode 'explicit' and give the worker_ids "
        "from the roster you were shown, never names.",
        {
            "mode": {
                "type": "string",
                "enum": ["all", "count", "fraction", "explicit", "remaining"],
                "description": (
                    "'all' everyone; 'count' a number of them at random; 'fraction' a "
                    "share of them at random; 'explicit' exactly the worker_ids you "
                    "give; 'remaining' everyone not yet reached."
                ),
            },
            "count": {"type": "integer", "description": "With mode 'count': how many."},
            "fraction": {
                "type": "number",
                "description": "With mode 'fraction': a decimal. Half is 0.5, 40 percent is 0.4.",
            },
            "worker_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "With mode 'explicit': worker_ids copied from the roster.",
            },
            "exclude_already_called": {
                "type": "boolean",
                "description": "True when they asked for people not yet reached, 'the rest', or 'the others'.",
            },
        },
        ["mode"],
    ),
    _tool(
        "ask_schedule",
        "Show the administrator a time picker because something about the timing "
        "is missing or ambiguous. Use this rather than guessing a time, a zone, or "
        "which reading of a bare hour was meant.",
        {
            "missing": {
                "type": "array",
                "items": {"type": "string", "enum": ["when", "timezone", "retries"]},
                "description": "What you still need.",
            },
            "text": {
                "type": "string",
                "description": "One short sentence saying what you need and why.",
            },
        },
        ["missing", "text"],
    ),
    _tool(
        "schedule_calls",
        "Place the confirmed selection in the queue to be telephoned at a given "
        "time. Only call this when you know the exact local time and the zone; "
        "otherwise call ask_schedule. A scheduled round can be cancelled in words "
        "before it fires, so this does not need a separate confirmation.",
        {
            "when_iso": {
                "type": "string",
                "description": "The local wall-clock time, ISO 8601, no offset: 2026-08-31T15:00.",
            },
            "timezone": {
                "type": "string",
                "description": "IANA zone name, for example Africa/Addis_Ababa.",
            },
            "retries": {
                "type": "integer",
                "description": "How many times to retry a call that does not connect. Omit to use the default.",
            },
            "clock_convention": {
                "type": "string",
                "enum": ["24h", "ethiopian"],
                "description": "Which clock the administrator was reading from, if they made it clear.",
            },
            "confirmed_outside_window": {
                "type": "boolean",
                "description": "True only if the administrator explicitly confirmed a time outside working hours.",
            },
        },
        ["when_iso", "timezone"],
    ),
    _tool(
        "dial_now",
        "Telephone the selected people immediately. Call this ONLY when the "
        "administrator has clearly agreed to place the calls now — 'yes', 'go "
        "ahead', 'call them', 'do it'. A placed call cannot be recalled, so if "
        "there is any doubt about what they meant, use ask instead.",
        {},
    ),
    _tool(
        "cancel_selection",
        "Discard the unconfirmed selection, or a scheduled round that has not "
        "fired yet. Nobody is called.",
        {},
    ),
    _tool(
        "set_language",
        "Set the language the interviews are conducted in.",
        {"code": {"type": "string", "description": "Two-letter code: am, en, sw, om, ti."}},
        ["code"],
    ),
    _tool(
        "answer",
        "Reply to the administrator using the counts you were given. Every figure "
        "in the context has already been computed from stored answers — quote "
        "those and never work out a number of your own.",
        {"text": {"type": "string", "description": "The reply, plain and short."}},
        ["text"],
    ),
    _tool(
        "ask",
        "Ask the administrator a question, or tell them something that needs no "
        "action. Use this whenever you are unsure: asking is free, and a wrong "
        "guess telephones real people.",
        {"text": {"type": "string", "description": "What to say, plain and short."}},
        ["text"],
    ),
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)


def build_agent_prompt() -> str:
    """System instructions for the router."""

    return """You run interview rounds for a company by telephone. An administrator
talks to you in one chat thread per company. You read each message and choose
exactly one action.

WHAT YOU ARE FOR
The administrator uploads a list of employees once, types questions, tells you who
to telephone, and asks about the answers that come back. Each set of questions is
one round. The list of people belongs to the company and is reused every round.

HOW TO CHOOSE
Read the message together with the context block, which holds the company, the
list of people with their worker_ids, the current round, the counts so far, and
the current local time. Then call exactly one tool.

Questions to be asked go to set_questions. Who to telephone goes to
select_people. A time goes to schedule_calls, or to ask_schedule when something
about it is missing. A clear agreement to call now goes to dial_now. A question
about the results goes to answer. Anything else goes to ask.

NUMBERS
Every count you are given has been computed from stored answers. Quote them.
Never add up, estimate, or infer a figure of your own, and never state a count
that is not in the context block.

PEOPLE
The context block lists every employee with a worker_id. To telephone named
people, copy their worker_ids into select_people with mode 'explicit'. Never
invent a worker_id, and never pass worker_ids for a random share — 'half of
them', '40%', 'a hundred of them' are random draws, and the draw is not yours to
make. If a name matches two people, use ask and tell the administrator which two,
with their telephone numbers.

TIME
You have no clock. The current local time is in the context block; work from it
and from nothing else. Resolve relative times against it: 'tomorrow at 3' is the
next day at that hour. Send schedule_calls a local wall-clock time with no offset
and the zone separately.
If the company's zone is not yet known, do not guess it — call ask_schedule with
'timezone' among the missing items.
The interview numbers are Ethiopian and the interviews are often in Amharic, where
a bare hour spoken colloquially means six hours later than the same number on a
24-hour clock: 'three o'clock' is usually 9:00 in the morning. When a bare hour
could mean either, use ask and offer both readings. Guessing wrong telephones
twenty people in the middle of the night.
If the administrator gives a time but no retry count, schedule_calls without
retries — the default is applied and you will be told what it was so you can say
it out loud.

CALLING PEOPLE
Every call is a real telephone ringing and cannot be recalled. select_people only
draws a selection; dial_now places the calls. Use dial_now only for a clear yes.
'Not yet', 'wait', 'no', 'different people' are cancel_selection or ask, never
dial_now.

TONE
Write like a colleague, not a form. Short sentences, no headings, no bullet
points, no emoji, and never repeat the whole context back. Say what happened and
what you need next, and stop.
"""


def _roster_lines(roster: Sequence[dict[str, Any]]) -> list[str]:
    lines = []
    for person in roster:
        marks = []
        if person.get("excluded"):
            marks.append("excluded, counted nowhere")
        if person.get("call_status"):
            marks.append(f"last call {person['call_status']}")
        elif person.get("selected"):
            marks.append("selected, not yet called")
        if person.get("answered"):
            marks.append("answers in")
        suffix = f" [{'; '.join(marks)}]" if marks else ""
        lines.append(
            f"  {person.get('worker_id')} | {person.get('name')} | {person.get('phone_number')}{suffix}"
        )
    return lines


def build_context_block(context: dict[str, Any]) -> str:
    """Render the context as the text the model actually reads.

    Plain lines rather than JSON: the roster is the largest part of it and a
    table of it costs a third of the tokens the equivalent JSON does, which
    matters because this block is resent on every message.
    """

    company = context.get("company") or {}
    lines = [
        "CONTEXT",
        f"Company: {company.get('name') or company.get('company_id')}",
        f"Interview language: {company.get('language') or 'am'}",
        f"Company timezone: {company.get('timezone') or 'NOT KNOWN — ask before scheduling'}",
        f"Clock convention: {company.get('clock_convention') or 'not known'}",
        f"Now: {context.get('now_local') or 'unknown'}",
    ]

    roster = context.get("roster") or []
    lines.append(f"People on the list: {len(roster)}")
    if roster:
        lines.append("  worker_id | name | telephone")
        lines.extend(_roster_lines(roster))
    else:
        lines.append("  (none yet — the administrator has not uploaded a CSV)")

    round_ = context.get("round")
    if not round_:
        lines.append("Current round: none. No questions have been set yet.")
    else:
        lines.append(f"Current round: {round_.get('status')}")
        questions = round_.get("questions") or []
        if questions:
            lines.append(f"  Questions ({len(questions)}, age is always asked first):")
            lines.extend(f"    {index + 1}. {item['text']}" for index, item in enumerate(questions))
        selected = round_.get("selected") or []
        lines.append(
            f"  Selected and awaiting confirmation: {len(selected)}"
            + (" — " + ", ".join(selected) if selected else "")
        )
        if round_.get("scheduled_local"):
            lines.append(
                f"  Scheduled for {round_['scheduled_local']}, {round_.get('retries')} retry attempt(s)"
            )
        lines.append(f"  Calls placed: {round_.get('dialed', 0)}; answers back: {round_.get('returned', 0)}")

    counts = context.get("counts")
    if counts:
        lines.append("Counts, already computed — quote these and add nothing:")
        lines.append(f"  Interviews counted: {counts.get('interviews_counted', 0)}")
        lines.append(f"  Excluded and counted nowhere: {counts.get('excluded_total', 0)}")
        for block in (counts.get("questions") or {}).values():
            tally = ", ".join(f"{name} {total}" for name, total in (block.get("category_counts") or {}).items())
            lines.append(f"  {block.get('question')} — {tally or 'no categorised answers yet'}")

    notes = context.get("notes") or []
    if notes:
        lines.append("Notes for you:")
        lines.extend(f"  {note}" for note in notes)

    return "\n".join(lines)


def normalize_call(payload: Any) -> dict[str, Any]:
    """Coerce a provider's answer into a tool name and clean arguments.

    Argument types are coerced rather than trusted: the API returns every number
    as a float, so ``count`` arrives as ``4.0`` and indexing a roster with it
    raises. An unknown tool name is a provider failure and not a silent no-op,
    because a router that quietly does nothing looks exactly like a router that
    decided nothing needed doing.
    """

    if not isinstance(payload, dict):
        raise ProviderError(f"Router returned {type(payload).__name__}, expected a tool call")
    name = payload.get("name")
    if name not in TOOL_NAMES:
        raise ProviderError(f"Router chose an unknown tool: {name!r}")

    raw = payload.get("arguments")
    arguments = dict(raw) if isinstance(raw, dict) else {}

    for key in ("count", "retries"):
        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            arguments.pop(key, None)
        else:
            arguments[key] = int(value)

    fraction = arguments.get("fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        arguments.pop("fraction", None)
    else:
        arguments["fraction"] = float(fraction)

    for key in ("questions", "worker_ids", "missing"):
        value = arguments.get(key)
        if isinstance(value, (list, tuple)):
            arguments[key] = [str(item).strip() for item in value if str(item).strip()]
        else:
            arguments.pop(key, None)

    for key in ("text", "mode", "code", "when_iso", "timezone", "clock_convention"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            arguments[key] = value.strip()
        else:
            arguments.pop(key, None)

    for key in ("exclude_already_called", "confirmed_outside_window"):
        if key in arguments:
            arguments[key] = bool(arguments[key])

    # The provider's signature for this call, when it issues one. Opaque, carried
    # back unread so a second step can replay the first call the way Gemini 3
    # requires; see :func:`app.intelligence.providers.gemini._signature_for`.
    return {"name": name, "arguments": arguments, "signature": payload.get("signature")}


def decide(
    provider: ToolCallingProvider,
    context: dict[str, Any],
    history: Sequence[dict[str, str]],
    *,
    prior: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Choose one action for the latest message in ``history``.

    ``prior`` carries the tool calls already made for this same message and what
    each returned, so a second call sees the result of the first. That is what
    makes "call Abebe and schedule it for tomorrow at 3" one message and two
    actions, without the caller having to re-derive a context the model already
    has.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "text": build_context_block(context)}]
    for turn in list(history)[-HISTORY_TURNS:]:
        messages.append(
            {
                "role": "model" if turn.get("role") == "system" else "user",
                "text": turn.get("text") or "",
            }
        )
    for step in prior:
        # A signed call can be replayed as the function call it was, which is what
        # lets the model see its own action. Without a signature the provider would
        # reject the replay, so the step is described in words instead: the model
        # still learns what happened, and the turn stays valid.
        if step.get("signature"):
            messages.append(
                {
                    "role": "model",
                    "call": {
                        "name": step["name"],
                        "arguments": step.get("arguments") or {},
                        "signature": step["signature"],
                    },
                }
            )
            messages.append({"role": "tool", "name": step["name"], "response": step.get("result") or {}})
        else:
            messages.append(
                {
                    "role": "user",
                    "text": (
                        "You already ran this for the message above:\n"
                        f"  {step['name']}({json.dumps(step.get('arguments') or {}, ensure_ascii=False)})\n"
                        f"  it returned: {json.dumps(step.get('result') or {}, ensure_ascii=False)}\n"
                        "Do not run it again. Reply to the administrator, or take the "
                        "one further action they asked for."
                    ),
                }
            )

    return normalize_call(
        provider.complete_tool_call(system=build_agent_prompt(), messages=messages, tools=TOOLS)
    )


__all__ = [
    "HISTORY_TURNS",
    "MAX_STEPS",
    "TOOLS",
    "TOOL_NAMES",
    "build_agent_prompt",
    "build_context_block",
    "decide",
    "normalize_call",
]
