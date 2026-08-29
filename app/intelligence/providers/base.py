"""Provider port for the extraction step.

The rule engine never talks to a model. It receives facts, applies thresholds,
and returns a verdict. Everything model-shaped lives behind this port, so a
provider can be swapped, stubbed, or added without the qualification logic
noticing.

Gemini is the project default (:mod:`app.intelligence.providers.gemini`).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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
