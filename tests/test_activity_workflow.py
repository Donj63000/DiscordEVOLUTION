"""The activity redesign exercised without a Discord connection."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

import activite
from slash_commands import SlashCommandsCog
from test_activite_init import FakeConsoleChannel, FakeMessage
from test_activite_reminders import RecordingConsoleChannel
from test_slash_commands import AUTHOR_ID, make_interaction, slash_bot
from utils.activity_data import (
    ActivityError, ActiviteData, apply_draft, change_roster, migrate_snapshot,
    parse_activity_when, utc, validate_draft,
)
from utils.activity_store import ActivitySnapshotStore
from utils.activity_views import ActivityCardView, ActivityListView, ActivityModal, activity_embed
from utils.calendar_data import matches_filter, snapshot_events

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


def draft(**changes):
    values = {"titre": "Donjon Blop", "date": "demain 21h", "lieu": "Entrée du donjon",
              "capacite": "8", "description": "Sortie de guilde"}
    values.update(changes)
    return validate_draft(values, now=NOW)


def record(**changes):
    data = {
        **draft(), "id": "1", "guild_id": 100, "creator_id": AUTHOR_ID,
        "participants": [AUTHOR_ID], "waitlist": [], "role_id": None,
        "cancelled": False, "revision": 0,
        "reminder_24_sent": False, "reminder_1_sent": False,
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("value,expected", [
    ("demain 21h", "2026-09-13 21:00"),
    ("aujourd'hui 20:30", "2026-09-12 20:30"),
    ("après-demain 8h05", "2026-09-14 08:05"),
    ("vendredi 21h", "2026-09-18 21:00"),
    ("samedi 10h", "2026-09-19 10:00"),
    ("18/09 21h30", "2026-09-18 21:30"),
    ("18/09/2026 21:00", "2026-09-18 21:00"),
    ("  DEMAIN   21h  ", "2026-09-13 21:00"),
])
def test_quick_dates(value, expected):
    assert parse_activity_when(value, now=NOW).strftime("%Y-%m-%d %H:%M") == expected


@pytest.mark.parametrize("value", [
    "", "demain", "demain 25h", "demain 20:99", "31/02/2027 21h",
    "11/09 21h", "12/09/2026 10h", "demain 21:", "un jour 21h",
    "29/03/2026 02:30", "25/10/2026 02:30", "01/01/2101 20h",
])
def test_invalid_or_ambiguous_dates_are_not_guessed(value):
    with pytest.raises(ActivityError):
        parse_activity_when(value, now=NOW)


@pytest.mark.parametrize("capacity", ["0", 0, "-1", "101", "huit", "8.5"])
def test_capacity_limits(capacity):
    with pytest.raises(ActivityError):
        draft(capacite=capacity)


def test_defaults_and_unicode_limits():
    assert draft(capacite="", lieu="", description="")["capacity"] == 8
    with pytest.raises(ActivityError):
        draft(titre="😀" * 50)
    with pytest.raises(ActivityError):
        draft(titre="Titre\nAutre")
    with pytest.raises(ActivityError):
        draft(description="a" * 1501)


def test_migration_preserves_members_extensions_ids_and_quarantines_damage():
    legacy = ActiviteData("7", "Historique", datetime(2026, 9, 15, 21), "Texte", 1, 42).to_dict()
    legacy.pop("starts_at")
    legacy.update(participants=[1, "1", 2], waitlist=[2, 3, 3], unknown={"keep": True})
    original = {"next_id": 2, "other": "kept", "events": {"7": legacy, "99": {"broken": True}}}
    untouched = copy.deepcopy(original)
    migrated = migrate_snapshot(original, 100)
    assert original == untouched
    assert migrated["next_id"] == 100
    assert migrated["events"]["7"]["participants"] == [1, 2]
    assert migrated["events"]["7"]["waitlist"] == [3]
    assert migrated["events"]["7"]["unknown"] == {"keep": True}
    assert migrated["events"]["7"]["guild_id"] == 100
    assert migrated["quarantine"]["99"] == {"broken": True}
    assert migrated["other"] == "kept"
    assert migrate_snapshot(migrated, 100) == migrated


@pytest.mark.parametrize("bad", [[], {}, {"events": []}, {"events": {}, "schema_version": 99}])
def test_bad_or_future_snapshot_is_rejected(bad):
    with pytest.raises((ActivityError, ValueError)):
        migrate_snapshot(bad, 100)


def test_waitlist_fifo_and_duplicate_clicks():
    data = record(capacity=1)
    text, promoted = change_roster(data, 2, "join", now=NOW)
    assert "attente" in text and not promoted
    change_roster(data, 3, "join", now=NOW)
    with pytest.raises(ActivityError):
        change_roster(data, 2, "join", now=NOW)
    _, promoted = change_roster(data, AUTHOR_ID, "leave", now=NOW)
    assert data["participants"] == [2]
    assert data["waitlist"] == [3]
    assert promoted == [2]
    change_roster(data, 3, "leave", now=NOW)
    assert data["waitlist"] == []


def test_roster_refuses_closed_and_full_waitlist():
    with pytest.raises(ActivityError):
        change_roster(record(cancelled=True), 2, "join", now=NOW)
    with pytest.raises(ActivityError):
        change_roster(record(), 2, "join", now=datetime(2099, 1, 1, tzinfo=timezone.utc))
    data = record(capacity=1, waitlist=list(range(1, 101)))
    with pytest.raises(ActivityError):
        change_roster(data, 101, "join", now=NOW)


def test_edit_preserves_roster_and_rejects_conflicts_and_under_capacity():
    data = record(reminder_24_sent=True, reminder_1_sent=True, participants=[AUTHOR_ID, 2])
    with pytest.raises(ActivityError):
        apply_draft(data, draft(capacite="1"))
    with pytest.raises(ActivityError):
        apply_draft(data, draft(), revision=1)
    apply_draft(data, draft(description="Nouveau texte"), revision=0)
    assert data["reminder_24_sent"] and data["reminder_1_sent"]
    assert data["participants"] == [AUTHOR_ID, 2]
    apply_draft(data, draft(date="18/09/2026 22h"), revision=1)
    assert not data["reminder_24_sent"] and not data["reminder_1_sent"]


def test_calendar_reads_actual_capacity_waitlist_location_and_canonical_link():
    data = record(capacity=1, waitlist=[2], channel_id=300, message_id=900)
    event = snapshot_events({"1": data}).get("1")
    assert event.capacity == 1 and event.places == 0
    assert event.waitlist == (2,)
    assert event.location == "Entrée du donjon"
    assert event.message_url.endswith("/100/300/900")
    assert matches_filter(event, "inscrit", 2, NOW)
    assert "attente" in event.status(2, NOW)


@pytest_asyncio.fixture
async def workflow(slash_bot, monkeypatch):
    monkeypatch.setattr(activite.ActiviteCog, "cog_load", AsyncMock())
    cog = activite.ActiviteCog(slash_bot)
    cog.initialized = True
    cog.now = lambda: NOW
    cog.activities_data = {"next_id": 2, "events": {"1": record()}}
    cog.save_data_local = AsyncMock()
    cog.dump_data_to_console = AsyncMock()
    cog.dump_data_to_console_no_ctx = AsyncMock()
    cog.sync_card = AsyncMock(return_value=True)
    cog._sync_legacy_roles = AsyncMock(return_value=True)
    await slash_bot.add_cog(cog)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("activite").get_command("creer")
    click = make_interaction(slash_bot, command, roles=(activite.VALIDATED_ROLE_NAME,))
    cog._resolve_console_channel = lambda guild: None
    cog._resolve_organisation_channel = lambda guild: click.channel

    async def acknowledge(**kwargs):
        click.response.is_done.return_value = True

    async def send_modal(modal):
        await acknowledge()

    click.response.send_modal = AsyncMock(side_effect=send_modal)
    click.response.edit_message = AsyncMock(side_effect=acknowledge)
    env = SimpleNamespace(bot=slash_bot, cog=cog, catalog=catalog, click=click)
    yield env
    catalog.cog_unload()
    cog.cog_unload()


def context(env, *, user=None, values=None):
    return SimpleNamespace(
        guild=env.click.guild, author=user or env.click.user, send=AsyncMock(),
        slash_values=values or {}, message=SimpleNamespace(id=1200),
    )


def member(env, identifier):
    user = discord.Member(state=env.bot._connection, guild=env.click.guild, data={
        "user": {"id": str(identifier), "username": "Autre", "discriminator": "0", "avatar": None},
        "flags": 0, "roles": ["200"], "joined_at": "2025-01-01T00:00:00+00:00",
    })
    env.click.guild._members[identifier] = user
    return user


@pytest.mark.asyncio
async def test_create_command_has_no_required_fields_and_opens_modal_without_deferring(workflow):
    env = workflow
    command = env.click.command
    assert [parameter.name for parameter in command.parameters] == ["duree"]
    assert all(not parameter.required for parameter in command.parameters)
    await command.callback(env.click)
    env.click.response.defer.assert_not_awaited()
    env.click.response.send_modal.assert_awaited_once()
    env.cog.dump_data_to_console.assert_not_awaited()
    modal = env.click.response.send_modal.await_args.args[0]
    assert len(modal.children) == 5
    assert sum(field.required for field in modal.children) == 2
    modal.stop()


@pytest.mark.asyncio
async def test_modal_opening_does_not_bypass_global_checks(workflow):
    env = workflow
    env.bot.add_check(lambda ctx: False, call_once=True)
    await env.click.command.callback(env.click)
    env.click.response.send_modal.assert_not_awaited()
    env.cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_records_one_activity_per_draft_before_discord_effects(workflow):
    env = workflow
    env.cog.activities_data = {"next_id": 1, "events": {}}
    values = {"titre": "Sortie", "date": "demain 21h", "capacite": "4",
              "_draft": True, "_creation_key": "same-draft"}
    ctx = context(env, values=values)
    await asyncio.gather(env.cog.command_creer(ctx), env.cog.command_creer(ctx))
    assert len(env.cog.activities_data["events"]) == 1
    data = env.cog.activities_data["events"]["1"]
    assert data["participants"] == [AUTHOR_ID]
    assert data["capacity"] == 4 and data["role_id"] is None
    env.cog.dump_data_to_console.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_failure_exposes_neither_uncommitted_roster_nor_success(workflow):
    env = workflow
    original = copy.deepcopy(env.cog.activities_data)
    env.cog.dump_data_to_console.side_effect = ActivityError("Console indisponible")
    ctx = context(env, user=member(env, 2))
    with pytest.raises(ActivityError):
        await env.cog.command_join(ctx, "1")
    assert env.cog.activities_data == original
    env.cog.save_data_local.assert_not_awaited()
    env.cog.sync_card.assert_not_awaited()
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncommitted_change_is_not_visible_while_console_write_waits(workflow):
    env = workflow
    entered, release = asyncio.Event(), asyncio.Event()

    async def persist(*args, **kwargs):
        entered.set()
        await release.wait()

    env.cog.dump_data_to_console.side_effect = persist
    ctx = context(env, user=member(env, 2))
    task = asyncio.create_task(env.cog.command_join(ctx, "1"))
    await entered.wait()
    assert env.cog.activities_data["events"]["1"]["participants"] == [AUTHOR_ID]
    release.set()
    await task
    assert env.cog.activities_data["events"]["1"]["participants"] == [AUTHOR_ID, 2]


@pytest.mark.asyncio
async def test_simultaneous_last_place_and_fifo_promotion(workflow):
    env = workflow
    env.cog.activities_data["events"]["1"]["capacity"] = 2
    env.cog._notify_members = AsyncMock()
    first, second = member(env, 2), member(env, 3)
    await asyncio.gather(
        env.cog.command_join(context(env, user=first), "1"),
        env.cog.command_join(context(env, user=second), "1"),
    )
    data = env.cog.activities_data["events"]["1"]
    assert len(data["participants"]) == 2 and len(data["waitlist"]) == 1
    waiting = data["waitlist"][0]
    await env.cog.command_leave(context(env), "1")
    assert waiting in env.cog.activities_data["events"]["1"]["participants"]
    env.cog._notify_members.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_guild_cannot_read_or_change_rosters(workflow):
    env = workflow
    env.cog.activities_data["events"]["1"]["guild_id"] = 999
    ctx = context(env)
    with pytest.raises(ActivityError):
        await env.cog.command_info(ctx, "1")
    with pytest.raises(ActivityError):
        await env.cog.command_join(ctx, "1")
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_owner_cannot_cancel_or_edit(workflow):
    env = workflow
    ctx = context(env, user=member(env, 2), values={"date": "demain 22h"})
    with pytest.raises(ActivityError):
        await env.cog.command_annuler(ctx, "1")
    with pytest.raises(ActivityError):
        await env.cog.command_modifier(ctx, "1 demain 22h")
    env.cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistent_view_round_trip(workflow):
    env = workflow
    data = record(message_id=900, channel_id=300)
    first = ActivityCardView(env.cog, data)
    second = ActivityCardView(env.cog, data)
    assert first.is_persistent() and second.is_persistent()
    assert [item.custom_id for item in first.children] == [item.custom_id for item in second.children]
    env.cog.activities_data["events"]["1"] = data
    env.cog._register_persistent_views()
    assert env.cog._persistent_views["1"].is_persistent()
    first.stop()
    second.stop()


@pytest.mark.asyncio
async def test_modal_validation_keeps_inputs_for_correction(workflow):
    env = workflow
    modal = ActivityModal(env.cog, AUTHOR_ID, 100)
    modal.title_input._value = "Une sortie"
    modal.when_input._value = "hier"
    modal.where_input._value = "Lieu conservé"
    modal.capacity_input._value = "8"
    modal.description_input._value = "Texte conservé"
    await modal.on_submit(env.click)
    payload = env.click.response.send_message.await_args
    view = payload.kwargs["view"]
    assert view.values["description"] == "Texte conservé"
    assert view.values["lieu"] == "Lieu conservé"
    assert view.confirm.disabled
    assert payload.kwargs["ephemeral"]
    view.stop()
    modal.stop()


@pytest.mark.asyncio
async def test_newest_broken_snapshot_blocks_restore_instead_of_loading_old_data():
    bot = SimpleNamespace(user=object())
    older = FakeMessage(bot.user, '===BOTACTIVITES===\n```json\n{"events": {}}\n```')
    newer = FakeMessage(bot.user, '===BOTACTIVITES===\n```json\nbroken\n```')
    channel = FakeConsoleChannel([newer, older])
    with pytest.raises(ActivityError):
        await ActivitySnapshotStore(bot).load(channel)


@pytest.mark.asyncio
async def test_history_failure_cannot_be_mistaken_for_empty_storage():
    bot = SimpleNamespace(user=object())
    channel = FakeConsoleChannel([])

    def broken(**kwargs):
        raise RuntimeError("History refused")

    channel.history = broken
    with pytest.raises(RuntimeError):
        await ActivitySnapshotStore(bot).load(channel)


@pytest.mark.asyncio
async def test_console_edit_failure_never_deletes_the_previous_snapshot():
    bot = SimpleNamespace(user=object())
    channel = RecordingConsoleChannel(bot.user)
    store = ActivitySnapshotStore(bot)
    await store.persist(channel, {"events": {}, "next_id": 1})
    previous = channel._messages[0]
    previous.delete = AsyncMock()
    previous.edit = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "No permission",
    ))
    with pytest.raises(ActivityError):
        await store.persist(channel, {"events": {}, "next_id": 2})
    previous.delete.assert_not_awaited()
    assert len(channel._messages) == 1
    assert json.loads(previous.content.split("```json\n")[1].split("\n```")[0])["next_id"] == 1


@pytest.mark.asyncio
async def test_large_snapshot_is_restorable_and_uses_one_pinned_message():
    bot = SimpleNamespace(user=object())
    channel = RecordingConsoleChannel(bot.user)
    store = ActivitySnapshotStore(bot)
    payload = {"events": {"1": record(description="x" * 3000)}, "next_id": 2}
    await store.persist(channel, payload)
    await store.persist(channel, payload)
    assert len(channel._messages) == 1 and channel._messages[0].pinned
    assert len(channel._messages[0].attachments) == 1
    assert await ActivitySnapshotStore(bot).load(channel) == payload


@pytest.mark.asyncio
async def test_reminder_mentions_are_bounded_and_never_include_waitlist(workflow):
    env = workflow
    event = ActiviteData.from_dict(record(
        participants=list(range(100000000000000000, 100000000000000100)),
        waitlist=[999], titre="@everyone",
    ))
    channel = SimpleNamespace(send=AsyncMock())
    assert await env.cog.envoyer_rappel(channel, event, "1h")
    assert channel.send.await_count == 3
    for call in channel.send.await_args_list:
        assert len(call.args[0]) <= 2000
        allowed = call.kwargs["allowed_mentions"]
        assert allowed.everyone is False and allowed.roles is False
        assert 999 not in [user.id for user in allowed.users]
        assert len(allowed.users) <= 40


def test_embeds_respect_discord_limits_with_large_rosters():
    embed = activity_embed(record(
        titre="😀" * 40, description="😀" * 750,
        participants=list(range(100000000000000000, 100000000000000100)),
        waitlist=list(range(200000000000000000, 200000000000000100)),
    ))
    assert len(embed) < 6000
    assert all(len(field.value.encode("utf-16-le")) // 2 <= 1024 for field in embed.fields)


def component_click(env, *, user=None):
    click = env.click
    click.user = user or click.user
    click.message = discord.Message(state=env.bot._connection, channel=click.channel, data={
        "id": "900", "type": 0, "content": "Aperçu", "attachments": [], "embeds": [],
        "author": {"id": "777", "username": "Evolution BOT", "discriminator": "0", "avatar": None},
    })
    click.response.is_done.return_value = False
    return click


@pytest.mark.asyncio
async def test_valid_modal_preview_then_confirm_runs_the_real_checked_command(workflow):
    env = workflow
    env.cog.activities_data = {"next_id": 1, "events": {}}
    modal = ActivityModal(env.cog, AUTHOR_ID, 100)
    modal.title_input._value = "Donjon avec la guilde"
    modal.when_input._value = "demain 21h"
    modal.where_input._value = "Vocal Sorties"
    modal.capacity_input._value = "4"
    modal.description_input._value = "Sans ping général"
    await modal.on_submit(env.click)
    env.cog.dump_data_to_console.assert_not_awaited()
    env.click.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    preview = env.click.edit_original_response.await_args.kwargs["view"]
    await preview.confirm.callback(component_click(env))
    stored = env.cog.activities_data["events"]["1"]
    assert stored["titre"] == "Donjon avec la guilde"
    assert stored["lieu"] == "Vocal Sorties"
    assert stored["date_str"] == "2026-09-13 21:00:00"
    assert stored["capacity"] == 4 and stored["participants"] == [AUTHOR_ID]
    env.cog.dump_data_to_console.assert_awaited_once()
    assert all(call.kwargs["ephemeral"] for call in env.click.followup.send.await_args_list)
    assert preview.is_finished()
    modal.stop()


@pytest.mark.asyncio
async def test_failed_modal_commit_keeps_a_retryable_draft_without_creating_an_activity(workflow):
    env = workflow
    env.cog.activities_data = {"next_id": 1, "events": {}}
    env.cog.dump_data_to_console.side_effect = ActivityError("Console indisponible")
    modal = ActivityModal(env.cog, AUTHOR_ID, 100)
    from utils.activity_views import DraftView

    view = DraftView(modal, {"titre": "Sortie", "date": "demain 21h"}, valid=True)
    await view.confirm.callback(component_click(env))
    assert not env.cog.activities_data["events"]
    retry = env.click.followup.send.await_args.kwargs["view"]
    assert isinstance(retry, DraftView) and retry.modal.creation_key == modal.creation_key
    assert retry.values == view.values and not retry.confirm.disabled
    retry.stop()
    modal.stop()


@pytest.mark.asyncio
async def test_modify_modal_is_prefilled_and_only_requires_an_identifier(workflow):
    env = workflow
    command = env.bot.tree.get_command("activite").get_command("modifier")
    env.click.command = command
    await command.callback(env.click, identifiant="1")
    env.click.response.defer.assert_not_awaited()
    modal = env.click.response.send_modal.await_args.args[0]
    assert modal.event_id == "1"
    assert modal.title_input.default == env.cog.activities_data["events"]["1"]["titre"]
    assert modal.where_input.default == "Entrée du donjon"
    assert modal.capacity_input.default == "8"
    env.cog.dump_data_to_console.assert_not_awaited()
    modal.stop()


@pytest.mark.asyncio
async def test_two_edits_from_same_revision_cannot_overwrite_each_other(workflow):
    env = workflow
    env.cog._notify_members = AsyncMock()
    first = context(env, values={"date": "demain 22h", "_revision": 0})
    second = context(env, values={"date": "demain 23h", "_revision": 0})
    await env.cog.command_modifier(first, "1")
    with pytest.raises(ActivityError, match="entre-temps"):
        await env.cog.command_modifier(second, "1")
    assert env.cog.activities_data["events"]["1"]["date_str"] == "2026-09-13 22:00:00"
    env.cog.dump_data_to_console.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_menu_has_a_valid_message_command_schema_and_retains_source_link(workflow):
    env = workflow
    menu = env.bot.tree.get_command("Créer une activité", type=discord.AppCommandType.message)
    schema = menu.to_dict(env.bot.tree)
    assert schema["type"] == discord.AppCommandType.message.value
    assert schema["name"] == "Créer une activité"
    source = SimpleNamespace(
        id=321, channel=env.click.channel, guild=env.click.guild, author=env.click.user,
        content="Donjon de guilde\nRendez-vous au zaap", jump_url="https://discord.com/channels/100/300/321",
    )
    await menu.callback(env.click, source)
    modal = env.click.response.send_modal.await_args.args[0]
    assert modal.title_input.default == "Donjon de guilde"
    assert modal.description_input.default == source.content
    assert modal.announcement_url == source.jump_url
    env.click.response.defer.assert_not_awaited()
    env.cog.dump_data_to_console.assert_not_awaited()
    modal.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["different_channel", "different_author"])
async def test_context_menu_cannot_republish_private_channels_or_another_members_message(workflow, violation):
    env = workflow
    source = SimpleNamespace(
        id=321, channel=env.click.channel, guild=env.click.guild, author=env.click.user,
        content="Message", jump_url="https://discord.com/channels/100/300/321",
    )
    if violation == "different_channel":
        source.channel = SimpleNamespace(id=555)
    else:
        source.author = SimpleNamespace(id=2)
    ctx = context(env, values={"_source_message": source})
    with pytest.raises(ActivityError):
        await env.cog.command_depuis(ctx)
    env.click.response.send_modal.assert_not_awaited()
    env.cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_card_can_be_republished_without_losing_registration(workflow):
    env = workflow
    env.cog.activities_data["events"]["1"].update(message_id=800, channel_id=300)
    new_message = SimpleNamespace(id=900, author=env.bot.user, delete=AsyncMock())
    channel = SimpleNamespace(
        id=300, guild=env.click.guild,
        fetch_message=AsyncMock(side_effect=discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Gone",
        )),
        send=AsyncMock(return_value=new_message),
    )

    async def empty_history(**kwargs):
        if False:
            yield

    channel.history = empty_history
    env.click.guild._channels[300] = channel
    env.cog._resolve_organisation_channel = lambda guild: channel
    result = await activite.ActiviteCog.sync_card(env.cog, "1", env.click.guild, publish=True)
    assert result
    stored = env.cog.activities_data["events"]["1"]
    assert stored["message_id"] == 900 and stored["participants"] == [AUTHOR_ID]
    assert not stored["publication_pending"]
    channel.send.assert_awaited_once()
    assert not channel.send.await_args.kwargs["allowed_mentions"].everyone
    assert not channel.send.await_args.kwargs["allowed_mentions"].roles


@pytest.mark.asyncio
async def test_inaccessible_original_channel_does_not_erase_the_card_pointer(workflow):
    env = workflow
    stored = env.cog.activities_data["events"]["1"]
    stored.update(message_id=800, channel_id=999)
    assert not await activite.ActiviteCog.sync_card(env.cog, "1", env.click.guild, publish=True)
    assert stored["message_id"] == 800 and stored["channel_id"] == 999
    env.cog.dump_data_to_console_no_ctx.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_snapshot_is_removed_when_deleted_snapshot_replacement_cannot_be_pinned():
    bot = SimpleNamespace(user=object())
    channel = RecordingConsoleChannel(bot.user)
    store = ActivitySnapshotStore(bot)
    await store.persist(channel, {"events": {}, "next_id": 1})
    original = store.message
    original.edit = AsyncMock(side_effect=discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Gone",
    ))
    replacement = SimpleNamespace(id=999, pinned=False, delete=AsyncMock())
    replacement.pin = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Pin refused",
    ))
    channel.send = AsyncMock(return_value=replacement)
    with pytest.raises(ActivityError):
        await store.persist(channel, {"events": {}, "next_id": 2})
    replacement.delete.assert_awaited_once()
    assert store.message is None


@pytest.mark.asyncio
async def test_member_departure_promotes_the_first_waiter_and_preserves_past_rosters(workflow):
    env = workflow
    env.cog._source_guild = lambda: env.click.guild
    env.cog._notify_members = AsyncMock()
    env.cog.activities_data["events"]["1"].update(capacity=1, waitlist=[2])
    member(env, 2)
    old = record(id="2", starts_at="2026-09-11T10:00:00+00:00", date_str="2026-09-11 12:00:00")
    env.cog.activities_data["events"]["2"] = old
    await env.cog.on_member_remove(env.click.user)
    assert env.cog.activities_data["events"]["1"]["participants"] == [2]
    assert env.cog.activities_data["events"]["2"]["participants"] == [AUTHOR_ID]
    env.cog.dump_data_to_console_no_ctx.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_retains_roster_and_notifies_only_interested_members(workflow):
    env = workflow
    env.cog._notify_members = AsyncMock()
    env.cog.activities_data["events"]["1"]["waitlist"] = [2]
    await env.cog.command_annuler(context(env), "1")
    stored = env.cog.activities_data["events"]["1"]
    assert stored["cancelled"] and stored["participants"] == [AUTHOR_ID]
    assert stored["waitlist"] == [2]
    env.cog.dump_data_to_console.assert_awaited_once()
    assert set(env.cog._notify_members.await_args.args[-1]) == {AUTHOR_ID, 2}


@pytest.mark.asyncio
async def test_empty_but_present_snapshot_is_not_silently_replaced_with_a_new_database(workflow):
    env = workflow
    env.cog.initialized = False
    original = copy.deepcopy(env.cog.activities_data)
    env.cog._source_guild = lambda: env.click.guild
    env.cog.snapshot_store.load = AsyncMock(return_value={})
    env.cog.snapshot_store.persist = AsyncMock()
    await env.cog.initialize_data()
    assert not env.cog.initialized
    assert env.cog.activities_data == original
    env.cog.snapshot_store.persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_shortcut_is_available_in_help_with_a_message_context_menu(workflow):
    from utils.slash_help import HelpView

    view = HelpView(workflow.bot, AUTHOR_ID)
    assert "/activite creer" in view.embed().description
    assert all(hasattr(command, "description") for group in view.catalog.values() for command in group)
    view.stop()


@pytest.mark.asyncio
async def test_code_fences_in_member_text_do_not_poison_inline_console_snapshots():
    bot = SimpleNamespace(user=object())
    channel = RecordingConsoleChannel(bot.user)
    store = ActivitySnapshotStore(bot)
    payload = {"events": {}, "note": "```json\nExemple\n```", "next_id": 1}
    await store.persist(channel, payload)
    assert not store.message.attachments
    assert await ActivitySnapshotStore(bot).load(channel) == payload
    assert store.message.content.count("```") == 2
    legacy = FakeMessage(bot.user, "===BOTACTIVITES===\n```json\n" + json.dumps(payload) + "\n```")
    assert await store.extract_payload(legacy) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["join", "leave"])
async def test_quick_picker_selection_runs_checked_membership_command(workflow, monkeypatch, action):
    env = workflow
    user = member(env, 2)
    if action == "leave":
        env.cog.activities_data["events"]["1"]["participants"].append(user.id)
    picker = ActivityListView(env.cog, user.id, 100, action=action)
    picker.build_embed()
    picker.choose._values = ["1"]
    click = component_click(env, user=user)
    edit_message = AsyncMock()
    monkeypatch.setattr(discord.Message, "edit", edit_message)
    try:
        await picker.choose.callback(click)
        participants = env.cog.activities_data["events"]["1"]["participants"]
        assert (user.id in participants) is (action == "join")
        edit_message.assert_awaited_once()
        assert picker.choose.disabled
        assert click.followup.send.await_args.kwargs["ephemeral"]
    finally:
        picker.stop()
