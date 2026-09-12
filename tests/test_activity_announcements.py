"""Offline acceptance tests for the real activity coordinator, views and role reconciler."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

import activite
from utils.activity_data import ActivityError, activity_end, migrate_snapshot, validate_draft
from utils.activity_media import announcement_image, close_artwork
from utils.activity_roles import ActivityRoleManager, team_role_name
from utils.activity_views import ActivityCardView, ActivityListView

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Absent")


class Role:
    def __init__(self, guild, identifier, name, position=1):
        self.guild, self.id, self.name, self.position = guild, identifier, name, position
        self.permissions = discord.Permissions.none()
        self.managed = self.mentionable = self.hoist = False
        self.edit = AsyncMock(side_effect=self._edit)
        self.delete = AsyncMock(side_effect=self._delete)

    def is_default(self):
        return False

    def __lt__(self, other):
        return self.position < other.position

    @property
    def members(self):
        return [member for member in self.guild.members.values() if self in member.roles]

    async def _edit(self, **kwargs):
        for key in ("name", "mentionable", "hoist"):
            if key in kwargs:
                setattr(self, key, kwargs[key])
        return self

    async def _delete(self, **kwargs):
        self.guild.roles.pop(self.id, None)
        for member in self.guild.members.values():
            member.roles = [role for role in member.roles if role.id != self.id]


class Member:
    def __init__(self, guild, identifier):
        self.guild, self.id = guild, identifier
        self.display_name = f"Membre {identifier}"
        self.mention = f"<@{identifier}>"
        self.guild_permissions = discord.Permissions.none()
        self.roles = [SimpleNamespace(id=200, name=activite.VALIDATED_ROLE_NAME)]
        self.add_roles = AsyncMock(side_effect=self._add)
        self.remove_roles = AsyncMock(side_effect=self._remove)

    async def _add(self, role, **kwargs):
        if not any(item.id == role.id for item in self.roles):
            self.roles.append(role)

    async def _remove(self, role, **kwargs):
        self.roles = [item for item in self.roles if item.id != role.id]


class Guild:
    def __init__(self):
        self.id, self.filesize_limit = 100, 8 * 1024 * 1024
        self.roles, self.members, self.channels = {}, {}, {}
        self.me = SimpleNamespace(
            guild_permissions=discord.Permissions(manage_roles=True),
            top_role=SimpleNamespace(position=100),
        )
        self.fetch_roles = AsyncMock(side_effect=self._fetch_roles)
        self.fetch_member = AsyncMock(side_effect=self._fetch_member)
        self.create_role = AsyncMock(side_effect=self._create_role)

    def get_role(self, identifier):
        return self.roles.get(identifier)

    def get_member(self, identifier):
        return self.members.get(identifier)

    def get_channel(self, identifier):
        return self.channels.get(identifier)

    async def _fetch_roles(self):
        return list(self.roles.values())

    async def _fetch_member(self, identifier):
        member = self.members.get(identifier)
        if member is None:
            raise not_found()
        return member

    async def _create_role(self, **kwargs):
        role = Role(self, max(self.roles, default=1000) + 1, kwargs["name"])
        self.roles[role.id] = role
        return role


class Message:
    def __init__(self, channel, identifier, **kwargs):
        self.channel, self.guild = channel, channel.guild
        self.id, self.author = identifier, channel.author
        self.attachments, self.components, self.embeds = [], [], []
        self.edits = []
        self.edit = AsyncMock(side_effect=self._edit)
        self._apply(kwargs)

    def _apply(self, kwargs):
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]
        if "view" in kwargs:
            self.view = kwargs["view"]
        if "content" in kwargs:
            self.content = kwargs["content"]
        if "file" in kwargs:
            file = kwargs["file"]
            data = file.fp.read()
            self.attachments = [SimpleNamespace(filename=file.filename, data=data)]
        if "attachments" in kwargs:
            self.attachments = kwargs["attachments"]

    async def _edit(self, **kwargs):
        self.edits.append(kwargs)
        self._apply(kwargs)
        return self


class Channel:
    def __init__(self, guild, author):
        self.id, self.guild, self.author = 300, guild, author
        self.messages = []
        self.permissions = discord.Permissions.all()
        self.send = AsyncMock(side_effect=self._send)
        self.fetch_message = AsyncMock(side_effect=self._fetch_message)

    def permissions_for(self, member):
        return self.permissions

    async def _send(self, content=None, **kwargs):
        message = Message(self, 900 + len(self.messages), content=content, **kwargs)
        self.messages.append(message)
        return message

    async def _fetch_message(self, identifier):
        found = next((message for message in self.messages if message.id == identifier), None)
        if found is None:
            raise not_found()
        return found

    def history(self, **kwargs):
        async def items():
            for message in reversed(self.messages):
                yield message
        return items()


@pytest_asyncio.fixture
async def announcement_env(monkeypatch):
    guild = Guild()
    bot = SimpleNamespace(user=object(), guilds=[guild], add_view=Mock(), is_ready=lambda: True)
    channel = Channel(guild, bot.user)
    guild.channels[channel.id] = channel
    for identifier in (7, 8, 9, 10):
        guild.members[identifier] = Member(guild, identifier)
    cog = activite.ActiviteCog(bot)
    cog.initialized = True
    clock = SimpleNamespace(now=NOW)
    cog.now = lambda: clock.now
    cog._source_guild_id = guild.id
    cog._resolve_console_channel = lambda _: object()
    cog._resolve_organisation_channel = lambda _: channel
    cog.save_data_local = AsyncMock()
    commits = []

    async def persist(*args, payload=None):
        commits.append(copy.deepcopy(payload))
    cog.dump_data_to_console = AsyncMock(side_effect=persist)
    cog.dump_data_to_console_no_ctx = AsyncMock(side_effect=persist)
    monkeypatch.setenv("DISCORD_HISTORY_MIN_INTERVAL", "0")
    monkeypatch.delenv("ACTIVITE_IMAGE_PATH", raising=False)
    monkeypatch.delenv("ACTIVITE_DEFAULT_DURATION_MINUTES", raising=False)
    env = SimpleNamespace(cog=cog, guild=guild, channel=channel, bot=bot, clock=clock, commits=commits)
    yield env
    cog.cog_unload()
    for message in channel.messages:
        if getattr(message, "view", None):
            message.view.stop()


def context(env, identifier=7, **values):
    return SimpleNamespace(
        guild=env.guild, author=env.guild.members[identifier], channel=env.channel,
        message=SimpleNamespace(id=1200), slash_values=values,
        send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


async def create(env, **values):
    ctx = context(env, _draft=True, _creation_key="draft-1", titre="Donjon Blop",
                  date="12/09/2026 20:00", capacite="2", **values)
    await env.cog.command_creer(ctx)
    return ctx, env.cog.activities_data["events"]["1"]


@pytest.mark.asyncio
async def test_creation_publishes_image_buttons_roster_role_and_canonical_link(announcement_env):
    env = announcement_env
    ctx, record = await create(env)
    assert len(env.channel.messages) == 1
    message = env.channel.messages[0]
    assert record["message_id"] == message.id and record["channel_id"] == 300
    assert message.embeds[0].image.url == "attachment://activite.png"
    assert message.attachments[0].data.startswith(b"\x89PNG")
    assert message.view.is_persistent()
    assert [item.label for item in message.view.children][:2] == ["S'inscrire", "Se désinscrire"]
    assert all(item.custom_id.startswith("evo:activity:1:") for item in message.view.children)
    assert env.channel.send.await_args.kwargs["allowed_mentions"].everyone is False
    role = env.guild.roles[record["role_id"]]
    assert role.name == "equipe Donjon Blop 12/09/2026 20:00"
    assert role in env.guild.members[7].roles
    assert not role.permissions.value and not role.mentionable
    assert record["participants"] == [7]
    assert activity_end(record).isoformat() == "2026-09-12T21:00:00+00:00"
    assert "/100/300/900" in ctx.send.return_value.edit.await_args.kwargs["content"]
    assert env.commits[-1] == env.cog.activities_data


@pytest.mark.asyncio
async def test_join_leave_fifo_update_one_message_and_assign_only_confirmed(announcement_env):
    env = announcement_env
    await create(env)
    attachment = env.channel.messages[0].attachments[0]
    await env.cog.command_join(context(env, 8), "1")
    await env.cog.command_join(context(env, 9), "1")
    record = env.cog.activities_data["events"]["1"]
    role = env.guild.roles[record["role_id"]]
    assert record["participants"] == [7, 8] and record["waitlist"] == [9]
    assert role not in env.guild.members[9].roles
    assert env.channel.messages[0].view.join.label == "Liste d'attente"
    await env.cog.command_leave(context(env, 8), "1")
    record = env.cog.activities_data["events"]["1"]
    assert record["participants"] == [7, 9] and record["waitlist"] == []
    assert role not in env.guild.members[8].roles and role in env.guild.members[9].roles
    assert env.channel.messages[0].attachments[0] is attachment
    assert sum(bool(message.embeds) for message in env.channel.messages) == 1
    assert env.commits[-1] == env.cog.activities_data


@pytest.mark.asyncio
async def test_concurrent_last_place_never_overbooks(announcement_env):
    env = announcement_env
    await create(env)
    await asyncio.gather(
        env.cog.command_join(context(env, 8), "1"), env.cog.command_join(context(env, 9), "1")
    )
    record = env.cog.activities_data["events"]["1"]
    assert len(record["participants"]) == 2 and len(record["waitlist"]) == 1
    assert set(record["participants"] + record["waitlist"]) == {7, 8, 9}
    role = env.guild.roles[record["role_id"]]
    assert {member.id for member in role.members} == set(record["participants"])


@pytest.mark.asyncio
async def test_same_draft_is_idempotent_including_discord_side_effects(announcement_env):
    env = announcement_env
    await asyncio.gather(create(env), create(env))
    assert len(env.cog.activities_data["events"]) == 1
    assert env.guild.create_role.await_count == 1
    assert sum(bool(message.embeds) for message in env.channel.messages) == 1


@pytest.mark.asyncio
async def test_role_survives_start_and_is_deleted_at_planned_end(announcement_env):
    env = announcement_env
    _, record = await create(env, duree=120)
    role = env.guild.roles[record["role_id"]]
    env.clock.now = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)
    await env.cog._maintain_event(env.guild, "1")
    assert env.cog.activities_data["events"]["1"]["closed"]
    role.delete.assert_not_awaited()
    assert env.channel.messages[0].view.join.disabled
    env.clock.now += timedelta(hours=2)
    await env.cog._maintain_event(env.guild, "1")
    role.delete.assert_awaited_once()
    saved = env.cog.activities_data["events"]["1"]
    assert saved["role_id"] is None and saved["completed"] and saved["participants"] == [7]
    await env.cog._maintain_event(env.guild, "1")
    role.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_cleans_role_but_preserves_history(announcement_env):
    env = announcement_env
    _, record = await create(env)
    role = env.guild.roles[record["role_id"]]
    await env.cog.command_annuler(context(env, _confirmed=True), "1")
    saved = env.cog.activities_data["events"]["1"]
    assert saved["cancelled"] and saved["participants"] == [7]
    assert saved["role_id"] is None and env.channel.messages[0].view.join.disabled
    role.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_permissions_failure_does_not_lose_creation_and_recovers(announcement_env):
    env = announcement_env
    env.guild.me.guild_permissions.manage_roles = False
    _, record = await create(env)
    assert record["participants"] == [7] and record["message_id"]
    assert record["role_sync_pending"] and record["role_error"]
    assert record["role_id"] is None
    env.guild.me.guild_permissions.manage_roles = True
    assert await env.cog.role_manager.sync(env.guild, "1")
    saved = env.cog.activities_data["events"]["1"]
    assert saved["role_id"] and not saved["role_sync_pending"] and saved["role_error"] is None


@pytest.mark.asyncio
async def test_staging_role_is_recovered_after_restart_without_adopting_named_roles(announcement_env):
    env = announcement_env
    env.guild.me.guild_permissions.manage_roles = False
    await create(env)
    env.guild.me.guild_permissions.manage_roles = True
    record = env.cog.activities_data["events"]["1"]
    unrelated = Role(env.guild, 70, team_role_name(record))
    staging = Role(env.guild, 71, "evo-activity-100-1-unique")
    duplicate = Role(env.guild, 72, staging.name)
    env.guild.roles.update({70: unrelated, 71: staging, 72: duplicate})
    record["role_staging_name"] = staging.name
    env.cog.role_manager = ActivityRoleManager(env.cog)
    assert await env.cog.role_manager.sync(env.guild, "1")
    saved = env.cog.activities_data["events"]["1"]
    assert saved["role_id"] == 71
    env.guild.create_role.assert_not_awaited()
    unrelated.edit.assert_not_awaited()
    unrelated.delete.assert_not_awaited()
    duplicate.delete.assert_awaited_once()
    assert staging in env.guild.members[7].roles


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["permissions", "managed", "hierarchy"])
async def test_never_deletes_unsafe_role(announcement_env, unsafe):
    env = announcement_env
    _, record = await create(env)
    role = env.guild.roles[record["role_id"]]
    if unsafe == "permissions":
        role.permissions.administrator = True
    elif unsafe == "managed":
        role.managed = True
    else:
        role.position = 1000
    env.cog.activities_data["events"]["1"]["cancelled"] = True
    assert not await env.cog.role_manager.sync(env.guild, "1")
    role.delete.assert_not_awaited()
    assert env.cog.activities_data["events"]["1"]["role_sync_pending"]


@pytest.mark.asyncio
async def test_role_deletion_failure_retries(announcement_env):
    env = announcement_env
    _, record = await create(env)
    role = env.guild.roles[record["role_id"]]
    env.clock.now = activity_end(record)
    role.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Denied")
    assert not await env.cog.role_manager.sync(env.guild, "1")
    assert env.cog.activities_data["events"]["1"]["role_id"] == role.id
    role.delete.side_effect = role._delete
    assert await env.cog.role_manager.sync(env.guild, "1")
    assert env.cog.activities_data["events"]["1"]["role_id"] is None


@pytest.mark.asyncio
async def test_manual_role_deletion_is_repaired_without_losing_roster(announcement_env):
    env = announcement_env
    _, record = await create(env)
    old = env.guild.roles[record["role_id"]]
    await old.delete()
    assert await env.cog.role_manager.sync(env.guild, "1")
    current = env.cog.activities_data["events"]["1"]
    assert current["participants"] == [7] and current["role_id"] in env.guild.roles
    assert env.guild.roles[current["role_id"]] in env.guild.members[7].roles
    assert env.guild.create_role.await_count == 2


@pytest.mark.asyncio
async def test_member_not_in_cache_is_fetched_before_assigning_role(announcement_env):
    env = announcement_env
    _, record = await create(env)
    member = env.guild.members[8]
    env.cog.activities_data["events"]["1"]["participants"].append(8)
    env.guild.get_member = lambda uid: None if uid == 8 else env.guild.members.get(uid)
    assert await env.cog.role_manager.sync(env.guild, "1")
    env.guild.fetch_member.assert_awaited_once_with(8)
    assert env.guild.roles[record["role_id"]] in member.roles


@pytest.mark.asyncio
async def test_repair_missing_announcement_republishes_once(announcement_env):
    env = announcement_env
    _, record = await create(env)
    old_id = record["message_id"]
    env.channel.messages.clear()
    await env.cog.on_raw_message_delete(SimpleNamespace(guild_id=100, message_id=old_id))
    assert "1" in env.cog._dirty_cards
    await env.cog._maintain_event(env.guild, "1")
    await env.cog._maintain_event(env.guild, "1")
    assert sum(bool(message.embeds) for message in env.channel.messages) == 1


@pytest.mark.asyncio
async def test_card_commit_failure_recovers_sent_message_without_duplicate(announcement_env):
    env = announcement_env
    ctx, _ = await create(env)
    env.channel.messages.clear()
    record = env.cog.activities_data["events"]["1"]
    record.update(message_id=None, publication_pending=True)
    original_update = env.cog._update_event_fields

    async def fail_link(guild, key, **fields):
        if fields.get("message_id"):
            raise ActivityError("Console indisponible")
        return await original_update(guild, key, **fields)
    env.cog._update_event_fields = fail_link
    assert not await env.cog.sync_card("1", env.guild, publish=True)
    assert len(env.channel.messages) == 1
    env.cog._update_event_fields = original_update
    assert await env.cog.sync_card("1", env.guild, publish=True)
    assert len(env.channel.messages) == 1
    assert env.cog.activities_data["events"]["1"]["message_id"] == env.channel.messages[0].id


@pytest.mark.asyncio
async def test_slow_discord_message_does_not_hold_roster_lock(announcement_env):
    env = announcement_env
    await create(env)
    entered, release = asyncio.Event(), asyncio.Event()
    message = env.channel.messages[0]
    original_edit = message._edit

    async def slow_edit(**kwargs):
        entered.set()
        await release.wait()
        return await original_edit(**kwargs)
    message.edit.side_effect = slow_edit
    task = asyncio.create_task(env.cog.sync_card("1", env.guild))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(env.cog._mutation_lock.acquire(), 0.1)
        env.cog._mutation_lock.release()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_snapshot_timeout_stops_writes_until_remote_reload(announcement_env, monkeypatch):
    env = announcement_env
    await create(env)
    before = copy.deepcopy(env.cog.activities_data)
    monkeypatch.setattr(activite, "SNAPSHOT_TIMEOUT", 0.01)
    env.cog.dump_data_to_console = AsyncMock(side_effect=lambda *a, **k: None)

    async def hung(*args, **kwargs):
        await asyncio.Event().wait()
    env.cog.dump_data_to_console.side_effect = hung
    with pytest.raises(ActivityError, match="trop de temps"):
        await env.cog.command_join(context(env, 8), "1")
    assert not env.cog.initialized and env.cog._remote_uncertain
    assert env.cog.activities_data == before
    with pytest.raises(ActivityError):
        await env.cog.command_join(context(env, 9), "1")


@pytest.mark.asyncio
async def test_join_without_identifier_opens_explicit_picker_with_counts(announcement_env):
    env = announcement_env
    await create(env)
    ctx = context(env, 8)
    await env.cog.command_join(ctx)
    payload = ctx.send.await_args.kwargs
    view = payload["view"]
    try:
        assert isinstance(view, ActivityListView) and view.action == "join"
        option = view.choose.options[0]
        assert "Donjon Blop" in option.label
        assert "12/09/2026" in option.description
        assert "1/2" in option.description and "1" in option.description
        assert "inscrire" in view.choose.placeholder
        assert not env.cog.activities_data["events"]["1"]["waitlist"]
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_missing_or_forbidden_image_keeps_interactive_announcement(announcement_env, monkeypatch):
    env = announcement_env
    monkeypatch.setenv("ACTIVITE_IMAGE_PATH", "absent/activite.png")
    await create(env)
    message = env.channel.messages[0]
    assert not message.attachments and not message.embeds[0].image.url
    assert any(field.name == "Illustration" for field in message.embeds[0].fields)
    assert message.view.is_persistent()
    env.channel.permissions.attach_files = False
    artwork, warning = announcement_image(env.channel)
    assert artwork is None and warning


def test_image_path_is_resolved_from_project_not_process_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACTIVITE_IMAGE_PATH", raising=False)
    artwork, warning = announcement_image()
    try:
        assert isinstance(artwork, discord.File) and not warning
        assert artwork.filename == "activite.png"
    finally:
        close_artwork(artwork)
    assert artwork.fp.closed


@pytest.mark.parametrize("duration", [0, -1, 10081, "1.5", True, "abc"])
def test_invalid_durations_are_rejected(duration):
    with pytest.raises(ActivityError):
        validate_draft({"titre": "Sortie", "date": "demain 20h", "duree": duration}, now=NOW)


def test_duration_uses_elapsed_minutes_across_daylight_saving():
    record = validate_draft(
        {"titre": "Sortie", "date": "25/10/2026 01:30", "duree": 180}, now=NOW
    )
    assert activity_end(record) - datetime.fromisoformat(record["starts_at"]) == timedelta(hours=3)
    assert activity_end(record).astimezone(activite.PARIS).hour == 3


def test_migration_preserves_rosters_and_normalizes_legacy_role_id():
    draft = validate_draft({"titre": "Sortie", "date": "demain 20h"}, now=NOW)
    legacy = {**draft, "id": "1", "role_id": "42", "creator_id": 7, "participants": [7, 8],
              "waitlist": [9], "unknown": "preserved"}
    legacy.pop("ends_at")
    legacy.pop("duration_minutes")
    result = migrate_snapshot({"next_id": 2, "events": {"1": legacy}}, 100)
    saved = result["events"]["1"]
    assert result["schema_version"] == 3 and saved["role_id"] == 42
    assert saved["participants"] == [7, 8] and saved["waitlist"] == [9]
    assert saved["unknown"] == "preserved" and saved["duration_minutes"] == 180


def test_role_name_preserves_date_with_long_unicode_title():
    record = validate_draft({"titre": "⚔" * 80, "date": "demain 20h"}, now=NOW)
    name = team_role_name(record)
    assert len(name.encode("utf-16-le")) // 2 <= 100
    assert name.endswith("13/09/2026 20:00")


@pytest.mark.asyncio
async def test_open_calendar_refreshes_after_create_and_roster_changes(announcement_env):
    from utils.calendar_data import CalendarState
    from utils.calendar_view import CalendrierView

    env = announcement_env
    message = SimpleNamespace(id=999, attachments=[], edit=AsyncMock())
    view = CalendrierView(
        env.guild.members[7], {}, guild=env.guild,
        source=lambda: env.cog.events_for_guild(100),
        state=CalendarState(NOW.date(), "mois"), clock=lambda: env.clock.now,
        attach_files=False, registry=env.cog._calendar_views,
    )
    await view.send_initial(AsyncMock(return_value=message))
    assert view in env.cog._calendar_views
    assert not view.page.events
    await create(env)
    await asyncio.wait_for(env.cog._calendar_refresh_task, 2)
    assert len(view.page.events) == 1 and view.page.events[0].participants == (7,)
    message.edit.assert_awaited()
    await env.cog.command_join(context(env, 8), "1")
    await asyncio.wait_for(env.cog._calendar_refresh_task, 2)
    assert view.page.events[0].participants == (7, 8)
    assert view.page.events[0].places == 0
    view.stop()
    count = message.edit.await_count
    await env.cog._refresh_calendars()
    assert message.edit.await_count == count


@pytest.mark.asyncio
async def test_modification_renames_existing_role_and_moves_calendar_record(announcement_env):
    env = announcement_env
    _, record = await create(env)
    role_id = record["role_id"]
    ctx = context(env, _draft=True, _revision=0, titre="Autre donjon",
                  date="13/09/2026 21:00", capacite="2", duree=90)
    await env.cog.command_modifier(ctx, "1")
    saved = env.cog.activities_data["events"]["1"]
    assert saved["role_id"] == role_id
    assert env.guild.roles[role_id].name == "equipe Autre donjon 13/09/2026 21:00"
    assert saved["participants"] == [7] and saved["duration_minutes"] == 90
    assert activity_end(saved).isoformat() == "2026-09-13T20:30:00+00:00"
    assert not saved["reminder_24_sent"] and not saved["reminder_1_sent"]
    assert env.guild.create_role.await_count == 1
    assert "Autre donjon" in env.channel.messages[0].embeds[0].title


@pytest.mark.asyncio
async def test_member_departure_promotes_waiter_and_reconciles_role(announcement_env):
    env = announcement_env
    await create(env)
    await env.cog.command_join(context(env, 8), "1")
    await env.cog.command_join(context(env, 9), "1")
    departed = env.guild.members.pop(8)
    await env.cog.on_member_remove(departed)
    saved = env.cog.activities_data["events"]["1"]
    assert saved["participants"] == [7, 9] and saved["waitlist"] == []
    assert env.guild.roles[saved["role_id"]] in env.guild.members[9].roles
