"""Provider-agnostic JSON completion.

Both backends are free tiers, which means both will fail regularly — Groq at 30 RPM, Gemini
at its own daily cap. The interface is therefore built around failure being routine rather
than exceptional: LLMRateLimited is a signal to try the other provider, LLMUnavailable is a
signal to give up and let the caller degrade to a manual path.

Nothing user-facing may block on this. Every caller in the app has a non-LLM fallback.
"""

from typing import Any, Protocol, runtime_checkable


class _UseSettings:
    """Sentinel for 'read the key from settings'.

    Without it, `Provider(api_key=None)` is ambiguous: it reads as "no key" but silently
    falls back to the configured one, so a test asserting the unconfigured path passes or
    fails depending on whether the developer happens to have a key in their `.env`.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<from settings>"


USE_SETTINGS = _UseSettings()


class LLMError(Exception):
    """Base for anything that went wrong talking to a model."""


class LLMRateLimited(LLMError):
    """429 or quota exhausted. Worth retrying on a different provider, not on this one."""


class LLMUnavailable(LLMError):
    """No key configured, network down, or the response was unusable. Stop trying."""


@runtime_checkable
class LLMProvider(Protocol):
    """A provider returns parsed JSON conforming to `schema`, or raises.

    `schema` is a JSON Schema object. Providers that support constrained decoding must use
    it (so conformance is guaranteed, not hoped for); providers that don't must validate
    the response themselves and raise LLMUnavailable rather than return something off-shape.
    """

    name: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "response",
        max_tokens: int = 1024,
    ) -> dict[str, Any]: ...

    def is_configured(self) -> bool: ...
