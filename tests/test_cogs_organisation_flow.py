from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import discord

from cogs import organisation as organisation_cog


def make_info(**overrides):
    base = dict(
        type_category="donjon",
        type_label="Donjon",
        title="Sortie Croca",
        when_raw="samedi 21h",
        when_dt=datetime(2025, 5, 17, 19, 0),
        duration_raw="2h",
        objective="Aller taper le croca",
        requirements="Niveau 180+",
        slots="8",
        vocal="obligatoire",
        mentions_raw="@here",
        link="https://discord.example",
    )
    return organisation_cog.CollectedInfo(**{**base, **overrides})


@pytest.mark.asyncio
async def test_organisation_fallback_without_ai(monkeypatch):
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow.console = SimpleNamespace(upsert=AsyncMock())

    flow._ensure_client = AsyncMock(return_value=False)
    flow._load_error = "OPENAI_API_KEY manquant"
    info = make_info()
    flow._collect_info = AsyncMock(return_value=info)
    flow._moderate = AsyncMock(return_value=False)
    flow._build_embed = MagicMock(return_value=discord.Embed(title="Fallback"))
    flow._call_openai = AsyncMock()

    dm_channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(
        id=42,
        display_name="Staffer",
        create_dm=AsyncMock(return_value=dm_channel),
    )
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=999),
        author=author,
        reply=AsyncMock(),
    )

    thread_send = AsyncMock()
    thread = SimpleNamespace(send=thread_send)
    message = SimpleNamespace(
        id=123,
        guild=ctx.guild,
        channel=SimpleNamespace(id=555),
        jump_url="https://discord.test/123",
        created_at=datetime(2025, 5, 17, 18, 0),
        create_thread=AsyncMock(return_value=thread),
    )
    channel_send = AsyncMock(return_value=message)
    channel = SimpleNamespace(
        id=111,
        mention="#organisation",
        send=channel_send,
    )
    flow._resolve_channel = MagicMock(return_value=channel)

    monkeypatch.setattr(organisation_cog, "CREATE_THREAD", True)

    await organisation_cog.OrganisationFlow.organisation(flow, ctx)

    flow._collect_info.assert_awaited_once()
    flow._call_openai.assert_not_awaited()
    flow._moderate.assert_awaited_once()

    dm_messages = [call.args[0] for call in dm_channel.send.await_args_list]
    assert any("IA indisponible" in msg for msg in dm_messages)

    channel_send.assert_awaited_once()
    message.create_thread.assert_awaited_once()
    thread_send.assert_awaited_once_with("Repondez ici pour vous inscrire !")

    flow.console.upsert.assert_awaited_once()
    record = flow.console.upsert.await_args.args[0]
    assert record["event_id"] == 123
    assert record["ai_used"] is False
    assert record["title"] == info.title
    assert record["link"] == info.link


@pytest.mark.asyncio
async def test_organisation_with_ai_success(monkeypatch):
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow.console = SimpleNamespace(upsert=AsyncMock())

    flow._ensure_client = AsyncMock(return_value=True)
    info = make_info(mentions_raw="aucun")
    flow._collect_info = AsyncMock(return_value=info)
    flow._moderate = AsyncMock(return_value=False)
    flow._call_openai = AsyncMock(return_value={
        "title": "Annonce Propre",
        "body": "Texte reformule",
        "cta": "Inscris-toi !",
    })
    flow._build_embed = MagicMock(return_value=discord.Embed(title="IA"))

    dm_channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(
        id=77,
        display_name="Chef",
        create_dm=AsyncMock(return_value=dm_channel),
    )
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=321),
        author=author,
        reply=AsyncMock(),
    )

    message = SimpleNamespace(
        id=456,
        guild=ctx.guild,
        channel=SimpleNamespace(id=654),
        jump_url="https://discord.test/456",
        created_at=datetime(2025, 6, 1, 20, 30),
        create_thread=AsyncMock(),
    )
    channel_send = AsyncMock(return_value=message)
    channel = SimpleNamespace(
        id=222,
        mention="#annonces",
        send=channel_send,
    )
    flow._resolve_channel = MagicMock(return_value=channel)

    monkeypatch.setattr(organisation_cog, "CREATE_THREAD", False)

    await organisation_cog.OrganisationFlow.organisation(flow, ctx)

    flow._call_openai.assert_awaited_once()
    channel_send.assert_awaited_once()
    message.create_thread.assert_not_awaited()

    flow.console.upsert.assert_awaited_once()
    record = flow.console.upsert.await_args.args[0]
    assert record["ai_used"] is True
    assert record["title"] == "Annonce Propre"
    assert record["objective"] == info.objective
    assert "when_iso" in record

    mod_arg = flow._moderate.await_args.args[0]
    assert "Texte reformule" in mod_arg

    dm_messages = [call.args[0] for call in dm_channel.send.await_args_list]
    assert any("Annonce publiee" in contenu for contenu in dm_messages)


