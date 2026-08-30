"""Offline stand-ins for the batch path's model calls.

The batch path makes exactly five kinds of provider call — question refinement,
extraction, categorization, selection parsing, and analysis — and
:class:`FakeBatchProvider` answers all five, chosen by the system prompt it is
handed. That keeps the whole flow testable with no network, no API key, and no
telephone.

The fake behaves like a competent model, not like a hostile one. Everything a
real provider could return that code has to reject (a category outside the
declared set, a malformed selection spec, an invented worker id) is exercised in
the unit suites against narrower stubs.
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
    """Answers all five batch model calls, chosen by the system prompt."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.selection: dict[str, Any] = {"mode": "all", "confidence": "HIGH"}

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if "You rewrite an administrator's typed questions" in system:
            return self._refine(user)
        if "You read one telephone interview transcript" in system:
            return self._extract(system, user)
        if "You group answers" in system:
            return self._categorize(user)
        if "You read one instruction from an administrator" in system:
            return dict(self.selection)
        if "You answer an administrator's questions" in system:
            return {"answer": "Two of the three counted interviews said the pay is not enough."}
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


__all__ = ["TRANSCRIPTS", "FakeBatchProvider"]
