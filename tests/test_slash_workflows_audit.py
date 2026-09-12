"""Régressions métier des parcours slash sans API, réseau ni données réelles."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

import activite
import organisation
import sondage
from test_slash_commands import AUTHOR_ID, make_interaction, slash_bot
from utils.slash_errors import send_interaction_error


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["offline", "gemini", "openai", "both"])
async def test_real_startup_loads_only_explicitly_enabled_ai(monkeypatch, provider):
    # Seule la frontière du serveur de santé est isolée ; setup_hook est le code réel.
    fake_alive = ModuleType("alive")
    fake_alive.keep_alive = Mock()
    monkeypatch.setitem(sys.modules, "alive", fake_alive)
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("IASTAFF_BACKEND", "openai")
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "0" if provider == "offline" else "1")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key" if provider in {"gemini", "both"} else "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key" if provider in {"openai", "both"} else "")
    spec = importlib.util.spec_from_file_location("_main_slash_audit", Path(__file__).parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bot = SimpleNamespace(
        remove_command=Mock(), _safe_load=AsyncMock(return_value=True),
        _load_iastaff_anywhere=AsyncMock(), _sync_app_commands=AsyncMock(), commands=[],
    )
    try:
        await module.EvoBot.setup_hook(bot)
        loaded = [call.args[0] for call in bot._safe_load.await_args_list]
        assert loaded[-1] == "slash_commands"
        assert ("ia" in loaded) == (provider in {"gemini", "both"})
        assert bool(bot._load_iastaff_anywhere.await_count) == (provider in {"openai", "both"})
        assert "slash_events" not in loaded
        assert "event_conversation" in loaded and "cogs.annonce_ai" in loaded
        if "ia" in loaded:
            assert loaded.index("ia") < loaded.index("cogs.annonce_ai")
        bot._sync_app_commands.assert_awaited_once()
        fake_alive.keep_alive.assert_not_called()
    finally:
        await module.bot.close()


@pytest.mark.asyncio
async def test_organisation_uses_template_without_api_even_if_old_client_exists(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    cog = organisation.OrganisationCog(SimpleNamespace())
    cog._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    session = organisation.OrganisationSession(
        user_id=1, guild_id=100, channel_id=300, context={"guild": "Test"},
        collected={"event_type": "Donjon", "date_time": "Demain 20h"}, summary="Donjon",
    )
    payload = await cog._generate_announcement(session, organiser="Staff", channel=SimpleNamespace(name="organisation"))
    assert payload["title"] and payload["body"]
    cog._client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_error_finishes_public_defer_before_private_detail(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="perco"))
    interaction.response.is_done.return_value = True
    interaction.response.type = discord.InteractionResponseType.deferred_channel_message
    interaction.original_response.return_value = SimpleNamespace(flags=SimpleNamespace(loading=True, ephemeral=False))
    assert await send_interaction_error(interaction, "Détail privé")
    assert "Détail privé" not in interaction.edit_original_response.call_args.kwargs["content"]
    assert interaction.followup.send.call_args.kwargs["ephemeral"]


@pytest_asyncio.fixture
async def poll_env():
    # Aucune boucle de surveillance ne doit concurrencer le test.
    sondage.POLL_STORAGE.clear()
    channel = SimpleNamespace(id=200, guild=SimpleNamespace(id=100), fetch_message=AsyncMock(), send=AsyncMock())
    cog = sondage.SondageCog(SimpleNamespace(get_channel=lambda identifier: channel if identifier == 200 else None))
    cog.poll_watcher.cancel()
    cog._save_polls_to_console = AsyncMock(return_value=True)
    sondage.POLL_STORAGE[123] = {
        "title": "Sortie", "choices": ["Oui", "Non"], "channel_id": 200, "guild_id": 100,
        "author_id": 1, "end_time_ts": 0,
    }
    yield cog, channel
    cog.cog_unload()
    sondage.POLL_STORAGE.clear()


def http_error(kind=discord.Forbidden, status=403):
    return kind(SimpleNamespace(status=status, reason="Test"), {"message": "test", "code": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["fetch", "edit"])
async def test_poll_close_http_failure_preserves_tracking(poll_env, step):
    cog, channel = poll_env
    message = SimpleNamespace(reactions=[], edit=AsyncMock())
    channel.fetch_message.return_value = message
    if step == "fetch":
        channel.fetch_message.side_effect = http_error()
    else:
        message.edit.side_effect = http_error()
    assert not await cog._finish_poll(123)
    assert 123 in sondage.POLL_STORAGE
    cog._save_polls_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_poll_can_be_removed_from_tracking(poll_env):
    cog, channel = poll_env
    channel.fetch_message.side_effect = http_error(discord.NotFound, 404)
    assert await cog._finish_poll(123)
    assert 123 not in sondage.POLL_STORAGE


@pytest.mark.asyncio
async def test_poll_closure_is_serialized_and_does_not_publish_duplicate_results(poll_env):
    cog, channel = poll_env
    message = SimpleNamespace(reactions=[], edit=AsyncMock())
    channel.fetch_message.return_value = message
    assert await asyncio.gather(cog._finish_poll(123), cog._finish_poll(123)) == [True, True]
    message.edit.assert_awaited_once()
    channel.send.assert_not_awaited()
    cog._save_polls_to_console.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mine,expected", [(True, 2), (False, 3)])
async def test_poll_vote_count_only_subtracts_an_existing_bot_reaction(poll_env, mine, expected):
    cog, channel = poll_env
    message = SimpleNamespace(reactions=[SimpleNamespace(emoji="🇦", count=3, me=mine)], edit=AsyncMock())
    channel.fetch_message.return_value = message
    assert await cog.close_poll(123)
    assert f"{expected} vote(s)" in message.edit.call_args.kwargs["embed"].description


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_poll_cannot_be_closed_from_another_guild_even_by_its_author(poll_env, legacy):
    cog, channel = poll_env
    if legacy:
        sondage.POLL_STORAGE[123].pop("guild_id")
    ctx = SimpleNamespace(guild=SimpleNamespace(id=999), author=SimpleNamespace(id=1), send=AsyncMock())
    cog._finish_poll = AsyncMock()
    await cog.manual_close_poll.callback(cog, ctx, 123)
    cog._finish_poll.assert_not_awaited()
    assert "serveur" in ctx.send.call_args.args[0]


@pytest.mark.asyncio
async def test_poll_is_saved_before_reactions_and_remains_tracked_if_reactions_fail(poll_env, monkeypatch):
    cog, channel = poll_env
    sondage.POLL_STORAGE.clear()
    message = SimpleNamespace(id=456, jump_url="https://discord.com/channels/100/200/456",
                              edit=AsyncMock(), add_reaction=AsyncMock())
    channel.send.return_value = message

    async def react(emoji):
        assert 456 in sondage.POLL_STORAGE
        cog._save_polls_to_console.assert_awaited_once()
        raise http_error()

    message.add_reaction.side_effect = react
    monkeypatch.setattr(sondage, "resolve_text_channel", lambda *args, **kwargs: channel)
    ctx = SimpleNamespace(
        guild=channel.guild, author=SimpleNamespace(id=1, display_name="Test", display_avatar=None),
        interaction=object(), command=cog.create_sondage, send=AsyncMock(),
    )
    await cog.create_sondage.callback(cog, ctx, args="Question ; Oui ; Non")
    assert 456 in sondage.POLL_STORAGE
    assert any("publié" in str(call) for call in ctx.send.await_args_list)
    assert "@everyone" not in channel.send.call_args.args[0]


@pytest.mark.asyncio
async def test_activity_create_uses_structured_fields_instead_of_splitting_a_date_in_title(slash_bot, monkeypatch):
    monkeypatch.setattr(activite.ActiviteCog, "cog_load", AsyncMock())
    cog = activite.ActiviteCog(slash_bot)
    cog.initialized = True
    cog.activities_data = {"events": {}, "next_id": 1}
    cog.save_data_local = AsyncMock()
    cog.dump_data_to_console = AsyncMock()
    cog._resolve_organisation_channel = lambda guild: None
    role = SimpleNamespace(id=333)
    ctx = SimpleNamespace(
        slash_values={"titre": "Suite du 01/01/2098 10:00", "date": "01/01/2099 21:00",
                      "description": "Première ligne\nDeuxième ligne"},
        author=SimpleNamespace(id=1, add_roles=AsyncMock()),
        guild=SimpleNamespace(create_role=AsyncMock(return_value=role)), send=AsyncMock(),
    )
    try:
        await cog.command_creer(ctx, "argument déjà validé")
        stored = cog.activities_data["events"]["1"]
        assert stored["titre"] == ctx.slash_values["titre"]
        assert stored["date_str"] == "2099-01-01 21:00:00"
        assert stored["description"] == ctx.slash_values["description"]
    finally:
        cog.cog_unload()


@pytest.mark.asyncio
async def test_activity_modifier_keeps_description_when_omitted(slash_bot, monkeypatch):
    monkeypatch.setattr(activite.ActiviteCog, "cog_load", AsyncMock())
    cog = activite.ActiviteCog(slash_bot)
    cog.initialized = True
    event = activite.ActiviteData("1", "Titre", activite.datetime(2099, 1, 1, 21), "À conserver", 1, 333)
    cog.activities_data = {"events": {"1": event.to_dict()}}
    cog.save_data_local = AsyncMock()
    cog.dump_data_to_console = AsyncMock()
    cog.can_modify = lambda ctx, event: True
    ctx = SimpleNamespace(slash_values={"date": "02/01/2099 22:00", "description": None}, send=AsyncMock())
    try:
        await cog.command_modifier(ctx, "1 02/01/2099 22:00")
        assert cog.activities_data["events"]["1"]["description"] == "À conserver"
        assert cog.activities_data["events"]["1"]["date_str"] == "2099-01-02 22:00:00"
    finally:
        cog.cog_unload()
