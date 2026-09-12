"""Exercise Discord deferred-response types, not only generic followup mocks."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from slash_commands import SlashCommandsCog
from test_slash_commands import make_interaction, slash_bot
from utils import slash_support
from utils.slash_errors import send_interaction_error


def realistic_defer(interaction):
    async def defer(*, thinking=False, ephemeral=False):
        interaction.response.is_done.return_value = True
        interaction.response.type = (
            discord.InteractionResponseType.deferred_channel_message if thinking
            else discord.InteractionResponseType.deferred_message_update
        )
    interaction.response.type = None
    interaction.response.defer = AsyncMock(side_effect=defer)


def registered_interaction(bot, callback):
    bot.command(name="activite")(callback)
    catalog = SlashCommandsCog(bot)
    catalog.register_commands()
    command = bot.tree.get_command("activite").get_command("rejoindre")
    interaction = make_interaction(bot, command)
    realistic_defer(interaction)
    return catalog, interaction


@pytest.mark.asyncio
async def test_first_private_reply_explicitly_finishes_loading_original(slash_bot):
    async def action(ctx, action=None, *, args=None):
        await ctx.send("Inscription enregistrée.")
    catalog, interaction = registered_interaction(slash_bot, action)
    try:
        await interaction.command.callback(interaction)
        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.edit_original_response.assert_awaited_once_with(content="Inscription enregistrée.")
        interaction.followup.send.assert_not_awaited()
    finally:
        catalog.cog_unload()


@pytest.mark.asyncio
async def test_file_reply_edits_original_with_attachments_not_followup(slash_bot):
    file = discord.File(io.BytesIO(b"png"), filename="activite.png")

    async def action(ctx, action=None, *, args=None):
        await ctx.send("Annonce", file=file)
        await ctx.send("Suite privée")
    catalog, interaction = registered_interaction(slash_bot, action)
    try:
        await interaction.command.callback(interaction)
        interaction.edit_original_response.assert_awaited_once_with(content="Annonce", attachments=[file])
        interaction.followup.send.assert_awaited_once()
        assert interaction.followup.send.await_args.kwargs["ephemeral"]
    finally:
        file.close()
        catalog.cog_unload()


@pytest.mark.asyncio
async def test_hung_activity_is_cancelled_and_original_gets_explicit_error(slash_bot, monkeypatch):
    cancelled = asyncio.Event()

    async def action(ctx, action=None, *, args=None):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    catalog, interaction = registered_interaction(slash_bot, action)
    monkeypatch.setattr(slash_support, "ACTIVITY_COMMAND_TIMEOUT", 0.02)
    try:
        await asyncio.wait_for(interaction.command.callback(interaction), 1)
        assert cancelled.is_set()
        assert "L'attente est arrêtée" in interaction.edit_original_response.await_args.kwargs["content"]
        interaction.followup.send.assert_not_awaited()
    finally:
        catalog.cog_unload()


@pytest.mark.asyncio
async def test_timeout_after_commit_does_not_tell_user_to_create_again(slash_bot, monkeypatch):
    async def action(ctx, action=None, *, args=None):
        ctx.activity_committed = True
        await ctx.send("Enregistrée")
        await asyncio.Event().wait()
    catalog, interaction = registered_interaction(slash_bot, action)
    monkeypatch.setattr(slash_support, "ACTIVITY_COMMAND_TIMEOUT", 0.02)
    try:
        await interaction.command.callback(interaction)
        interaction.edit_original_response.assert_awaited_once_with(content="Enregistrée")
        sent = interaction.followup.send.await_args
        content = sent.args[0] if sent.args else sent.kwargs["content"]
        assert "enregistrée" in content and "Ne recrée pas" in content
        assert sent.kwargs["ephemeral"]
    finally:
        catalog.cog_unload()


@pytest.mark.asyncio
async def test_unexpected_global_check_exception_finishes_deferred_response(slash_bot):
    async def action(ctx, action=None, *, args=None):
        await ctx.send("Ne doit pas arriver")

    async def broken_check(ctx):
        raise RuntimeError("Unexpected check failure")
    catalog, interaction = registered_interaction(slash_bot, action)
    slash_bot.add_check(broken_check, call_once=True)
    try:
        await interaction.command.callback(interaction)
        content = interaction.edit_original_response.await_args.kwargs["content"]
        assert "erreur" in content and "Ne doit pas arriver" not in content
    finally:
        catalog.cog_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize("ephemeral", [False, True])
async def test_error_completes_loading_original_without_public_private_leak(slash_bot, ephemeral):
    interaction = make_interaction(slash_bot, SimpleNamespace(qualified_name="activite"))
    realistic_defer(interaction)
    await interaction.response.defer(thinking=True, ephemeral=ephemeral)
    interaction.original_response.return_value = SimpleNamespace(
        flags=SimpleNamespace(loading=True, ephemeral=ephemeral)
    )
    assert await send_interaction_error(interaction, "Erreur privée")
    content = interaction.edit_original_response.await_args.kwargs["content"]
    if ephemeral:
        assert content == "Erreur privée"
        interaction.followup.send.assert_not_awaited()
    else:
        assert "Erreur privée" not in content
        assert interaction.followup.send.await_args.args[0] == "Erreur privée"
        assert interaction.followup.send.await_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_deferred_component_update_never_overwrites_public_announcement(slash_bot):
    async def action(ctx, action=None, *, args=None):
        await ctx.send("Inscription privée")
    catalog, interaction = registered_interaction(slash_bot, action)
    try:
        await interaction.response.defer()
        await slash_support.invoke_from_slash(
            slash_bot, interaction, "activite", "join", private=True, defer_response=False,
        )
        interaction.edit_original_response.assert_not_awaited()
        assert interaction.followup.send.await_args.kwargs["ephemeral"]
    finally:
        catalog.cog_unload()
