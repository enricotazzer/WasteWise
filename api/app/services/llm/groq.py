"""Groq free tier via the OpenAI-compatible endpoint.

Called directly with httpx rather than through the SDK: httpx is already a dependency for
Open Food Facts, the surface used here is two fields wide, and it keeps the failure modes
(429 vs. everything else) explicit instead of wrapped.

Model choice matters. The `openai/gpt-oss-*` family is the one Groq supports with *strict*
constrained decoding, so `strict: true` genuinely guarantees the schema. Point `GROQ_MODEL`
at a model without that support and conformance silently degrades to best-effort — which is
why the response is validated here regardless.
"""

import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.services.llm.base import USE_SETTINGS, LLMRateLimited, LLMUnavailable

log = logging.getLogger(__name__)

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider:
    name = "groq"

    def __init__(self, api_key=USE_SETTINGS, model: str | None = None) -> None:
        # Passing `api_key=None` means "no key", not "fall back to settings" — otherwise the
        # unconfigured path can't be exercised on a machine that has a key configured.
        self._api_key = settings.groq_api_key if api_key is USE_SETTINGS else api_key
        self.model = model or settings.groq_model
        # Token counts from the last successful call, or None. Recorded rather than
        # estimated: `recipe_suggestions` stores them, and a token count this app invented
        # would be indistinguishable from one the provider reported.
        self.last_usage: dict[str, int] | None = None

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "response",
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise LLMUnavailable("GROQ_API_KEY is not set")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_tokens,
            # Deterministic: these are classification calls, not creative ones. Two scans of
            # the same product must not land in different categories.
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }

        try:
            resp = httpx.post(
                ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=settings.llm_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"groq request failed: {exc}") from exc

        if resp.status_code == 429:
            raise LLMRateLimited("groq rate limit reached")
        if resp.status_code >= 500:
            # Transient on their side — worth failing over rather than giving up.
            raise LLMRateLimited(f"groq {resp.status_code}")
        if resp.status_code != 200:
            raise LLMUnavailable(f"groq {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            # Strict mode should make this impossible; if it happens the model or the config
            # is not what we think it is, so surface it rather than paper over it.
            raise LLMUnavailable(f"groq returned unparseable content: {exc}") from exc

        usage = body.get("usage") or {}
        self.last_usage = (
            {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            }
            if usage
            else None
        )
        return parsed
