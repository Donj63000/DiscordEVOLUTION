from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from utils.openai_config import build_async_openai_client
from utils.openai_generation import (
    OpenAIGenerationError,
    extract_generated_text,
    generate_openai_text,
    generation_options,
)


class FakeAPIError(Exception):
    def __init__(self, status_code, *, body=None):
        super().__init__("private-prompt-secret-token")
        self.status_code = status_code
        self.body = body


def _client(*, response=None, error=None):
    responses = AsyncMock(return_value=response, side_effect=error)
    chat = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="Texte du proxy"))
    ]))
    return SimpleNamespace(
        responses=SimpleNamespace(create=responses),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat)),
    )


async def _generate(client, *, tools=False, timeout=1):
    request = {"model": "gpt-5-mini", "input": "bonjour"}
    if tools:
        request["tools"] = [{"type": "web_search"}]
    return await generate_openai_text(
        client,
        response_request=request,
        chat_request={"model": "gpt-5-mini", "messages": [{"role": "user", "content": "bonjour"}]},
        timeout=timeout,
    )


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-5-mini-2025-08-07", "o3", "gpt-4o-mini"])
def test_generation_options_bound_reasoning_and_respect_model_parameters(monkeypatch, chat, model):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)

    result = generation_options(model, 1800, 0.6, chat=chat)

    assert result["max_completion_tokens" if chat else "max_output_tokens"] == 1800
    assert result["store"] is False
    assert "max_tokens" not in result
    assert ("temperature" in result) == (model == "gpt-4o-mini")
    if model.startswith("gpt-5-mini"):
        assert result["reasoning_effort" if chat else "reasoning"] == ("low" if chat else {"effort": "low"})


def test_generation_options_preserve_explicit_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    assert generation_options("gpt-5-mini", 1800, 0.6)["reasoning"] == {"effort": "medium"}


@pytest.mark.parametrize("effort", ["", "high"])
@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.4-mini", "gpt-5.6-luna",
                                    "gpt-6-sol", "gpt-6-luna", "gpt-6-luna-2026-08-20"])
def test_modern_chat_tools_disable_reasoning_only_for_the_router(monkeypatch, effort, model):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", effort)

    routed = generation_options(model, 1800, 0.6, chat=True, tool_calling=True)
    response = generation_options(model, 1800, 0.6)
    plain_chat = generation_options(model, 1800, 0.6, chat=True)

    assert routed["reasoning_effort"] == "none"
    assert "max_tokens" not in routed and routed["store"] is False
    assert response.get("reasoning") != {"effort": "none"}
    assert plain_chat.get("reasoning_effort") != "none"
    if model.startswith("gpt-5") and effort:
        assert response["reasoning"] == {"effort": "high"}
        assert plain_chat["reasoning_effort"] == "high"


@pytest.mark.parametrize("effort, expected", [("", "low"), ("high", "high")])
def test_original_gpt5_chat_tools_preserve_their_effort(monkeypatch, effort, expected):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", effort)
    assert generation_options("gpt-5-mini", 1800, 0.6, chat=True, tool_calling=True)["reasoning_effort"] == expected


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-sol-2026-10-01"])
def test_responses_only_tool_models_keep_plain_text_available(model):
    with pytest.raises(OpenAIGenerationError, match="exige Responses"):
        generation_options(model, 1800, 0.6, chat=True, tool_calling=True)
    assert generation_options(model, 1800, 0.6)["max_output_tokens"] == 1800
    assert generation_options(model, 1800, 0.6, chat=True)["max_completion_tokens"] == 1800


def test_client_retry_policy_is_explicit_and_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory = lambda **kwargs: kwargs

    explicit = build_async_openai_client(factory, timeout=45, max_retries=0)
    unchanged = build_async_openai_client(factory)

    assert explicit["timeout"] == 45
    assert explicit["max_retries"] == 0
    assert "max_retries" not in unchanged


def test_extracts_output_blocks_without_tool_or_reasoning_content():
    response = {
        "output": [
            {"type": "reasoning", "content": [{"type": "text", "text": "private reasoning"}]},
            {"type": "file_search_call", "results": [{"text": "private document"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Réponse finale"}]},
        ]
    }
    assert extract_generated_text(response) == "Réponse finale"


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502, 503])
@pytest.mark.asyncio
async def test_failed_generation_is_not_replayed_or_exposed(status, caplog):
    client = _client(error=FakeAPIError(status))

    with pytest.raises(OpenAIGenerationError) as failure:
        await _generate(client)

    assert "private-prompt-secret-token" not in str(failure.value)
    assert "private-prompt-secret-token" not in caplog.text
    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_model_is_not_treated_as_missing_responses_endpoint():
    client = _client(error=FakeAPIError(404, body={"code": "model_not_found", "param": "model"}))

    with pytest.raises(OpenAIGenerationError, match="configuration"):
        await _generate(client)

    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.parametrize("status", [404, 405, 501])
@pytest.mark.asyncio
async def test_explicit_endpoint_rejection_allows_chat_compatibility(status):
    client = _client(error=FakeAPIError(status))

    assert await _generate(client) == "Texte du proxy"

    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_sdk_without_responses_uses_chat_directly():
    client = _client()
    del client.responses

    assert await _generate(client) == "Texte du proxy"
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_responses_support_does_not_drop_search_tools():
    client = _client(error=FakeAPIError(404))

    with pytest.raises(OpenAIGenerationError, match="recherche IA"):
        await _generate(client, tools=True)

    client.chat.completions.create.assert_not_awaited()


@pytest.mark.parametrize("response", [
    {"status": "completed", "output": []},
    {"status": "incomplete", "output_text": "Annonce coupée..."},
    {"status": "failed", "error": {"message": "private-prompt-secret-token"}},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "non"}]}]},
    {"choices": [{"finish_reason": "length", "message": {"content": "Annonce coupée..."}}]},
])
@pytest.mark.asyncio
async def test_empty_partial_failed_or_refused_response_is_never_regenerated(response):
    client = _client(response=response)

    with pytest.raises(OpenAIGenerationError) as failure:
        await _generate(client)

    assert "private-prompt-secret-token" not in str(failure.value)
    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_deadline_cancels_generation_without_fallback():
    import asyncio

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = _client()
    client.responses.create.side_effect = pending

    with pytest.raises(OpenAIGenerationError, match="délai"):
        await _generate(client, timeout=0.01)

    assert entered.is_set() and cancelled.is_set()
    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_cancellation_propagates_without_second_call():
    import asyncio

    client = _client(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _generate(client)

    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_sdk_rate_limit_does_not_retry(monkeypatch):
    from openai import AsyncOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "private-prompt-secret-token", "type": "rate_limit_error"}})

    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))

    async with build_async_openai_client(factory, timeout=1, max_retries=0) as client:
        with pytest.raises(OpenAIGenerationError, match="Limite"):
            await _generate(client)

    assert len(calls) == 1
