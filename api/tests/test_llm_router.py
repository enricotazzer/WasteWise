"""LLM provider layer.

Free tiers rate-limit as a matter of routine, so failover is a normal code path rather than
an error path — and "everything failed" has to be a clean, catchable outcome, because every
caller in the app has a non-LLM fallback behind it.
"""

import httpx
import pytest
import respx

from app.services.llm.base import LLMRateLimited, LLMUnavailable
from app.services.llm.gemini import GeminiProvider, to_gemini_schema
from app.services.llm.groq import ENDPOINT as GROQ_ENDPOINT
from app.services.llm.groq import GroqProvider
from app.services.llm.router import FailoverLLM

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _call(provider):
    return provider.complete_json(system="s", user="u", schema=SCHEMA)


class _Stub:
    def __init__(self, name, result=None, raises=None, configured=True):
        self.name, self._result, self._raises, self._configured = name, result, raises, configured
        self.calls = 0

    def is_configured(self):
        return self._configured

    def complete_json(self, **kwargs):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._result


# --- failover ---


def test_first_working_provider_wins_and_the_second_is_never_called():
    a, b = _Stub("a", {"ok": True}), _Stub("b", {"ok": False})
    llm = FailoverLLM([a, b])
    assert _call(llm) == {"ok": True}
    assert (a.calls, b.calls) == (1, 0)
    assert llm.last_provider == "a"


def test_rate_limit_fails_over_to_the_next_provider():
    a = _Stub("a", raises=LLMRateLimited("429"))
    b = _Stub("b", {"ok": True})
    llm = FailoverLLM([a, b])
    assert _call(llm) == {"ok": True}
    assert llm.last_provider == "b"


def test_unconfigured_providers_are_skipped_without_being_called():
    a = _Stub("a", configured=False)
    b = _Stub("b", {"ok": True})
    assert _call(FailoverLLM([a, b])) == {"ok": True}
    assert a.calls == 0


def test_all_providers_failing_raises_one_catchable_error():
    a = _Stub("a", raises=LLMRateLimited("429"))
    b = _Stub("b", raises=LLMUnavailable("bad key"))
    with pytest.raises(LLMUnavailable) as exc:
        _call(FailoverLLM([a, b]))
    # Both causes are named: debugging a silent free-tier outage is otherwise miserable.
    assert "a:" in str(exc.value) and "b:" in str(exc.value)


def test_no_providers_configured_is_reported_clearly():
    llm = FailoverLLM([_Stub("a", configured=False)])
    assert llm.is_configured() is False
    with pytest.raises(LLMUnavailable, match="no LLM provider"):
        _call(llm)


# --- groq ---


def test_groq_without_a_key_raises_instead_of_calling_out():
    with pytest.raises(LLMUnavailable, match="GROQ_API_KEY"):
        _call(GroqProvider(api_key=None))


@respx.mock
def test_groq_requests_strict_schema_enforcement():
    """Strict mode is the whole reason for the gpt-oss model choice — if this flag is lost,
    conformance silently degrades to best-effort."""
    route = respx.post(GROQ_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )
    )
    assert _call(GroqProvider(api_key="k")) == {"ok": True}
    sent = route.calls[0].request
    body = sent.read().decode()
    assert '"strict": true' in body or '"strict":true' in body
    assert sent.headers["authorization"] == "Bearer k"


@respx.mock
def test_groq_429_is_a_failover_signal_not_a_hard_failure():
    respx.post(GROQ_ENDPOINT).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(LLMRateLimited):
        _call(GroqProvider(api_key="k"))


@respx.mock
def test_groq_5xx_also_triggers_failover():
    respx.post(GROQ_ENDPOINT).mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(LLMRateLimited):
        _call(GroqProvider(api_key="k"))


@respx.mock
def test_groq_401_is_terminal_and_does_not_retry_forever():
    respx.post(GROQ_ENDPOINT).mock(return_value=httpx.Response(401, text="invalid key"))
    with pytest.raises(LLMUnavailable):
        _call(GroqProvider(api_key="k"))


@respx.mock
def test_groq_non_json_content_is_unavailable_not_a_crash():
    respx.post(GROQ_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "sorry!"}}]})
    )
    with pytest.raises(LLMUnavailable):
        _call(GroqProvider(api_key="k"))


# --- gemini ---


def test_gemini_schema_strips_keys_its_dialect_rejects():
    """Gemini rejects additionalProperties, which Groq's strict mode requires. Callers write
    one schema; the adapter reconciles the two."""
    out = to_gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "a": {"type": "string", "additionalProperties": False},
                "b": {"type": "array", "items": {"type": "object", "additionalProperties": False}},
            },
        }
    )
    assert "additionalProperties" not in out
    assert "additionalProperties" not in out["properties"]["a"]
    assert "additionalProperties" not in out["properties"]["b"]["items"]
    assert out["properties"]["a"]["type"] == "string"


@respx.mock
def test_gemini_parses_a_response():
    respx.post(url__regex=r".*generativelanguage.*").mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
        )
    )
    assert _call(GeminiProvider(api_key="k")) == {"ok": True}


@respx.mock
def test_gemini_429_is_a_rate_limit():
    respx.post(url__regex=r".*generativelanguage.*").mock(
        return_value=httpx.Response(429, text="quota")
    )
    with pytest.raises(LLMRateLimited):
        _call(GeminiProvider(api_key="k"))
