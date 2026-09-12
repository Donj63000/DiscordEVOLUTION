"""Sécurité des confirmations et de la publication distante, sans accès à Discord."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext import commands

from slash_commands import SlashCommandsCog
from test_slash_commands import make_interaction, slash_bot
from utils.slash_catalog import Route
from utils.slash_confirm import ActionConfirmation, DESTRUCTIVE_ROUTES
from utils.slash_sync import (
    cleanup_retired_guild_commands, remove_retired_remote_commands, sync_application_commands,
)


def click_for(bot, command=None, *, roles=()):
    click = make_interaction(bot, command or SimpleNamespace(name="test"), roles=roles)
    click.message = discord.Message(state=bot._connection, channel=click.channel, data={
        "id": "900", "type": 0, "content": "Confirmation", "attachments": [], "embeds": [],
        "author": {"id": "777", "username": "Evolution BOT", "discriminator": "0", "avatar": None},
    })

    async def edit(**kwargs):
        click.response.is_done.return_value = True

    click.response.edit_message = AsyncMock(side_effect=edit)
    return click


@pytest.mark.asyncio
async def test_confirmation_runs_once_with_the_real_clicking_member(slash_bot):
    calls = []

    @slash_bot.command(name="erase")
    @commands.has_role("Staff")
    async def erase(ctx):
        calls.append(ctx.author.id)
        await ctx.send("Terminé")

    click = click_for(slash_bot, roles=("Staff",))
    view = ActionConfirmation(slash_bot, click, Route(("erase",), "erase", "Effacer"), "", {})
    await asyncio.gather(view.confirm.callback(click), view.confirm.callback(click))
    assert calls == [click.user.id]
    assert view._used
    assert view.is_finished()
    click.response.edit_message.assert_awaited_once()
    assert all(call.kwargs["ephemeral"] for call in click.followup.send.await_args_list)


@pytest.mark.asyncio
async def test_permission_revoked_after_opening_is_rechecked(slash_bot):
    action = Mock()

    @slash_bot.command(name="erase")
    @commands.has_role("Staff")
    async def erase(ctx):
        action()

    opening = click_for(slash_bot, roles=("Staff",))
    view = ActionConfirmation(slash_bot, opening, Route(("erase",), "erase", "Effacer"), "", {})
    click = click_for(slash_bot)
    await view.confirm.callback(click)
    action.assert_not_called()
    assert "permission" in click.followup.send.call_args.kwargs["content"]
    assert view._used


@pytest.mark.asyncio
@pytest.mark.parametrize("different", ["user", "guild"])
async def test_confirmation_cannot_be_stolen(slash_bot, different):
    opening = click_for(slash_bot)
    view = ActionConfirmation(slash_bot, opening, Route(("erase",), "erase", "Effacer"), "", {})
    click = click_for(slash_bot)
    if different == "user":
        click.user = SimpleNamespace(id=999)
    else:
        click.guild_id = 999
    try:
        await view.confirm.callback(click)
        assert not view._used
        click.response.edit_message.assert_not_awaited()
        assert click.response.send_message.call_args.kwargs["ephemeral"]
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_failure_after_a_side_effect_never_replays_the_action(slash_bot):
    action = Mock()

    @slash_bot.command(name="erase")
    async def erase(ctx):
        action()
        raise RuntimeError("échec après écriture")

    click = click_for(slash_bot)
    view = ActionConfirmation(slash_bot, click, Route(("erase",), "erase", "Effacer"), "", {})
    await view.confirm.callback(click)
    await view.confirm.callback(click)
    action.assert_called_once()
    assert view._used


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "timeout"])
async def test_cancel_and_expiry_do_not_execute_business_code(slash_bot, action):
    mutation = Mock()

    @slash_bot.command(name="erase")
    async def erase(ctx):
        mutation()

    click = click_for(slash_bot)
    view = ActionConfirmation(slash_bot, click, Route(("erase",), "erase", "Effacer"), "", {})
    view.message = SimpleNamespace(edit=AsyncMock())
    if action == "cancel":
        await view.cancel.callback(click)
    else:
        await view.on_timeout()
        view.stop()
        assert view.message.edit.call_args.kwargs["view"] is None
    await view.confirm.callback(click)
    mutation.assert_not_called()


@pytest.mark.asyncio
async def test_destructive_route_only_opens_a_private_confirmation(slash_bot):
    mutation = Mock()

    @slash_bot.command(name="resetwarnings")
    async def resetwarnings(ctx):
        mutation()

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("resetwarnings")
    click = make_interaction(slash_bot, command)
    view = await command.callback(click)
    try:
        mutation.assert_not_called()
        assert click.response.send_message.call_args.kwargs["ephemeral"]
        assert ("resetwarnings",) in DESTRUCTIVE_ROUTES
    finally:
        view.stop()


@pytest.fixture
def sync_bot(monkeypatch):
    monkeypatch.delenv("ENABLE_AI_COMMANDS", raising=False)
    monkeypatch.delenv("SYNC_SLASH_COMMANDS", raising=False)
    monkeypatch.delenv("SYNC_SLASH_GUILD_ID", raising=False)
    monkeypatch.delenv("SLASH_CLEANUP_RETIRED", raising=False)
    return SimpleNamespace(
        tree=SimpleNamespace(
            sync=AsyncMock(return_value=[]), copy_global_to=Mock(), remove_command=Mock(),
            fetch_commands=AsyncMock(return_value=[]),
        ),
        guilds=[discord.Object(id=100), discord.Object(id=200)],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "-1", "0", "９９", str(2**64), "1.2"])
async def test_bad_guild_id_never_falls_back_to_global_publication(sync_bot, monkeypatch, value):
    monkeypatch.setenv("SYNC_SLASH_GUILD_ID", value)
    assert not await sync_application_commands(sync_bot)
    sync_bot.tree.sync.assert_not_awaited()
    sync_bot.tree.fetch_commands.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "off"])
async def test_sync_disabled_also_disables_every_cleanup(sync_bot, monkeypatch, value):
    monkeypatch.setenv("SYNC_SLASH_COMMANDS", value)
    assert not await sync_application_commands(sync_bot)
    await cleanup_retired_guild_commands(sync_bot)
    sync_bot.tree.sync.assert_not_awaited()
    sync_bot.tree.fetch_commands.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_sync_does_not_clear_guild_catalogs(sync_bot):
    assert await sync_application_commands(sync_bot)
    sync_bot.tree.sync.assert_awaited_once_with()
    sync_bot.tree.copy_global_to.assert_not_called()
    assert sync_bot._slash_sync_succeeded


@pytest.mark.asyncio
async def test_guild_sync_removes_stale_ai_from_copied_tree(sync_bot, monkeypatch):
    monkeypatch.setenv("SYNC_SLASH_GUILD_ID", "100")
    assert await sync_application_commands(sync_bot)
    assert sync_bot.tree.sync.call_args.kwargs["guild"].id == 100
    assert "ia" in [call.args[0] for call in sync_bot.tree.remove_command.call_args_list]
    sync_bot.tree.fetch_commands.assert_awaited_once_with(guild=None)


@pytest.mark.asyncio
async def test_remote_cleanup_is_targeted_and_preserves_context_menus(sync_bot):
    def remote(name, kind=discord.AppCommandType.chat_input):
        return SimpleNamespace(name=name, type=kind, delete=AsyncMock())

    commands_ = [
        remote("ia"), remote("event"), remote("event-rapide"), remote("calendrier"),
        remote("organisation"), remote("annonce-list"), remote("inconnue"),
        remote("ia", discord.AppCommandType.user),
    ]
    sync_bot.tree.fetch_commands.return_value = commands_
    assert await remove_retired_remote_commands(sync_bot.tree)
    assert [command.name for command in commands_ if command.delete.await_count] == ["ia", "event", "event-rapide"]


@pytest.mark.asyncio
async def test_remote_cleanup_is_not_repeated_on_every_reconnect(sync_bot):
    sync_bot._slash_sync_succeeded = True
    await cleanup_retired_guild_commands(sync_bot)
    await cleanup_retired_guild_commands(sync_bot)
    assert sync_bot.tree.fetch_commands.await_count == 2
    assert sync_bot._slash_cleanup_done == {100, 200}


@pytest.mark.asyncio
async def test_cleanup_http_failure_can_be_retried_on_reconnect(sync_bot):
    sync_bot._slash_sync_succeeded = True
    response = SimpleNamespace(status=403, reason="Forbidden")
    sync_bot.tree.fetch_commands.side_effect = discord.Forbidden(response, {"message": "test", "code": 50013})
    await cleanup_retired_guild_commands(sync_bot)
    assert sync_bot._slash_cleanup_done == set()
    sync_bot.tree.fetch_commands.side_effect = None
    await cleanup_retired_guild_commands(sync_bot)
    assert sync_bot._slash_cleanup_done == {100, 200}


@pytest.mark.asyncio
async def test_failed_sync_never_runs_remote_cleanup(sync_bot):
    response = SimpleNamespace(status=403, reason="Forbidden")
    sync_bot.tree.sync.side_effect = discord.Forbidden(response, {"message": "test", "code": 50013})
    assert not await sync_application_commands(sync_bot)
    await cleanup_retired_guild_commands(sync_bot)
    sync_bot.tree.fetch_commands.assert_not_awaited()
