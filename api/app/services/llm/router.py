"""Failover across free tiers.

Free tiers rate-limit as a matter of course, so this tries providers in order and moves on
when one says 429. When every provider is exhausted or unconfigured it raises
LLMUnavailable, and the caller is expected to fall back to something that doesn't need a
model at all — never to an error screen.
"""

import logging
from typing import Any

from app.services.llm.base import LLMError, LLMProvider, LLMRateLimited, LLMUnavailable
from app.services.llm.gemini import GeminiProvider
from app.services.llm.groq import GroqProvider

log = logging.getLogger(__name__)


class FailoverLLM:
    """Tries each configured provider in order. `last_provider` records who served the
    most recent successful call, so callers can attribute a result."""

    name = "failover"

    def __init__(self, providers: list[LLMProvider] | None = None) -> None:
        self._providers = providers if providers is not None else [GroqProvider(), GeminiProvider()]
        self.last_provider: str | None = None

    def is_configured(self) -> bool:
        return any(p.is_configured() for p in self._providers)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "response",
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        configured = [p for p in self._providers if p.is_configured()]
        if not configured:
            raise LLMUnavailable("no LLM provider is configured (set GROQ_API_KEY)")

        errors: list[str] = []
        for provider in configured:
            try:
                result = provider.complete_json(
                    system=system,
                    user=user,
                    schema=schema,
                    schema_name=schema_name,
                    max_tokens=max_tokens,
                )
            except LLMRateLimited as exc:
                log.warning("llm provider %s rate-limited, failing over: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            except LLMError as exc:
                log.warning("llm provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            self.last_provider = provider.name
            return result

        raise LLMUnavailable("all providers failed -> " + "; ".join(errors))


_cached: FailoverLLM | None = None


def get_llm() -> FailoverLLM:
    global _cached
    if _cached is None:
        _cached = FailoverLLM()
    return _cached


def reset_llm_cache() -> None:
    """Test hook — settings are read at construction time."""
    global _cached
    _cached = None
