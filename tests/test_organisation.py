import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import organisation


class DummyResponses:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def create(self, **kwargs):
        if not self._payloads:
            raise AssertionError("No more payloads prepared")
        payload = self._payloads.pop(0)
        return SimpleNamespace(output_text=json.dumps(payload), output=[])


class DummyClient:
    def __init__(self, payloads):
        self.responses = DummyResponses(payloads)


class FakePermissions:
    def __init__(
        self,
        *,
        send_messages=True,
        embed_links=True,
        manage_messages=True,
        manage_roles=True,
        administrator=False,
    ):
        self.send_messages = send_messages
        self.embed_links = embed_links
        self.manage_messages = manage_messages
        self.manage_roles = manage_roles
        self.administrator = administrator


class FakeRole:
    def __init__(self, role_id, name, *, position=1):
        self.id = role_id
        self.name = name
        self.position = position
        self.mention = f"<@&{role_id}>"
        self.deleted = False

    def __lt__(self, other):
        return self.position < getattr(other, "position", 0)

    async def delete(self, *, reason=None):
        self.deleted = True
        self.delete_reason = reason


class FakeMember:
    def __init__(self, member_id, *, roles=None, permissions=None, top_role=None):
        self.id = member_id
        self.roles = list(roles or [])
        self.guild_permissions = permissions or FakePermissions()
        self.top_role = top_role or FakeRole(10000, "BotTop", position=10000)
        self.added_roles = []
        self.removed_roles = []

    async def add_roles(self, role, *, reason=None):
        self.added_roles.append((role, reason))
        if all(existing.id != role.id for existing in self.roles):
            self.roles.append(role)

    async def remove_roles(self, role, *, reason=None):
        self.removed_roles.append((role, reason))
        self.roles = [existing for existing in self.roles if existing.id != role.id]


class FakeMessage:
    def __init__(self, message_id=555):
        self.id = message_id
        self.reactions = []
        self.deleted = False
        self.edited_embed = None

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def delete(self, *, reason=None):
        self.deleted = True
        self.delete_reason = reason

    async def edit(self, *, embed=None, content=None):
        self.edited_embed = embed
        self.edited_content = content


class FakeChannel:
    def __init__(self, channel_id=222, *, name="organisation", permissions=None, fail_send=False):
        self.id = channel_id
        self.name = name
        self._permissions = permissions or FakePermissions()
        self.fail_send = fail_send
        self.sent_messages = []
        self.message = FakeMessage()

    def permissions_for(self, _member):
        return self._permissions

    async def send(self, *args, **kwargs):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent_messages.append((args, kwargs))
        return self.message

    async def fetch_message(self, message_id):
        if message_id != self.message.id:
            raise organisation.discord.NotFound(response=None, message="missing")
        return self.message


class FakeGuild:
    def __init__(self, *, guild_id=123, me=None, channel=None, create_role_position=1):
        self.id = guild_id
        self.me = me or FakeMember(999, permissions=FakePermissions(manage_roles=True))
        self.roles = []
        self.members = {self.me.id: self.me}
        self.channel = channel or FakeChannel()
        self.text_channels = [self.channel]
        self.create_role_position = create_role_position
        self.created_roles = []

    def get_member(self, member_id):
        return self.members.get(member_id)

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None

    def get_role(self, role_id):
        for role in self.roles:
            if role.id == role_id:
                return role
        return None

    async def create_role(self, *, name, mentionable=False, reason=None):
        role = FakeRole(10000 + len(self.created_roles), name, position=self.create_role_position)
        role.mentionable = mentionable
        role.create_reason = reason
        self.roles.append(role)
        self.created_roles.append(role)
        return role


class FakeBot:
    def __init__(self, guild):
        self.user = SimpleNamespace(id=999)
        self.guilds = [guild]
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


def make_draft(**overrides):
    future_ts = int(datetime.now(tz=timezone.utc).timestamp()) + 3600
    data = {
        "id": "evt-test",
        "author_id": 42,
        "guild_id": 123,
        "activity": "Donjon Blop",
        "date_time": "samedi 21h",
        "location": "Zaap",
        "seats": 8,
        "details": "Prévoir clef",
        "title": "Sortie Donjon Blop",
        "body": "On part faire le donjon.",
        "cta": "Réagis pour t'inscrire.",
        "date_ts": future_ts,
    }
    data.update(overrides)
    return organisation.OrganisationDraft(**data)


