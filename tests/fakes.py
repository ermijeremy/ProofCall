"""Offline stand-ins for the company thread's model calls.

Two fakes live here. :class:`FakeBatchProvider` answers the three JSON calls —
question refinement, extraction, and categorization — chosen by the system prompt
it is handed. :class:`FakeToolProvider` adds the fourth, the router, which is
function calling rather than JSON and therefore a different method. Together they
make the whole flow testable with no network, no API key, and no telephone.

The fakes behave like a competent model, not a hostile one. Everything a real
provider could return that code has to reject — a category outside the declared
set, a malformed selection spec, an invented worker id, a tool that is not on the
list — is exercised in the unit suites against narrower stubs.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Transcripts keyed by the name that appears in them. The fake reads the age out
#: of the text, the way the real model reads it out of speech.
TRANSCRIPTS: dict[str, str] = {
    "Abebe Kebede": "agent: how old are you?\nrespondent: I am 32.\nagent: is the pay enough?\nrespondent: no, it is not enough at all.",
    "Marta Alemu": "agent: how old are you?\nrespondent: I am 41.\nagent: is the pay enough?\nrespondent: yes, it is fine.",
    "Yonas Tesfaye": "agent: how old are you?\nrespondent: I am 27.\nagent: is the pay enough?\nrespondent: I would rather not say.",
    "Hanna Girma": "agent: how old are you?\nrespondent: I am 15.",
}


#: Shorthand a real model would expand. The fake rewrites only what it has been
#: taught, and leaves anything else exactly as typed — which is also what code
#: does when the provider is unreachable.
SHORTHAND = (
    (" ur ", " your "),
    (" u ", " you "),
    (" r ", " are "),
)


#: Words too common to name a topic. Short enough to stay honest about being a
#: fake: a real model picks the subject, it does not filter a stopword list.
_STOPWORDS = frozenset({"what", "does", "your", "have", "with", "that", "this", "they", "them", "enough", "much"})


class FakeBatchProvider:
    """Answers the three JSON model calls, chosen by the system prompt.

    Satisfies ``LLMProvider`` and nothing more, which is the honest description:
    it cannot choose an action, so :meth:`BatchIntelligence.decide` refuses it.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if "You rewrite an administrator's typed questions" in system:
            return self._refine(user)
        if "You read one telephone interview transcript" in system:
            return self._extract(system, user)
        if "You group answers" in system:
            return self._categorize(user)
        raise AssertionError(f"Unexpected system prompt: {system[:60]}")

    def calls_for(self, marker: str) -> list[dict[str, str]]:
        return [call for call in self.calls if marker in call["system"]]

    def _refine(self, user: str) -> dict[str, Any]:
        """Expand shorthand and name a topic, one entry per question, in order.

        Deliberately conservative: the count and the order are what code checks,
        and a fake that reordered or merged questions would hide the fact that
        ``normalize_refinement`` is the thing enforcing that.
        """

        typed = [
            re.sub(r"^\s*\d+\.\s*", "", line).strip()
            for line in user.splitlines()
            if re.match(r"^\s*\d+\.\s", line)
        ]
        entries = []
        for original in typed:
            text = f" {original} "
            for shorthand, full in SHORTHAND:
                text = text.replace(shorthand, full)
            text = text.strip()
            text = text[0].upper() + text[1:] if text else text
            if not text.endswith("?"):
                text += "?"
            words = [word for word in re.findall(r"[a-z]{4,}", original.lower()) if word not in _STOPWORDS]
            entries.append({"original": original, "text": text, "topic": " ".join(words[:2])})
        return {"questions": entries}

    @staticmethod
    def _slugs(system: str) -> list[str]:
        return re.findall(r'^  - "([a-z0-9_]+)":', system, flags=re.MULTILINE)

    def _extract(self, system: str, user: str) -> dict[str, Any]:
        ages = re.findall(r"I am (\d+)", user)
        age = ages[0] if ages else None
        # Standing in for the model's own judgement about age. The application
        # never looks at a number here; it acts on this label only.
        assessment = "UNKNOWN" if age is None else ("CHILD" if int(age) < 18 else "ADULT")
        stopped = assessment == "CHILD"
        answers: dict[str, Any] = {}
        for slug in self._slugs(system):
            if slug == "age_years":
                answers[slug] = {
                    "value": age,
                    "state": "STATED" if age else "NOT_ASKED",
                    "confidence": "HIGH",
                    "evidence": f"I am {age}." if age else None,
                }
            elif stopped:
                answers[slug] = {"value": None, "state": "NOT_ASKED", "confidence": "LOW", "evidence": None}
            elif "rather not say" in user:
                answers[slug] = {
                    "value": None,
                    "state": "REFUSED",
                    "confidence": "HIGH",
                    "evidence": "I would rather not say.",
                }
            else:
                enough = "yes, it is fine" in user
                answers[slug] = {
                    "value": "enough" if enough else "not enough",
                    "state": "STATED",
                    "confidence": "HIGH",
                    "evidence": "yes, it is fine." if enough else "no, it is not enough at all.",
                }
        return {
            "consent": True,
            "language": "am",
            "interview_stopped": stopped,
            "stop_reason": "UNDER_MINIMUM_AGE" if stopped else None,
            "age_assessment": assessment,
            "answers": answers,
        }

    @staticmethod
    def _categorize(user: str) -> dict[str, Any]:
        payload = json.loads(user)
        result: dict[str, Any] = {}
        for slug, block in payload.items():
            categories: list[str] = []
            assignments: dict[str, str] = {}
            for worker_id, entry in block["answers"].items():
                if slug == "age_years":
                    label = "Under 40" if int(entry["answer"]) < 40 else "40 and over"
                else:
                    label = "Enough" if entry["answer"] == "enough" else "Not enough"
                assignments[worker_id] = label
                if label not in categories:
                    categories.append(label)
            result[slug] = {"categories": categories, "assignments": assignments}
        return result


