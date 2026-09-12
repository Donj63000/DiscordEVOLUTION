from datetime import date, datetime, timezone
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


def test_calendar_route_exposes_no_form_and_invokes_default_arguments():
    """Le schéma ne propose plus de champs, même facultatifs."""
    route = next(route for route in custom_routes() if route.path == ("calendrier",))
    assert route.options == ()
    assert route.path == ("calendrier",)
    assert "Afficher directement" in route.description
    assert format_arguments(route, {}) == ""
    assert format_arguments(route, {"prive": True, "date": "11/09/2026"}) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["slash", "prefix"])
@pytest.mark.parametrize("now_utc,today_paris", [
    (datetime(2026, 9, 11, 19, 32, tzinfo=timezone.utc), date(2026, 9, 11)),
    (datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc), date(2026, 10, 1)),
    (datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc), date(2027, 1, 1)),
])
async def test_calendar_without_arguments_opens_current_paris_month(
    calendar_bot, clock, transport, now_utc, today_paris,
):
    """Je vérifie les deux parcours complets sans date ni vue fournies."""
    env = calendar_bot
    clock.current = now_utc
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    command = env.bot.tree.get_command("calendrier")
    click = make_interaction(env.bot, command)
    click.response.send_modal = AsyncMock()
    if transport == "slash":
        namespace = discord.app_commands.Namespace(click, {}, [])
        await command._invoke_with_namespace(click, namespace)
        payload = click.followup.send.await_args.kwargs
        click.response.send_modal.assert_not_awaited()
    else:
        message = discord.Message(state=env.bot._connection, channel=click.channel, data={
            "id": "901", "type": 0, "content": "!calendrier", "attachments": [], "embeds": [],
            "author": {"id": str(AUTHOR_ID), "username": "Hero", "discriminator": "0", "avatar": None},
        })
        ctx = await env.bot.get_context(message)
        ctx.send = AsyncMock(return_value=SimpleNamespace(id=902))
        await env.bot.invoke(ctx)
        assert not ctx.command_failed
        payload = ctx.send.await_args.kwargs
    view = payload["view"]
    try:
        assert view.state.mode == "mois"
        assert view.state.anchor == today_paris
        assert (view.year, view.month) == (today_paris.year, today_paris.month)
        assert view.state.filter == "toutes"
        assert payload["embed"].image.url.startswith("attachment://calendrier-")
        assert len(payload["files"]) == 1
        assert payload["files"][0].fp.closed
        assert view.children and not view.is_finished()
        assert view.switch_mode.label == "Semaine"
        env.cog.calendar_renderer.render.assert_awaited_once()
        assert env.cog.calendar_renderer.render.await_args.args[1:] == (
            today_paris.year, today_paris.month, today_paris,
        )
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_real_calendar_slash_schema_is_direct_and_public(calendar_bot):
    env = calendar_bot
    command = env.bot.tree.get_command("calendrier")
    schema = command.to_dict(env.bot.tree)
    assert schema.get("options", []) == []
    assert "Afficher directement" in schema["description"]
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    click = make_interaction(env.bot, command)
    await command._invoke_with_namespace(click, discord.app_commands.Namespace(click, {}, []))
    view = click.followup.send.await_args.kwargs["view"]
    try:
        assert view.state.mode == "mois"
        assert view.state.anchor == date(2026, 9, 11)
        assert click.followup.send.await_args.kwargs["ephemeral"] is False
        click.response.defer.assert_awaited_once_with(thinking=True)
        assert not view.private
        assert not view.is_finished()
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_private_calendar_is_available_after_opening_without_slash_options(calendar_bot):
    env = calendar_bot
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    command = env.bot.tree.get_command("calendrier")
    initial = make_interaction(env.bot, command)
    await command.callback(initial)
    public = initial.followup.send.await_args.kwargs["view"]
    click = component_interaction(env)
    public.choose_options._values = ["private"]
    try:
        await public.choose_options.callback(click)
        child = click.followup.send.await_args.kwargs["view"]
        try:
            assert child is not public
            assert child.private
            assert child.author_id == click.user.id
            assert child.state == public.state
            assert child.renderer is public.renderer
            click.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
            assert click.followup.send.await_args.kwargs["ephemeral"] is True
            assert not public.is_finished()
        finally:
            child.stop()
    finally:
        public.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["mois", "semaine"])
async def test_legacy_prefix_keeps_advanced_parameters(calendar_bot, mode):
    """La simplification du slash ne casse pas les appels texte déjà utilisés."""
    env = calendar_bot
    env.cog.calendar_renderer.render = AsyncMock(return_value=b"png")
    click = make_interaction(env.bot, env.bot.tree.get_command("calendrier"))
    message = discord.Message(state=env.bot._connection, channel=click.channel, data={
        "id": "901", "type": 0, "content": f'!calendrier {mode} 01/10/2026 inscrit',
        "attachments": [], "embeds": [],
        "author": {"id": str(AUTHOR_ID), "username": "Hero", "discriminator": "0", "avatar": None},
    })
    ctx = await env.bot.get_context(message)
    ctx.send = AsyncMock(return_value=SimpleNamespace(id=902))
    await env.bot.invoke(ctx)
    assert not ctx.command_failed
    payload = ctx.send.await_args.kwargs
    view = payload["view"]
    try:
        assert view.state.mode == mode
        assert view.state.filter == "inscrit"
        assert view.state.anchor == date(2026, 10, 1)
        if mode == "mois":
            assert payload["files"][0].fp.closed
        else:
            assert payload["files"] == []
            env.cog.calendar_renderer.render.assert_not_awaited()
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
@pytest.mark.parametrize("reason", ["past", "cancelled", "duplicate"])
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
    assert env.cog.save_data_local.await_count == 2
    assert len(env.cog.activities_data["events"]["1"]["waitlist"]) == 1


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
    await command.callback(click)
    payload = click.followup.send.await_args.kwargs
    try:
        env.cog.calendar_renderer.render.assert_not_awaited()
        assert payload["files"] == []
        assert "Mode texte" in payload["embed"].fields[-1].name
        assert payload["view"].state.mode == "mois"
        assert not payload["view"].choose_event.disabled
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
    await command.callback(click)
    first, second = click.followup.send.await_args_list
    view = second.kwargs["view"]
    try:
        assert first.kwargs["files"][0].fp.closed
        assert not second.kwargs["embed"].image.url
        assert view.message.id == 901
        assert not view.is_finished()
        assert view.state.mode == "mois"
        assert not view.choose_event.disabled
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
