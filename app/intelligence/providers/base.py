"""Provider port for the extraction step.

The rule engine never talks to a model. It receives facts, applies thresholds,
and returns a verdict. Everything model-shaped lives behind this port, so a
provider can be swapped, stubbed, or added without the qualification logic
noticing.

Gemini is the project default (:mod:`app.intelligence.providers.gemini`).
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable


class ProviderError(RuntimeError):
    """The provider could not return usable JSON.

    Raised for transport failures, refusals, and malformed output alike. The
    caller decides what a failure means; the provider only reports that it
    happened.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """Anything that can turn a system prompt plus a transcript into JSON."""

    #: Short identifier recorded alongside extraction output, e.g. ``"gemini"``.
    name: str

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        """Return the model's JSON response as a dict.

        Implementations must raise :class:`ProviderError` rather than returning
        a partial or guessed result.
        """
        ...


@runtime_checkable
class ToolCallingProvider(LLMProvider, Protocol):
    """A provider that can also be asked to *choose* an action.

    Separate from :class:`LLMProvider` because the two are different capabilities,
    not one with an optional extra. Extraction wants one shape of answer and JSON
    mode gives it; a router wants a decision and must never be handed prose where
    an action was required. ``RecordedProvider`` replays fixtures and satisfies
    only :class:`LLMProvider`, which is the honest description of it.
    """

    def complete_tool_call(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return ``{"name": str, "arguments": dict}`` for the tool the model chose.

        Implementations force a call and raise :class:`ProviderError` if none comes
        back, because a router that silently does nothing is worse than one that
        fails loudly.

        ``messages`` carries the turn history as
        ``[{"role": "user" | "model", "text": str}]``. A tool result is fed back as
        ``{"role": "tool", "name": str, "response": dict}``, preceded by the model
        turn that called it as ``{"role": "model", "call": {"name", "arguments"}}``.
        """
        ...
