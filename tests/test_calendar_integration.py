from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

import activite
from slash_commands import SlashCommandsCog
from test_activite_reminders import clock
from test_calendar_data import record
from test_slash_commands import AUTHOR_ID, make_interaction, slash_bot
from utils.slash_catalog import custom_routes, format_arguments
from utils.slash_support import invoke_from_component


@pytest_asyncio.fixture
async def calendar_bot(slash_bot, monkeypatch, clock):
    monkeypatch.setattr(activite.ActiviteCog, "cog_load", AsyncMock())
    cog = activite.ActiviteCog(slash_bot)
    cog.initialized = True
    cog.activities_data = {"next_id": 2, "events": {"1": record()}}
    cog.save_data_local = AsyncMock()
    cog.dump_data_to_console = AsyncMock()
    cog.afficher_calendrier._buckets._cache.clear()
    await slash_bot.add_cog(cog)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    yield SimpleNamespace(bot=slash_bot, cog=cog, catalog=catalog)
    catalog.cog_unload()


def component_interaction(env, roles=(activite.VALIDATED_ROLE_NAME,)):
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command, roles=roles)
    click.message = discord.Message(state=env.bot._connection, channel=click.channel, data={
        "id": "900", "type": 0, "content": "Calendrier", "attachments": [], "embeds": [],
        "author": {"id": "777", "username": "Evolution BOT", "discriminator": "0", "avatar": None},
    })
    return click


def test_calendar_route_preserves_missing_positional_defaults():
    route = next(route for route in custom_routes() if route.path == ("calendrier",))
    assert format_arguments(route, {"prive": True}) == '"semaine" "" "toutes" "True"'
    assert format_arguments(route, {"date": "11/09/2026"}) == '"semaine" "11/09/2026" "toutes" "False"'


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_real_calendar_slash_bridge_and_privacy(calendar_bot, private):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    schema = command.to_dict(env.bot.tree)
    assert [option["name"] for option in schema["options"]] == ["vue", "date", "filtre", "prive"]
    click = make_interaction(env.bot, command)
    await command.callback(click, prive=private)
    view = click.followup.send.await_args.kwargs["view"]
    try:
        assert view.state.mode == "semaine"
        assert view.state.anchor == date(2026, 9, 11)
        assert click.followup.send.await_args.kwargs["ephemeral"] is private
        if private:
            click.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        else:
            click.response.defer.assert_awaited_once_with(thinking=True)
        assert not view.is_finished()
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_real_slash_parameters_reach_month_filter_and_requested_date(calendar_bot):
    env = calendar_bot
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    await command.callback(click, vue="mois", date="01/10/2026", filtre="inscrit")
    view = click.followup.send.await_args.kwargs["view"]
    try:
        assert view.state.mode == "mois"
        assert view.state.filter == "inscrit"
        assert view.state.anchor == date(2026, 10, 1)
        assert click.followup.send.await_args.kwargs["files"][0].fp.closed
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_component_join_runs_existing_persistence_as_the_clicker(calendar_bot):
    env = calendar_bot
    click = component_interaction(env)
    original_author = click.message.author
    await env.cog._calendar_activity_action(click, "1", "join")
    assert env.cog.activities_data["events"]["1"]["participants"] == [7, AUTHOR_ID]
    env.cog.save_data_local.assert_awaited_once()
    env.cog.dump_data_to_console.assert_awaited_once()
    assert click.message.author is original_author
    assert click.message.content == "Calendrier"
    kwargs = click.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].roles is False
    assert kwargs["allowed_mentions"].users is False
    assert "rejoint" in kwargs["content"]


@pytest.mark.asyncio
async def test_component_leave_runs_existing_persistence(calendar_bot):
    env = calendar_bot
    env.cog.activities_data["events"]["1"]["participants"].append(AUTHOR_ID)
    click = component_interaction(env)
    await env.cog._calendar_activity_action(click, "1", "leave")
    assert env.cog.activities_data["events"]["1"]["participants"] == [7]
    env.cog.save_data_local.assert_awaited_once()
    env.cog.dump_data_to_console.assert_awaited_once()


@pytest.mark.asyncio
async def test_component_does_not_bypass_validated_role(calendar_bot):
    env = calendar_bot
    click = component_interaction(env, roles=())
    await env.cog._calendar_activity_action(click, "1", "join")
    assert env.cog.activities_data["events"]["1"]["participants"] == [7]
    env.cog.save_data_local.assert_not_awaited()
    assert "Rôle invalide" in click.followup.send.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_component_does_not_bypass_global_checks(calendar_bot, monkeypatch):
    env = calendar_bot
    seen = []

    async def deny(ctx):
        seen.append(ctx.author.id)
        return False

    env.bot.add_check(deny, call_once=True)
    command = env.bot.get_command("activite")
    monkeypatch.setattr(command, "dispatch_error", AsyncMock())
    click = component_interaction(env)
    await env.cog._calendar_activity_action(click, "1", "join")
    assert seen == [AUTHOR_ID]
    env.cog.save_data_local.assert_not_awaited()
    command.dispatch_error.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["full", "past", "cancelled", "duplicate"])