@pytest.mark.asyncio
async def test_call_openai_temperature_error_disables_custom_temperature():
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow._client = SimpleNamespace()
    error = Exception(
        "Unsupported value: 'temperature' does not support 0.25 with this model. Only the default (1) value is supported."
    )
    success = SimpleNamespace(output_text='{"title": "Ok", "tagline": "Body"}')
    create_mock = AsyncMock(side_effect=[error, success])
    flow._client.responses = SimpleNamespace(create=create_mock)

    result = await flow._call_openai({"foo": "bar"})

    assert result["title"] == "Ok"
    assert flow._supports_temperature is False
    assert flow._temperature_mode == "disabled"

    assert len(create_mock.await_args_list) == 2
    first_kwargs = create_mock.await_args_list[0].kwargs
    second_kwargs = create_mock.await_args_list[1].kwargs
    assert "inference_config" in first_kwargs
    assert "temperature" not in second_kwargs
    assert "inference_config" not in second_kwargs


@pytest.mark.asyncio
async def test_call_openai_parses_structured_json_output():
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow._client = SimpleNamespace()
    flow._supports_temperature = False

    block = SimpleNamespace(
        type="json",
        json={"title": "Struct", "tagline": "From block", "cta": "Join"},
    )
    message = SimpleNamespace(type="message", content=[block])
    response = SimpleNamespace(output_text="", output=[message])
    create_mock = AsyncMock(return_value=response)
    flow._client.responses = SimpleNamespace(create=create_mock)

    result = await flow._call_openai({"foo": "bar"})

    assert result == {"title": "Struct", "tagline": "From block", "cta": "Join"}
    create_mock.assert_awaited()



@pytest.mark.asyncio
async def test_call_openai_handles_text_object_with_value():
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow._client = SimpleNamespace()
    flow._supports_temperature = False

    class TextObj:
        def __init__(self, value: str) -> None:
            self.value = value

    block = SimpleNamespace(type="output_text", text=TextObj("{\"title\": \"Value\", \"tagline\": \"From value\"}"))
    message = SimpleNamespace(type="message", content=[block])
    response = SimpleNamespace(output_text=None, output=[message])
    flow._client.responses = SimpleNamespace(create=AsyncMock(return_value=response))

    result = await flow._call_openai({"foo": "bar"})

    assert result["title"] == "Value"
    assert result["tagline"] == "From value"



@pytest.mark.asyncio
async def test_call_openai_inference_config_typeerror_switches_to_legacy():
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow._client = SimpleNamespace()

    type_error = TypeError("inference_config parameter is not supported")
    success = SimpleNamespace(output_text='{"title": "Legacy", "tagline": "Fallback"}')
    create_mock = AsyncMock(side_effect=[type_error, success])
    flow._client.responses = SimpleNamespace(create=create_mock)

    result = await flow._call_openai({"foo": "bar"})

    assert result["title"] == "Legacy"
    assert flow._temperature_mode == "legacy"
    assert flow._supports_temperature is True

    assert len(create_mock.await_args_list) == 2
    first_kwargs = create_mock.await_args_list[0].kwargs
    second_kwargs = create_mock.await_args_list[1].kwargs
    assert "inference_config" in first_kwargs
    assert "temperature" not in first_kwargs
    assert second_kwargs.get("temperature") == organisation_cog.TEMPERATURE
    assert "inference_config" not in second_kwargs


@pytest.mark.asyncio
async def test_call_openai_builds_messages_with_system_and_user():
    bot = MagicMock()
    flow = organisation_cog.OrganisationFlow(bot=bot)
    flow._client = SimpleNamespace()
    flow._supports_temperature = False

    response = SimpleNamespace(output_text='{"title": "Struct", "tagline": "Body"}')
    create_mock = AsyncMock(return_value=response)
    flow._client.responses = SimpleNamespace(create=create_mock)

    payload = {"type": "Donjon", "title": "Test"}
    result = await flow._call_openai(payload)

    assert result["title"] == "Struct"

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    messages = call_kwargs["input"]
    assert isinstance(messages, list) and len(messages) == 2
    system_msg, user_msg = messages
    assert system_msg["role"] == "system"
    assert user_msg["role"] == "user"
    system_content = system_msg["content"][0]["text"]
    assert organisation_cog.OrganisationFlow._system_prompt(flow)[:20] in system_content
    user_text = user_msg["content"][0]["text"]
    assert json.loads(user_text)["title"] == "Test"
    assert call_kwargs.get("response_format", {}).get("type") == "json_schema"