#: What the fake router does with a message, in the order it is checked. Each
#: entry is a marker to look for in the administrator's words and the tools to
#: call in reply, one per step. A real model reads the sentence; this reads a
#: keyword, which is exactly the thing the real path no longer does — and that is
#: the point of a fake: the routing under test is the *dispatch*, not the reading.
ROUTES: list[tuple[str, list[dict[str, Any]]]] = [
    ("what is ur age", [{"name": "set_questions", "arguments": {"questions": "LINES"}}, {"name": "answer"}]),
    ("swahili", [{"name": "set_language", "arguments": {"code": "sw"}}, {"name": "answer"}]),
    ("klingon", [{"name": "set_language", "arguments": {"code": "tlh"}}, {"name": "answer"}]),
    ("all of them", [{"name": "select_people", "arguments": {"mode": "all"}}, {"name": "answer"}]),
    ("everyone", [{"name": "select_people", "arguments": {"mode": "all"}}, {"name": "answer"}]),
    ("half of them", [{"name": "select_people", "arguments": {"mode": "fraction", "fraction": 0.5}}, {"name": "answer"}]),
    ("40%", [{"name": "select_people", "arguments": {"mode": "fraction", "fraction": 0.4}}, {"name": "answer"}]),
    ("two of them", [{"name": "select_people", "arguments": {"mode": "count", "count": 2.0}}, {"name": "answer"}]),
    (
        "haven't reached",
        [{"name": "select_people", "arguments": {"mode": "remaining"}}, {"name": "answer"}],
    ),
    ("thirty of them", [{"name": "select_people", "arguments": {"mode": "count", "count": 30.0}}, {"name": "answer"}]),
    ("usual people", [{"name": "ask", "arguments": {"text": "Who do you mean?"}}]),
    ("cancel", [{"name": "cancel_selection", "arguments": {}}, {"name": "answer"}]),
    ("no,", [{"name": "cancel_selection", "arguments": {}}, {"name": "answer"}]),
    ("yes", [{"name": "dial_now", "arguments": {}}, {"name": "answer"}]),
    ("call them now", [{"name": "dial_now", "arguments": {}}, {"name": "answer"}]),
    (
        "ask me for the time",
        [{"name": "ask_schedule", "arguments": {"missing": ["when"], "text": "When should I call?"}}],
    ),
    ("schedule the calls for", [{"name": "schedule_calls", "arguments": "SCHEDULE"}, {"name": "answer"}]),
    ("call ", [{"name": "select_people", "arguments": "NAMED"}, {"name": "answer"}]),
]


