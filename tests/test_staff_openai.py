import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

import annonce
import iastaff
from utils.openai_generation import OpenAIGenerationError


MESSAGES = [{"role": "user", "content": [{"type": "input_text", "text": "Bonjour"}]}]


def _staff(client):
    cog = object.__new__(iastaff.IAStaff)
    cog.backend = "openai"
    cog.client = client
    cog.model = "gpt-5-mini"
    cog.enable_tools = True
    return cog


def _fake_client(*, response=None, chat_response=None, error=None):
    return SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=response, side_effect=error)),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=chat_response, side_effect=error))),
    )


@pytest.mark.parametrize("caller", ["staff", "annonce"])
@pytest.mark.asyncio
async def test_actual_sdk_serializes_compatible_parameters_and_reads_text_blocks(monkeypatch, caller):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(iastaff, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(iastaff, "VECTOR_STORE_ID", "")
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/responses"
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 0,
            "model": "gpt-5-mini", "status": "completed",
            "output": [{
                "type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Annonce prête", "annotations": []}],
            }],
        })

    async with AsyncOpenAI(
        api_key="test-placeholder", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
    ) as client:
        if caller == "staff":
            result = await _staff(client)._ask_model(MESSAGES)
        else:
            cog = object.__new__(annonce.AnnonceCog)
            cog.client, cog.model = client, "gpt-5-mini"
            result = await cog._call_openai(system_prompt="System", user_prompt="Bonjour")

    assert result == "Annonce prête"
    assert len(requests) == 1
    assert requests[0]["store"] is False
    assert requests[0]["max_output_tokens"] > 0
    assert requests[0]["reasoning"] == {"effort": "low"}
    assert "temperature" not in requests[0]


def test_staff_file_search_uses_responses_tool_contract(monkeypatch):
    monkeypatch.setattr(iastaff, "VECTOR_STORE_ID", "vs_staff_documents")
    monkeypatch.setattr(iastaff, "ENABLE_WEB_SEARCH", False)

    request = _staff(_fake_client())._build_request(MESSAGES)

    assert request["tools"] == [{"type": "file_search", "vector_store_ids": ["vs_staff_documents"]}]
    assert "tool_resources" not in request


@pytest.mark.parametrize("caller", ["staff", "annonce"])
@pytest.mark.asyncio
async def test_text_call_failure_is_not_replayed(monkeypatch, caller):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    client = _fake_client(error=TimeoutError("private-prompt-secret-token"))

    with pytest.raises(OpenAIGenerationError, match="délai") as failure:
        if caller == "staff":
            await _staff(client)._ask_model(MESSAGES)
        else:
            cog = object.__new__(annonce.AnnonceCog)
            cog.client, cog.model = client, "gpt-5-mini"
            await cog._call_openai(system_prompt="System", user_prompt="Bonjour")

    assert "private-prompt-secret-token" not in str(failure.value)
    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.parametrize("search_enabled", [False, True])
@pytest.mark.asyncio
async def test_staff_reuses_paid_router_text_only_when_no_retrieval_needed(monkeypatch, search_enabled):
    monkeypatch.setattr(iastaff, "ENABLE_WEB_SEARCH", search_enabled)
    monkeypatch.setattr(iastaff, "VECTOR_STORE_ID", "")
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content="Bonjour à toi", tool_calls=[])
    )])
    client = _fake_client(chat_response=response)
    cog = _staff(client)

    result = await cog._try_chat_with_tools(SimpleNamespace(), MESSAGES)

    assert result == (None if search_enabled else "Bonjour à toi")
    client.chat.completions.create.assert_awaited_once()
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["store"] is False
    assert kwargs["max_completion_tokens"] == iastaff.MAX_OUTPUT_TOKENS
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_staff_does_not_dispatch_truncated_tool_calls():
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="length",
        message=SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name="list_activities", arguments="{}"
        ))]),
    )])
    cog = _staff(_fake_client(chat_response=response))
    cog._dispatch_command_tool = AsyncMock()

    with pytest.raises(OpenAIGenerationError, match="incomplète"):
        await cog._try_chat_with_tools(SimpleNamespace(), MESSAGES)

    cog._dispatch_command_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_staff_routes_modern_chat_tools_with_none_despite_high_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setattr(iastaff, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(iastaff, "VECTOR_STORE_ID", "")
    client = _fake_client(chat_response=SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content="Bonjour à toi", tool_calls=[])
    )]))
    cog = _staff(client)
    cog.model = "gpt-5.6-luna"

    assert await cog._try_chat_with_tools(SimpleNamespace(), MESSAGES) == "Bonjour à toi"
    assert client.chat.completions.create.await_args.kwargs["reasoning_effort"] == "none"
    client.responses.create.assert_not_awaited()


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol"])
@pytest.mark.asyncio
async def test_staff_explains_responses_only_tools_without_sending_an_invalid_request(model):
    client = _fake_client()
    cog = _staff(client)
    cog.model = model

    with pytest.raises(OpenAIGenerationError, match="exige Responses"):
        await cog._try_chat_with_tools(SimpleNamespace(), MESSAGES)

    client.chat.completions.create.assert_not_awaited()
    client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_staff_handles_router_error_without_a_second_generation():
    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    client = _fake_client(error=TimeoutError("private-prompt-secret-token"))
    cog = _staff(client)
    cog.locks = {}
    cog._build_channel_context = AsyncMock(return_value="")
    cog._make_messages = lambda *args: MESSAGES
    cog._ask_model = AsyncMock()
    ctx = SimpleNamespace(message=None, typing=Typing, reply=AsyncMock(), author=SimpleNamespace(id=7))
    # This test isolates SDK failure handling; authorisation is covered with
    # actual command dispatch in tests_security/test_iastaff_policy.py.
    cog._authorised_context = AsyncMock(return_value=ctx)

    await cog.handle_staff_message(SimpleNamespace(id=42), SimpleNamespace(id=7), "Bonjour", ctx=ctx)

    client.chat.completions.create.assert_awaited_once()
    cog._ask_model.assert_not_awaited()
    ctx.reply.assert_awaited_once()
    reply = ctx.reply.await_args.args[0]
    assert "délai" in reply
    assert "private-prompt-secret-token" not in reply


@pytest.mark.asyncio
async def test_morning_failure_uses_local_fallback_without_repeated_calls():
    client = _fake_client(error=TimeoutError("private-prompt-secret-token"))
    cog = _staff(client)
    cog._current_morning_datetime = lambda: datetime(2026, 10, 8, tzinfo=timezone.utc)
    cog._render_morning_prompt = lambda now: "Bonjour"
    cog.morning_model = "gpt-5-mini"
    cog.morning_system_prompt = "Rédige un bonjour."
    cog.morning_max_tokens = 450
    cog.morning_temperature = 0.75

    assert await cog._generate_morning_message() == ""

    client.responses.create.assert_awaited_once()
    client.chat.completions.create.assert_not_awaited()