def make_event(**overrides):
    future_ts = int(datetime.now(tz=timezone.utc).timestamp()) + 3600
    data = {
        "id": "evt-test",
        "guild_id": 123,
        "channel_id": 222,
        "message_id": 555,
        "author_id": 42,
        "created_at_iso": "2026-01-01T00:00:00+00:00",
        "activity": "Donjon Blop",
        "date_time": "samedi 21h",
        "date_ts": future_ts,
        "location": "Zaap",
        "seats": 8,
        "details": "Prévoir clef",
        "title": "Sortie Donjon Blop",
        "body": "On part faire le donjon.",
        "role_id": 777,
        "role_name": "Event Donjon Blop",
        "cleanup_ts": future_ts + 7200,
    }
    data.update(overrides)
    return organisation.OrganisationEvent(**data)


@pytest.mark.asyncio
async def test_planner_step_collects_and_ready(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    cog = organisation.OrganisationCog(bot=MagicMock())
    cog._client = DummyClient([
        {
            "status": "ask",
            "next_question": "Quand souhaites-tu lancer cet evenement ?",
            "collected": {"event_type": "Donjon"},
            "summary": None,
        },
        {
            "status": "ready",
            "next_question": None,
            "collected": {"date_time": "Samedi 20h"},
            "summary": "Sortie donjon samedi 20h",
        },
    ])
    session = organisation.OrganisationSession(
        user_id=1,
        guild_id=1,
        channel_id=123,
        context={"guild": "Evolution", "organiser": "Staff"},
    )

    payload = await cog._planner_step(session, initial=True)
    assert payload["status"] == "ask"
    assert session.collected["event_type"] == "Donjon"
    assert session.last_question.startswith("Quand souhaites-tu")

    payload = await cog._planner_step(session, user_message="Samedi 20h")
    assert payload["status"] == "ready"
    assert session.collected["date_time"] == "Samedi 20h"
    assert session.summary == "Sortie donjon samedi 20h"


@pytest.mark.asyncio
async def test_generate_announcement_payload(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    cog = organisation.OrganisationCog(bot=MagicMock())
    cog._client = DummyClient([
        {
            "title": "Sortie Donjon",
            "body": "Rendez-vous samedi 20h a Astrub pour enchaine les donjons.",
            "cta": "Inscris-toi sur le canal organisation",
            "mentions": "@here",
            "summary": "Samedi 20h - donjon organise par Staff",
        }
    ])
    session = organisation.OrganisationSession(
        user_id=1,
        guild_id=1,
        channel_id=123,
        context={"guild": "Evolution", "organiser": "Staff"},
        collected={"event_type": "Donjon", "date_time": "Samedi 20h"},
        summary="Sortie donjon samedi 20h",
    )

    payload = await cog._generate_announcement(
        session,
        organiser="Staff",
        channel=SimpleNamespace(name="organisation"),
    )

    assert payload["title"] == "Sortie Donjon"
    ctx = SimpleNamespace(author=SimpleNamespace(display_name="Staff"))
    mentions, embed = cog._format_announcement(ctx, payload)
    assert mentions == "@here"
    assert embed.title == "Sortie Donjon"
    assert "Rendez-vous" in embed.description


@pytest.mark.asyncio
async def test_turn_limit_preserves_valid_response(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    monkeypatch.setattr(organisation, "ORGANISATION_MAX_TURNS", 2)
    cog = organisation.OrganisationCog(bot=MagicMock())

    async def fake_call(messages, schema, temperature):
        non_system = [m for m in messages if str(m.get("role") or "").lower() != "system"]
        assert len(non_system) <= organisation.ORGANISATION_MAX_TURNS
        assert any(
            m.get("role") == "assistant" and "Contexte precedent compresse" in str(m.get("content"))
            for m in messages
        )
        return {
            "status": "ready",
            "next_question": None,
            "collected": {"event_type": "Donjon"},
            "summary": "Sortie prevue samedi",
        }

    cog._call_openai_json = fake_call  # type: ignore[assignment]

    session = organisation.OrganisationSession(
        user_id=1,
        guild_id=1,
        channel_id=123,
        context={"guild": "Evolution", "organiser": "Staff"},
        messages=[
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
        collected={"event_type": "Donjon"},
        summary="Donjon deja prevu",
    )

    payload = await cog._planner_step(session, user_message="Dernier tour")

    assert payload["status"] == "ready"
    assert session.collected["event_type"] == "Donjon"


@pytest.mark.asyncio
async def test_load_events_from_console_tolerates_whitespace(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    bot = MagicMock()
    bot.user = object()
    cog = organisation.OrganisationCog(bot=bot)

    event = organisation.OrganisationEvent(
        id="evt-1",
        guild_id=1,
        channel_id=2,
        message_id=3,
        author_id=4,
        created_at_iso="2024-01-01T00:00:00+00:00",
        activity="Donjon",
        date_time="Samedi 20h",
        date_ts=None,
        location="Astrub",
        seats=0,
        details="Bring items",
        title="Sortie Donjon",
        body="Details",
    )
    payload = json.dumps(event.to_json(), ensure_ascii=False, indent=2)
    content = f"\n```{organisation.ORGANISATION_DB_BLOCK}\n{payload}\n```\n"

    message = MagicMock()
    message.author = bot.user
    message.content = content
    message.id = 99

    channel = MagicMock()
    channel.id = 123

    async def fake_console_channel(_guild):
        return channel

    async def fake_fetch_history(_channel, limit=300):
        return [message]

    monkeypatch.setattr(cog, "_console_channel", fake_console_channel)
    monkeypatch.setattr(cog, "_fetch_history", fake_fetch_history)

    loaded = await cog._load_events_from_console(MagicMock())

    assert loaded == 1
    assert event.message_id in cog._events


def test_organisation_event_serializes_temp_role_fields():
    event = make_event(cleanup_status="failed")

    payload = event.to_json()
    restored = organisation.OrganisationEvent.from_json(payload)

    assert payload["role_id"] == 777
    assert payload["role_name"] == "Event Donjon Blop"
    assert payload["cleanup_ts"] == event.cleanup_ts
    assert payload["cleanup_status"] == "failed"
    assert restored.role_id == 777
    assert restored.role_name == "Event Donjon Blop"
    assert restored.cleanup_ts == event.cleanup_ts
    assert restored.cleanup_status == "failed"


@pytest.mark.asyncio
async def test_publish_blocks_unparseable_date(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    monkeypatch.setattr(organisation, "parse_fr_datetime", lambda _text: None)
    guild = FakeGuild()
    cog = organisation.OrganisationCog(bot=FakeBot(guild))
    draft = make_draft(date_ts=None, date_time="pas une date")

    ok, reason = await cog._publish_draft(SimpleNamespace(guild=guild), draft)

    assert ok is False
    assert "date/heure invalide" in reason
    assert guild.created_roles == []


@pytest.mark.asyncio
async def test_publish_creates_temp_role_and_persists(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    guild = FakeGuild()
    bot = FakeBot(guild)
    cog = organisation.OrganisationCog(bot=bot)
    draft = make_draft()
    saved = []
    scheduled = []

    async def fake_save(event):
        saved.append(event)
        return True

    monkeypatch.setattr(cog, "_find_organisation_channel", lambda _guild, override="": guild.channel)
    monkeypatch.setattr(cog, "_save_event_to_console", fake_save)
    monkeypatch.setattr(cog, "_schedule_role_cleanup", lambda event: scheduled.append(event))

    ok, reason = await cog._publish_draft(SimpleNamespace(guild=guild), draft)

    assert ok is True, reason
    assert len(guild.created_roles) == 1
    role = guild.created_roles[0]
    assert role.name == "Event Donjon Blop"
    assert role.mentionable is True
    assert guild.channel.message.reactions == list(organisation.OUTING_EMOJIS)
    event = saved[0]
    assert event.role_id == role.id
    assert event.role_name == role.name
    assert event.cleanup_ts == draft.date_ts + organisation.ORGANISATION_TEMP_ROLE_GRACE_SECONDS
    assert event.message_id in cog._events
    assert scheduled == [event]
    sent_embed = guild.channel.sent_messages[0][1]["embed"]
    assert f"<@&{role.id}>" in sent_embed.description


@pytest.mark.asyncio
async def test_publish_rolls_back_role_when_announcement_send_fails(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    channel = FakeChannel(fail_send=True)
    guild = FakeGuild(channel=channel)
    cog = organisation.OrganisationCog(bot=FakeBot(guild))

    monkeypatch.setattr(cog, "_find_organisation_channel", lambda _guild, override="": channel)

    ok, reason = await cog._publish_draft(SimpleNamespace(guild=guild), make_draft())

    assert ok is False
    assert "Erreur Discord envoi message" in reason
    assert guild.created_roles[0].deleted is True
    assert cog._events == {}


@pytest.mark.asyncio
async def test_publish_rolls_back_when_console_save_fails(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    guild = FakeGuild()
    cog = organisation.OrganisationCog(bot=FakeBot(guild))

    async def fake_save(_event):
        return False

    monkeypatch.setattr(cog, "_find_organisation_channel", lambda _guild, override="": guild.channel)
    monkeypatch.setattr(cog, "_save_event_to_console", fake_save)

    ok, reason = await cog._publish_draft(SimpleNamespace(guild=guild), make_draft())

    assert ok is False
    assert "sauvegarde #console impossible" in reason
    assert guild.channel.message.deleted is True
    assert guild.created_roles[0].deleted is True
    assert cog._events == {}


@pytest.mark.asyncio
async def test_publish_requires_manage_roles(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    me = FakeMember(999, permissions=FakePermissions(manage_roles=False))
    guild = FakeGuild(me=me)
    cog = organisation.OrganisationCog(bot=FakeBot(guild))

    monkeypatch.setattr(cog, "_find_organisation_channel", lambda _guild, override="": guild.channel)

    ok, reason = await cog._publish_draft(SimpleNamespace(guild=guild), make_draft())

    assert ok is False
    assert "Gérer les rôles" in reason
    assert guild.created_roles == []


@pytest.mark.asyncio
async def test_create_temp_role_rejects_role_above_bot(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    me = FakeMember(999, permissions=FakePermissions(manage_roles=True), top_role=FakeRole(1, "BotTop", position=10))
    guild = FakeGuild(me=me, create_role_position=50)
    cog = organisation.OrganisationCog(bot=FakeBot(guild))

    role, reason = await cog._create_temp_role_for_draft(guild, make_draft())

    assert role is None
    assert "au-dessus" in reason


@pytest.mark.asyncio
async def test_reaction_handlers_sync_temp_role(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    role = FakeRole(777, "Event Donjon Blop", position=1)
    member = FakeMember(11)
    guild = FakeGuild()
    guild.roles.append(role)
    guild.members[member.id] = member
    cog = organisation.OrganisationCog(bot=FakeBot(guild))
    event = make_event(going=set(), maybe=set())
    cog._events[event.message_id] = event
    scheduled = []

    async def noop_cleanup(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cog, "_schedule_event_update", lambda updated: scheduled.append(updated))
    monkeypatch.setattr(cog, "_try_cleanup_member_reactions", noop_cleanup)

    await cog.on_raw_reaction_add(
        SimpleNamespace(
            message_id=event.message_id,
            user_id=member.id,
            emoji=organisation.EMOJI_GOING,
            member=member,
        )
    )
    await asyncio.sleep(0)

    assert member.roles == [role]
    assert event.going == {member.id}
    assert event.maybe == set()

    await cog.on_raw_reaction_add(
        SimpleNamespace(
            message_id=event.message_id,
            user_id=member.id,
            emoji=organisation.EMOJI_MAYBE,
            member=member,
        )
    )
    await asyncio.sleep(0)

    assert member.roles == []
    assert event.going == set()
    assert event.maybe == {member.id}

    event.going.add(member.id)
    event.maybe.clear()
    member.roles.append(role)

    await cog.on_raw_reaction_remove(
        SimpleNamespace(message_id=event.message_id, user_id=member.id, emoji=organisation.EMOJI_GOING)
    )

    assert member.roles == []
    assert event.going == set()
    assert scheduled


@pytest.mark.asyncio
async def test_cleanup_expired_event_role_deletes_role_and_persists(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    role = FakeRole(777, "Event Donjon Blop", position=1)
    guild = FakeGuild()
    guild.roles.append(role)
    cog = organisation.OrganisationCog(bot=FakeBot(guild))
    event = make_event()
    cog._events[event.message_id] = event
    saved = []

    async def fake_save(updated):
        saved.append(updated)
        return True

    monkeypatch.setattr(cog, "_save_event_to_console", fake_save)

    await cog._cleanup_expired_event_role(event)

    assert role.deleted is True
    assert event.status == "expired"
    assert event.cleanup_status == "expired"
    assert event.message_id not in cog._events
    assert saved == [event]


@pytest.mark.asyncio
async def test_load_events_from_console_schedules_temp_role_cleanup(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    bot_user = object()
    guild = FakeGuild()
    bot = FakeBot(guild)
    bot.user = bot_user
    cog = organisation.OrganisationCog(bot=bot)
    event = make_event()
    payload = json.dumps(event.to_json(), ensure_ascii=False, indent=2)
    message = SimpleNamespace(
        author=bot_user,
        content=f"```{organisation.ORGANISATION_DB_BLOCK}\n{payload}\n```",
        id=999,
    )
    scheduled = []

    async def fake_console_channel(_guild):
        return guild.channel

    async def fake_fetch_history(_channel, limit=300):
        return [message]

    monkeypatch.setattr(cog, "_console_channel", fake_console_channel)
    monkeypatch.setattr(cog, "_fetch_history", fake_fetch_history)
    monkeypatch.setattr(cog, "_schedule_role_cleanup", lambda loaded_event: scheduled.append(loaded_event))

    loaded = await cog._load_events_from_console(guild)

    assert loaded == 1
    assert event.message_id in cog._events
    assert scheduled[0].role_id == event.role_id
