"""Event authorization tests with the real Discord SDK and isolated I/O mocks."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse
import uuid

import discord
import pytest

from event_conversation import EventConversationCog, EventDraft, SecurityReviewView, ConfirmView
from models import EventData
from utils.console_store import ConsoleStore
from utils.event_provenance import EventSecurityError, sign_event, verify_event, resource_marker
from utils.storage import EventStore


class Obj(SimpleNamespace):
    __hash__ = object.__hash__


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.setenv("EVENT_PROVENANCE_KEY", "ab" * 32)
    everyone = Obj(id=1, name="@everyone", position=0, permissions=discord.Permissions.none(), managed=False)
    role = Obj(id=42, name="Event temporaire", position=1, permissions=discord.Permissions.none(), managed=False,
               delete=AsyncMock())
    management = discord.Permissions(manage_roles=True, manage_channels=True)
    staff_role = Obj(id=80, name="Staff", position=8, permissions=management)
    bot_role = Obj(id=90, name="Bot", position=10, permissions=management)
    staff = Obj(id=100, roles=[everyone, staff_role], top_role=staff_role, guild_permissions=management)
    bot_member = Obj(id=900, roles=[everyone, bot_role], top_role=bot_role, guild_permissions=management)
    member = Obj(id=200, roles=[everyone], top_role=everyone, add_roles=AsyncMock(), remove_roles=AsyncMock())
    category = Obj(id=5, name="Événements")
    guild = Obj(id=1, owner_id=999, categories=[category], roles=[everyone, role, staff_role, bot_role],
                default_role=everyone, me=bot_member)
    for item in (role, staff, bot_member, member):
        item.guild = guild
    message = Obj(id=33, author=Obj(id=900), webhook_id=None, embeds=[], delete=AsyncMock())
    announcement = Obj(id=11, guild=guild, fetch_message=AsyncMock(return_value=message))
    private = Obj(id=22, guild=guild, category=category, category_id=category.id, topic="",
                  delete=AsyncMock(), jump_url="https://discord.com/channels/1/22")
    private.permissions_for = lambda actor: actor.guild_permissions if actor.id in {staff.id, bot_member.id} else discord.Permissions.none()

    async def edit(**kwargs):
        for name, value in kwargs.items():
            if name != "reason":
                setattr(private, name, value)
        return private

    private.edit = AsyncMock(side_effect=edit)
    members = {member.id: member, staff.id: staff, bot_member.id: bot_member}
    guild.fetch_member = AsyncMock(side_effect=lambda uid: members[uid])
    guild.fetch_roles = AsyncMock(return_value=guild.roles)
    channels = {announcement.id: announcement, private.id: private}
    bot = Obj(user=Obj(id=900), guilds=[guild], fetch_channel=AsyncMock(side_effect=lambda cid: channels[cid]),
              add_view=MagicMock(), wait_until_ready=AsyncMock())
    guild.get_channel = lambda cid: channels.get(cid)
    record = {
        "event_id": 7, "event_uuid": str(uuid.uuid4()), "guild_id": 1,
        "channel_id": 11, "message_id": 33, "role_id": 42, "event_channel_id": 22, "category_id": 5,
        "ends_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "starts_at": datetime.now(timezone.utc).isoformat(), "cleanup_enabled": True,
        "title": "Donjon", "description": "Sortie", "going": [300], "max_slots": 8,
        "author_id": 100, "timezone": "Europe/Paris",
    }
    record = sign_event(record)
    private.topic = resource_marker(record)
    cog = EventConversationCog(bot)
    cog.store = Obj(save_event=AsyncMock(), delete_event=AsyncMock(), close=AsyncMock())
    cog._records["7"] = copy.deepcopy(record)
    interaction = Obj(user=member, guild=guild, guild_id=1, channel_id=11,
                      response=Obj(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock(), is_done=MagicMock(return_value=False)),
                      edit_original_response=AsyncMock(), followup=Obj(send=AsyncMock()))
    return Obj(cog=cog, bot=bot, guild=guild, role=role, private=private, announcement=announcement,
               message=message, staff=staff, member=member, bot_member=bot_member, record=record,
               interaction=interaction)


def legacy(record):
    result = copy.deepcopy(record)
    for name in ("provenance", "event_uuid", "category_id", "cleanup_enabled"):
        result.pop(name, None)
    return result


def test_signature_excludes_participants_and_transient_message(world):
    record = copy.deepcopy(world.record)
    record["going"] = [1, 2, 3]
    record["_msg"] = object()
    verify_event(record)


@pytest.mark.parametrize("signature", ["é" * 64, "g" * 64, "0" * 63, None, 123])
def test_malformed_signature_is_a_controlled_refusal(world, signature):
    record = copy.deepcopy(world.record)
    record["provenance"]["signature"] = signature
    with pytest.raises(EventSecurityError):
        verify_event(record)


@pytest.mark.parametrize("field,value", [
    ("role_id", 43), ("guild_id", 2), ("event_channel_id", 23), ("message_id", 34),
    ("category_id", 6), ("cleanup_enabled", False),
    ("ends_at", "2099-01-01T00:00:00+00:00"),
])
def test_signed_action_inputs_cannot_be_changed(world, field, value):
    record = copy.deepcopy(world.record)
    record[field] = value
    with pytest.raises(EventSecurityError):
        verify_event(record)


def test_model_and_record_conversions_preserve_signature_and_dates(world):
    event = world.cog._store_data_to_event(world.record)
    parsed = EventData.model_validate_json(event.model_dump_json())
    roundtrip = world.cog._event_data_to_store_data("7", parsed)
    verify_event(roundtrip)
    assert parsed.starts_at.tzinfo is not None
    assert roundtrip["going"] == [300]


def test_dataclass_fallback_preserves_json_dates_and_provenance():
    program = '''
import sys,json
sys.modules["pydantic"] = None
from models import EventData
from datetime import datetime,timezone
record=EventData(guild_id=1,channel_id=11,title="test",description="",starts_at=datetime.now(timezone.utc),event_uuid="x",event_category_id=5,provenance={"version":1,"signature":"proof"},cleanup_enabled=True)
restored=EventData.model_validate_json(record.model_dump_json())
assert restored.starts_at.tzinfo is not None
assert restored.provenance==record.provenance
assert restored.cleanup_enabled
'''
    subprocess.run([sys.executable, "-c", program], check=True, cwd=Path(__file__).resolve().parents[1])


@pytest.mark.asyncio
async def test_legacy_restore_never_arms_actions_or_deletes(world):
    record = legacy(world.record)
    world.cog._records["7"] = record
    record["ends_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert not await world.cog._restore_view(record)
    world.bot.add_view.assert_not_called()
    world.bot.fetch_channel.assert_not_awaited()
    world.role.delete.assert_not_awaited()
    world.private.delete.assert_not_awaited()
    assert not world.cog._tasks


@pytest.mark.asyncio
async def test_foreign_record_is_rejected_before_discord_io(world):
    record = {**world.record, "guild_id": 2}
    assert not await world.cog._restore_view(record)
    world.bot.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_key_leaves_legacy_and_signed_events_read_only(world, monkeypatch):
    monkeypatch.delenv("EVENT_PROVENANCE_KEY")
    assert not await world.cog._restore_view(world.record)
    world.bot.fetch_channel.assert_not_awaited()
    world.bot.add_view.assert_not_called()


@pytest.mark.asyncio
async def test_expired_legacy_revalidation_does_not_delete_or_schedule_cleanup(world):
    record = legacy(world.record)
    record["ends_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    world.cog._records["7"] = record
    world.interaction.user = world.staff
    result = await world.cog.confirm_security_review(world.interaction, "7", world.cog.record_fingerprint(record))
    assert "sans suppression" in result
    signed = world.cog._records["7"]
    verify_event(signed)
    assert signed["cleanup_enabled"] is False
    world.private.delete.assert_not_awaited()
    world.role.delete.assert_not_awaited()
    assert not world.cog._tasks
    assert not await world.cog._restore_view({**signed, "cleanup_enabled": True})


@pytest.mark.asyncio
async def test_revalidation_rechecks_current_staff_rights(world):
    record = legacy(world.record)
    world.cog._records["7"] = record
    world.interaction.user = world.staff
    fingerprint = world.cog.record_fingerprint(record)
    world.staff.roles = []
    with pytest.raises(EventSecurityError):
        await world.cog.confirm_security_review(world.interaction, "7", fingerprint)
    world.private.edit.assert_not_awaited()
    world.cog.store.save_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role_change", ["administrator", "managed", "hierarchy"])
async def test_revalidation_never_adopts_a_privileged_or_unmanageable_role(world, role_change):
    record = legacy(world.record)
    if role_change == "administrator":
        world.role.permissions = discord.Permissions(administrator=True)
    elif role_change == "managed":
        world.role.managed = True
    else:
        world.role.position = world.bot_member.top_role.position
    world.interaction.user = world.staff
    with pytest.raises(EventSecurityError):
        await world.cog._review_resources(world.interaction, record)
    world.private.edit.assert_not_awaited()
    world.cog.store.save_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_security_confirmation_is_owner_bound_and_single_use(world):
    record = legacy(world.record)
    world.cog._records["7"] = record
    world.interaction.user = world.staff
    view = SecurityReviewView(world.cog, world.interaction, record)
    other = copy.copy(world.interaction)
    other.user = world.member
    await view.confirm.callback(other)
    world.cog.store.save_event.assert_not_awaited()
    await view.confirm.callback(world.interaction)
    world.cog.store.save_event.assert_awaited_once()
    await view.confirm.callback(world.interaction)
    world.cog.store.save_event.assert_awaited_once()
    for task in list(world.cog._tasks):
        task.cancel()
    await asyncio.gather(*world.cog._tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_dm_creation_confirmation_uses_source_guild_and_current_membership(world):
    world.interaction.user = world.staff
    world.interaction.guild = None
    world.interaction.guild_id = None
    view = ConfirmView(world.staff.id, guild=world.guild, bot=world.bot)
    await view._confirm.callback(world.interaction)
    assert view.value is True
    rejected = ConfirmView(world.staff.id, guild=world.guild, bot=world.bot)
    world.guild.fetch_member.side_effect = discord.NotFound(Obj(status=404, reason="Not Found"), "Unknown Member")
    await rejected._confirm.callback(world.interaction)
    assert rejected.value is None


@pytest.mark.asyncio
async def test_creation_confirmation_rejects_foreign_guild_direct_callback(world):
    world.interaction.user = world.staff
    world.interaction.guild_id = 2
    view = ConfirmView(world.staff.id, guild=world.guild, bot=world.bot)
    await view._confirm.callback(world.interaction)
    assert view.value is None
    world.guild.fetch_member.assert_not_awaited()
    world.interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["name", "permissions"])
async def test_current_role_definitions_override_stale_member_cache(world, changed):
    record = legacy(world.record)
    world.interaction.user = world.staff
    fresh_staff = copy.copy(world.staff.roles[1])
    if changed == "name":
        fresh_staff.name = "Former Staff"
    else:
        fresh_staff.permissions = discord.Permissions.none()
    world.guild.fetch_roles.return_value = [fresh_staff if role.id == fresh_staff.id else role for role in world.guild.roles]
    # The cached member still appears to possess the old role and permissions.
    assert world.staff.roles[1].name == "Staff"
    assert world.staff.guild_permissions.manage_roles
    with pytest.raises(EventSecurityError):
        await world.cog._review_resources(world.interaction, record)
    world.private.edit.assert_not_awaited()


def test_real_sdk_channel_permissions_use_fresh_role_snapshot():
    client = discord.Client(intents=discord.Intents.none())
    state = client._connection

    def role_data(rid, name, permissions, position):
        return {"id": str(rid), "name": name, "permissions": str(permissions), "position": position,
                "color": 0, "hoist": False, "managed": False, "mentionable": False}

    management = discord.Permissions(manage_roles=True, manage_channels=True, view_channel=True).value
    default = role_data(1, "@everyone", 0, 0)
    cached_staff = role_data(80, "Staff", management, 8)
    guild = discord.Guild(data={"id": "1", "name": "test", "owner_id": "999", "roles": [default, cached_staff],
                               "features": [], "emojis": [], "stickers": []}, state=state)
    member = discord.Member(data={"user": {"id": "100", "username": "staff", "discriminator": "0", "avatar": None},
                                  "roles": ["80"], "flags": 0}, guild=guild, state=state)
    channel = discord.TextChannel(state=state, guild=guild, data={"id": "22", "name": "event", "type": 0,
                                                                 "position": 0, "permission_overwrites": []})
    assert channel.permissions_for(member).manage_channels
    fresh_roles = [discord.Role(guild=guild, state=state, data=default),
                   discord.Role(guild=guild, state=state, data=role_data(80, "Staff", 0, 8))]
    assert not EventConversationCog._channel_permissions(channel, member, fresh_roles).manage_channels
    assert channel.permissions_for(member).manage_channels, "The SDK's global cache must remain untouched"


@pytest.mark.asyncio
async def test_stale_or_expired_confirmation_has_no_effect(world):
    world.interaction.user = world.staff
    view = SecurityReviewView(world.cog, world.interaction, world.record)
    view._expires_at = 0
    await view.confirm.callback(world.interaction)
    world.private.edit.assert_not_awaited()
    world.cog.store.save_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_revalidation_save_never_activates_record(world):
    record = legacy(world.record)
    world.cog._records["7"] = record
    world.cog.store.save_event.side_effect = RuntimeError("storage unavailable")
    world.interaction.user = world.staff
    with pytest.raises(RuntimeError):
        await world.cog.confirm_security_review(world.interaction, "7", world.cog.record_fingerprint(record))
    assert "provenance" not in world.cog._records["7"]
    world.bot.add_view.assert_not_called()
    assert not world.cog._tasks


@pytest.mark.asyncio
async def test_cleanup_requires_separate_authorization_and_signature(world):
    record = copy.deepcopy(world.record)
    record["ends_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    record["cleanup_enabled"] = False
    record = sign_event(record)
    world.cog._records["7"] = record
    with pytest.raises(EventSecurityError):
        await world.cog._cleanup_event("7")
    world.private.delete.assert_not_awaited()
    world.role.delete.assert_not_awaited()
    world.interaction.user = world.staff
    await world.cog.confirm_security_review(world.interaction, "7", world.cog.record_fingerprint(record), cleanup=True)
    world.private.delete.assert_awaited_once()
    world.role.delete.assert_awaited_once()
    world.cog.store.delete_event.assert_awaited_once_with("7")


@pytest.mark.asyncio
async def test_rsvp_uses_authorized_role_and_does_not_mutate_on_bad_proof(world):
    changed = {**world.record, "role_id": 80}
    with pytest.raises(EventSecurityError):
        await world.cog.change_rsvp(world.interaction, changed, join=True)
    world.member.add_roles.assert_not_awaited()
    await world.cog.change_rsvp(world.interaction, world.record, join=True)
    world.member.add_roles.assert_awaited_once_with(world.role, reason="Inscription événement authentifié")
    assert 200 in world.cog._records["7"]["going"]


@pytest.mark.asyncio
async def test_rsvp_failed_save_compensates_and_preserves_durable_record(world):
    world.cog.store.save_event.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        await world.cog.change_rsvp(world.interaction, world.record, join=True)
    world.member.remove_roles.assert_awaited_once()
    assert world.cog._records["7"]["going"] == [300]


@pytest.mark.asyncio
async def test_new_event_resources_never_reuse_homonyms(world):
    draft = EventDraft("Same title", "", datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    world.guild.create_role = AsyncMock(side_effect=[Obj(id=51), Obj(id=52)])
    first = await world.cog._create_event_role(world.guild, draft, event_uuid=str(uuid.uuid4()))
    second = await world.cog._create_event_role(world.guild, draft, event_uuid=str(uuid.uuid4()))
    assert first.id != second.id
    assert world.guild.create_role.call_args_list[0].kwargs["name"] != world.guild.create_role.call_args_list[1].kwargs["name"]
    assert all(call.kwargs["permissions"].value == 0 for call in world.guild.create_role.call_args_list)
    world.guild.create_text_channel = AsyncMock(side_effect=[Obj(id=61), Obj(id=62)])
    await world.cog._create_event_channel(world.guild, draft, world.role, event_uuid=str(uuid.uuid4()))
    await world.cog._create_event_channel(world.guild, draft, world.role, event_uuid=str(uuid.uuid4()))
    assert world.guild.create_text_channel.call_args_list[0].args[0] != world.guild.create_text_channel.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_creation_failure_rolls_back_only_resources_created_by_attempt(world):
    created_role = Obj(id=51, delete=AsyncMock())
    created_channel = Obj(id=61, delete=AsyncMock(), jump_url="https://discord.com/channels/1/61")
    world.cog._create_event_role = AsyncMock(return_value=created_role)
    world.cog._create_event_channel = AsyncMock(return_value=created_channel)
    world.guild.create_scheduled_event = AsyncMock(side_effect=RuntimeError("API failed"))
    draft = EventDraft("Same title", "", datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(RuntimeError):
        await world.cog._publish_event(Obj(guild=world.guild, author=world.staff), draft)
    created_role.delete.assert_awaited_once()
    created_channel.delete.assert_awaited_once()
    world.role.delete.assert_not_awaited()
    world.private.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_console_admits_only_own_bot_non_webhook_snapshots(world, monkeypatch):
    record = legacy(world.record)
    text = "```event\n" + json.dumps(record) + "\n```"
    messages = [
        Obj(id=1, content=text, author=Obj(id=100), webhook_id=None),
        Obj(id=2, content=text, author=world.bot.user, webhook_id=123),
        Obj(id=3, content=text, author=world.bot.user, webhook_id=None),
    ]
    store = ConsoleStore(world.bot)
    store._channel = AsyncMock(return_value=world.announcement)
    monkeypatch.setattr("utils.console_store.fetch_channel_history", AsyncMock(return_value=messages))
    loaded = await store.load_all()
    assert list(loaded) == [7]
    assert loaded[7]["_msg"].id == 3


@pytest.mark.asyncio
async def test_console_roundtrip_preserves_json_dates_and_provenance(world, monkeypatch):
    event = world.cog._store_data_to_event(world.record)
    store = EventStore(world.bot)
    store.backend = "console"
    store.console_channel = world.announcement
    captured = []

    async def send(content, *, file=None):
        if file is not None:
            value = json.loads(file.fp.read())
            content = store.MARKER + "\n```json\n" + json.dumps(value) + "\n```"
        captured.append(Obj(id=50, content=content, author=world.bot.user, webhook_id=None, attachments=[]))

    world.announcement.send = AsyncMock(side_effect=send)
    await store.save_event("7", event)
    restored = EventStore(world.bot)
    restored.console_channel = world.announcement
    monkeypatch.setattr("utils.storage.fetch_channel_history", AsyncMock(return_value=captured))
    loaded = await restored.load()
    verify_event(world.cog._event_data_to_store_data("7", loaded["events"]["7"]))


@pytest.mark.asyncio
async def test_storage_failure_retains_last_durable_record(world):
    store = EventStore(world.bot)
    previous = world.cog._store_data_to_event(world.record)
    store.events["7"] = previous
    store._dump_console = AsyncMock(side_effect=RuntimeError("failed"))
    changed = previous.model_copy(update={"title": "Changed"})
    with pytest.raises(RuntimeError):
        await store.save_event("7", changed)
    assert store.events["7"] is previous


@pytest.mark.asyncio
async def test_real_postgres_jsonb_roundtrip(world):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL must point to a disposable local PostgreSQL database")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    assert "test" in parsed.path.lower(), "Only an explicitly named test database is allowed"
    import asyncpg
    schema = "event_security_" + uuid.uuid4().hex
    connection = await asyncpg.connect(dsn)
    pool = None
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, server_settings={"search_path": schema})
        store = EventStore(world.bot)
        store.backend, store.db = "postgres", pool
        await store._init_db()
        event = world.cog._store_data_to_event(world.record)
        await store.save_event("7", event)
        foreign = event.model_copy(update={"guild_id": 2})
        await pool.execute("INSERT INTO events(id,data) VALUES($1,$2::jsonb)", "88", foreign.model_dump_json())
        assert not await store.delete_event("88")
        with pytest.raises(RuntimeError):
            await store.save_event("88", event)
        assert json.loads(await pool.fetchval("SELECT data FROM events WHERE id='88'"))["guild_id"] == 2
        await store.save_conversation("100", ["test input"])
        restored = EventStore(world.bot)
        restored.backend, restored.db = "postgres", pool
        loaded = await restored.load()
        assert "88" not in loaded["events"]
        result = loaded["events"]["7"]
        assert result.starts_at == event.starts_at
        assert result.provenance == event.provenance
        verify_event(world.cog._event_data_to_store_data("7", result))
        assert loaded["conversations"] == {}, "Old DM collectors are never resumed on restart"
        assert await store.delete_event("7")
        assert (await restored.load())["events"] == {}
    finally:
        if pool is not None:
            await pool.close()
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()