async def test_existing_join_revalidates_stale_data_without_persisting(calendar_bot, reason):
    env = calendar_bot
    event = env.cog.activities_data["events"]["1"]
    if reason == "full":
        event["participants"] = list(range(8))
    elif reason == "past":
        event["date_str"] = "2026-09-11 20:00:00"
    elif reason == "cancelled":
        event["cancelled"] = True
    else:
        event["participants"] = [AUTHOR_ID]
    click = component_interaction(env)
    await env.cog._calendar_activity_action(click, "1", "join")
    env.cog.save_data_local.assert_not_awaited()
    env.cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_clickers_cannot_overfill_the_last_place(calendar_bot):
    import asyncio

    env = calendar_bot
    env.cog.activities_data["events"]["1"]["participants"] = list(range(7))
    first, second = component_interaction(env), component_interaction(env)
    second.user = discord.Member(state=env.bot._connection, guild=second.guild, data={
        "user": {"id": str(AUTHOR_ID + 1), "username": "Autre", "discriminator": "0", "avatar": None},
        "flags": 0, "roles": ["200"], "joined_at": "2025-01-01T00:00:00+00:00",
    })

    async def save():
        await asyncio.sleep(0.01)

    env.cog.save_data_local.side_effect = save
    await asyncio.gather(
        env.cog._calendar_activity_action(first, "1", "join"),
        env.cog._calendar_activity_action(second, "1", "join"),
    )
    assert len(env.cog.activities_data["events"]["1"]["participants"]) == 8
    assert env.cog.save_data_local.await_count == 1


@pytest.mark.asyncio
async def test_event_role_is_still_assigned_and_removed(calendar_bot, monkeypatch):
    env = calendar_bot
    click = component_interaction(env)
    role = discord.Role(guild=click.guild, state=env.bot._connection, data={
        "id": "42", "name": "Sortie", "permissions": "0", "position": 1,
    })
    click.guild._roles[42] = role
    add_roles, remove_roles = AsyncMock(), AsyncMock()
    monkeypatch.setattr(discord.Member, "add_roles", add_roles)
    monkeypatch.setattr(discord.Member, "remove_roles", remove_roles)
    await env.cog._calendar_activity_action(click, "1", "join")
    await env.cog._calendar_activity_action(click, "1", "leave")
    add_roles.assert_awaited_once_with(role)
    remove_roles.assert_awaited_once_with(role)


@pytest.mark.asyncio
async def test_missing_command_is_reported_privately(calendar_bot):
    click = component_interaction(calendar_bot)
    await invoke_from_component(calendar_bot.bot, click, "missing", "join 1")
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True


def set_bot_permissions(env, click, monkeypatch, *, embeds, attachments):
    bot_member = discord.Member(state=env.bot._connection, guild=click.guild, data={
        "user": {"id": "777", "username": "Evolution BOT", "discriminator": "0", "avatar": None},
        "flags": 0, "roles": [], "joined_at": "2025-01-01T00:00:00+00:00",
    })
    click.guild._members[777] = bot_member
    permissions = discord.Permissions.none()
    permissions.embed_links = embeds
    permissions.attach_files = attachments
    monkeypatch.setattr(discord.TextChannel, "permissions_for", Mock(return_value=permissions))


@pytest.mark.asyncio
async def test_missing_embed_permission_gets_a_text_explanation(calendar_bot, monkeypatch):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    set_bot_permissions(env, click, monkeypatch, embeds=False, attachments=False)
    await command.callback(click)
    payload = click.followup.send.await_args.kwargs
    assert "Intégrer des liens" in payload["content"]
    assert not isinstance(payload["view"], discord.ui.View)


@pytest.mark.asyncio
async def test_missing_attachment_permission_does_not_attempt_monthly_render(calendar_bot, monkeypatch):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    set_bot_permissions(env, click, monkeypatch, embeds=True, attachments=False)
    env.cog.calendar_renderer.render = AsyncMock()
    await command.callback(click, vue="mois")
    payload = click.followup.send.await_args.kwargs
    try:
        env.cog.calendar_renderer.render.assert_not_awaited()
        assert payload["files"] == []
        assert "Mode texte" in payload["embed"].fields[-1].name
    finally:
        payload["view"].stop()


@pytest.mark.asyncio
async def test_first_upload_refusal_still_opens_native_agenda(calendar_bot):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    failure = discord.HTTPException(SimpleNamespace(status=413, reason="Too large"), "upload refused")
    click.followup.send.side_effect = [failure, SimpleNamespace(id=901)]
    await command.callback(click, vue="mois")
    first, second = click.followup.send.await_args_list
    view = second.kwargs["view"]
    try:
        assert first.kwargs["files"][0].fp.closed
        assert not second.kwargs["embed"].image.url
        assert view.message.id == 901
        assert not view.is_finished()
    finally:
        view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("console_found", [True, False])
async def test_other_guild_never_receives_shared_activity_storage(calendar_bot, monkeypatch, console_found):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    source = discord.Guild(state=env.bot._connection, data={
        "id": "200", "name": "Source", "owner_id": "999", "roles": [],
    })
    env.bot._connection._guilds = {200: source, 100: click.guild}
    monkeypatch.setattr(
        env.cog, "_resolve_console_channel",
        lambda guild: SimpleNamespace(id=42) if console_found and guild.id == 200 else None,
    )
    await command.callback(click)
    payload = click.followup.send.await_args.kwargs
    assert "n’est pas disponible ici" in payload["content"]
    assert not isinstance(payload["view"], discord.ui.View)
