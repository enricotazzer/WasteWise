"""Gemini free tier — the failover when Groq rate-limits.

Gemini's `responseSchema` is a restricted dialect of JSON Schema: it wants an OpenAPI-style
subset and rejects `additionalProperties`, which Groq's strict mode *requires*. Callers
write one plain schema and this module adapts it, so no caller has to know which provider
served the request.
"""

import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.services.llm.base import USE_SETTINGS, LLMRateLimited, LLMUnavailable

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Keys Gemini's schema dialect rejects outright.
_UNSUPPORTED_KEYS = {"additionalProperties", "$schema", "definitions", "$defs"}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip the JSON Schema keywords Gemini's responseSchema doesn't accept."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: to_gemini_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = to_gemini_schema(value)
        else:
            out[key] = value
    return out


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key=USE_SETTINGS, model: str | None = None) -> None:
        # See GroqProvider: `api_key=None` means "no key", not "read settings".
        self._api_key = settings.gemini_api_key if api_key is USE_SETTINGS else api_key
        self.model = model or settings.gemini_model

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
            raise LLMUnavailable("GEMINI_API_KEY is not set")

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema),
            },
        }

        try:
            resp = httpx.post(
                f"{BASE_URL}/{self.model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": self._api_key},
                timeout=settings.llm_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"gemini request failed: {exc}") from exc

        if resp.status_code == 429:
            raise LLMRateLimited("gemini rate limit reached")
        if resp.status_code >= 500:
            raise LLMRateLimited(f"gemini {resp.status_code}")
        if resp.status_code != 200:
            raise LLMUnavailable(f"gemini {resp.status_code}: {resp.text[:200]}")

        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return json.loads("".join(p.get("text", "") for p in parts))
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMUnavailable(f"gemini returned unparseable content: {exc}") from exc