class FakeToolProvider(FakeBatchProvider):
    """Answers the router as well: a tool call per step, chosen by the message.

    Satisfies ``ToolCallingProvider``. Two behaviours matter for the tests. It
    reads worker ids out of the context block it was handed rather than inventing
    them, the way the real model is told to; and it counts the tool responses
    already in ``messages`` to know which step of a multi-action message it is on,
    so "call Abebe and schedule it for three" walks the same two-step path the
    real model does.
    """

    name = "fake-tools"

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: list[dict[str, Any]] = []
        #: Set to a tool call to override the routes for the next decision.
        self.next_call: dict[str, Any] | None = None

    def complete_tool_call(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.next_call is not None:
            call, self.next_call = self.next_call, None
            self.tool_calls.append(call)
            return call

        context = next((entry.get("text", "") for entry in messages if entry.get("role") == "user"), "")
        spoken = [entry for entry in messages if entry.get("role") == "user"]
        message = spoken[-1].get("text", "") if len(spoken) > 1 else ""
        step = len([entry for entry in messages if entry.get("role") == "tool"])

        plan = self._plan(message)
        call = plan[step] if step < len(plan) else {"name": "answer"}
        arguments = self._arguments(call.get("arguments", {}), message, context)
        if call["name"] in {"answer", "ask"} and not arguments.get("text"):
            arguments = {"text": self._spoken_text(messages, context)}
        call = {"name": call["name"], "arguments": arguments}
        self.tool_calls.append(call)
        return call

    @classmethod
    def _spoken_text(cls, messages: list[dict[str, Any]], context: str) -> str:
        """Narrate the last tool result, the way the real model is asked to.

        The service layer deliberately gives acting tools no words of their own:
        they hand back facts and the model writes the sentence. A fake that spoke
        no sentence would send every test through ``_fallback_text``, which is the
        backstop for a confused model rather than the product's voice — so the
        tests would stop covering the thing they exist to cover.
        """

        responses = [entry for entry in messages if entry.get("role") == "tool"]
        if not responses:
            return cls._counts_text(context)
        last = responses[-1]
        result = last.get("response") or {}
        name = last.get("name")
        if not result.get("ok"):
            return f"I could not do that: {result.get('error') or 'no reason given'}."
        if name == "set_questions":
            tail = " I tidied the wording." if result.get("rewritten") else ""
            return f"Saved {result['count']} question(s), age asked first.{tail}"
        if name == "select_people":
            people = ", ".join(result.get("selected") or [])
            return (
                f"That is {result['count']} people: {people}. Nobody is called until you say so, "
                f"or give me a time. I retry {result.get('default_retries')} times by default."
            )
        if name == "schedule_calls":
            return f"Set for {result.get('when')}. Tell me to cancel any time before then."
        if name == "dial_now":
            failed = result.get("failed") or []
            tail = " I could not get through to " + ", ".join(failed) + "." if failed else ""
            return f"Calling {result['count']} now.{tail}"
        if name == "cancel_selection":
            return f"Cancelled, and nobody was called. I let {result.get('cleared')} go."
        if name == "set_language":
            return f"The interviews will be in {result.get('language')}."
        return "Done."

    @staticmethod
    def _counts_text(context: str) -> str:
        """Quote the counts block back, since quoting it is the whole instruction."""

        quoted = [
            line.strip()
            for line in context.splitlines()
            if line.startswith("  Interviews counted:") or line.startswith("  Excluded and counted nowhere:")
        ]
        if quoted:
            return " ".join(quoted) + "."
        return "There is nothing counted yet, so there is nothing I can tell you."

    @staticmethod
    def _plan(message: str) -> list[dict[str, Any]]:
        """The tools for this message: a marker match, then a shape, then speech."""

        lowered = message.lower()
        for marker, plan in ROUTES:
            if marker in lowered:
                return plan
        # A numbered or multi-line message is a question set. Checked after the
        # markers so "call all of them" is never read as a question, and by shape
        # rather than by keyword because that is what an admin's typing looks like.
        if re.match(r"^\s*\d+\s*[-.)]", message) or len([l for l in message.splitlines() if l.strip()]) > 1:
            return [{"name": "set_questions", "arguments": {"questions": "LINES"}}, {"name": "answer"}]
        return [{"name": "answer"}]

    def _arguments(self, arguments: Any, message: str, context: str) -> dict[str, Any]:
        """Fill in the markers in a route with what the message actually said.

        The routes cannot hold real arguments, because the worker ids and the
        timestamp only exist at the moment the call is made.
        """

        if arguments == "NAMED":
            return {"mode": "explicit", "worker_ids": self._ids_named_in(message, context)}
        if arguments == "SCHEDULE":
            return self._schedule_from(message)
        filled = dict(arguments)
        if filled.get("questions") == "LINES":
            filled["questions"] = [
                re.sub(r"^\s*\d+\s*[-.)]\s*", "", line).strip()
                for line in message.splitlines()
                if line.strip()
            ]
        return filled

    @staticmethod
    def _roster(context: str) -> list[tuple[str, str]]:
        """``(worker_id, name)`` for everybody in the context block."""

        found = []
        for line in context.splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 3 and parts[0].startswith("w"):
                found.append((parts[0], parts[1]))
        return found

    def _ids_named_in(self, message: str, context: str) -> list[str]:
        """Worker ids for the names in the message, plus a fabricated one for a stranger.

        A name in the message that is not on the list becomes ``w_invented`` so the
        service layer's "the model named somebody who is not here" path is
        exercised, rather than the name being quietly dropped.
        """

        lowered = message.lower()
        ids = [
            worker_id
            for worker_id, name in self._roster(context)
            if name.split()[0].lower() in lowered
        ]
        known = {name.split()[0].lower() for _, name in self._roster(context)}
        words = re.findall(r"[a-z]{3,}", lowered.split("call", 1)[-1])
        strangers = [word for word in words if word not in known and word not in {"and", "the", "them", "for", "this"}]
        return ids + ["w_invented"] * len(strangers)

    @staticmethod
    def _schedule_from(message: str) -> dict[str, Any]:
        """Read the picker's own words back: a timestamp, a zone, a retry count."""

        arguments: dict[str, Any] = {}
        stamp = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", message)
        if stamp:
            arguments["when_iso"] = f"{stamp.group(1)}T{stamp.group(2)}"
        zone = re.search(r"time zone is ([\w/+_-]+)", message)
        if zone:
            arguments["timezone"] = zone.group(1)
        retries = re.search(r"retry (\d+) times", message)
        if retries:
            arguments["retries"] = float(retries.group(1))
        return arguments


__all__ = ["ROUTES", "TRANSCRIPTS", "FakeBatchProvider", "FakeToolProvider"]
