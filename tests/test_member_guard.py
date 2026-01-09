from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import member_guard


class DummyRole:
    def __init__(self, role_id: int, name: str) -> None:
        self.id = role_id
        self.name = name

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"


class DummyMember:
    def __init__(self, member_id: int, display_name: str, roles, guild, bot: bool = False) -> None:
        self.id = member_id
        self.display_name = display_name
        self.roles = list(roles)
        self.guild = guild
        self.bot = bot
        self.kick = AsyncMock()

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class DummyGuild:
    def __init__(self, guild_id: int, roles) -> None:
        self.id = guild_id
        self.roles = list(roles)
        self.default_role = next((role for role in roles if role.name == "@everyone"), None)
        self._members = {}

    def add_member(self, member: DummyMember) -> None:
        self._members[member.id] = member

    def get_member(self, member_id: int):
        return self._members.get(member_id)


class DummyMessage:
    _counter = 1000

    def __init__(self) -> None:
        DummyMessage._counter += 1
        self.id = DummyMessage._counter
        self.reactions = []

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(emoji)


class DummyChannel:
    _counter = 2000

    def __init__(self) -> None:
        DummyChannel._counter += 1
        self.id = DummyChannel._counter
        self.sent = []

    async def send(self, content=None, **kwargs):
        message = DummyMessage()
        self.sent.append({"content": content, "kwargs": kwargs, "message": message})
        return message


class DummyBot:
    def __init__(self, guilds, channels=None) -> None:
        self.guilds = list(guilds)
        self.user = SimpleNamespace(id=9999)
        self._channels = channels or {}

    def get_guild(self, guild_id: int):
        for guild in self.guilds:
            if guild.id == guild_id:
                return guild
        return None

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class DummyEmoji:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class DummyPayload:
    def __init__(self, message_id: int, user_id: int, guild_id: int, channel_id: int, emoji: str) -> None:
        self.message_id = message_id
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.emoji = DummyEmoji(emoji)


class DummyRateLimitError(Exception):
    def __init__(self, status=429) -> None:
        super().__init__("rate limited")
        self.status = status


class DummyHistory:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.error:
            raise self.error
        raise StopAsyncIteration


class DummyConsoleChannel:
    def __init__(self, pins=None, history_error: Exception | None = None) -> None:
        self._pins = list(pins or [])
        self.history_error = history_error
        self.history_calls = 0

    async def pins(self):
        return list(self._pins)

    def history(self, limit=200):
        self.history_calls += 1
        return DummyHistory(self.history_error)


@pytest.mark.asyncio
async def test_on_member_remove_records_former_member():
    default_role = DummyRole(1, "@everyone")
    member_role = DummyRole(2, "Member")
    guild = DummyGuild(123, roles=[default_role, member_role])
    bot = DummyBot([guild])
    cog = member_guard.FormerMemberGuardCog(bot)
    cog.initialized = True
    cog._persist_data = AsyncMock()

    member = DummyMember(42, "Oldie", [default_role, member_role], guild)
    guild.add_member(member)

    await cog.on_member_remove(member)

    record = cog._get_member_record(guild.id, member.id)
    assert record["name"] == "Oldie"
    assert record["left_roles"] == ["Member"]
    cog._persist_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_member_join_sends_alert(monkeypatch):
    default_role = DummyRole(1, "@everyone")
    staff_role = DummyRole(3, "Staff")
    guild = DummyGuild(456, roles=[default_role, staff_role])
    channel = DummyChannel()
    bot = DummyBot([guild])
    cog = member_guard.FormerMemberGuardCog(bot)
    cog.initialized = True
    cog._persist_data = AsyncMock()
    cog._resolve_inviter = AsyncMock(return_value={"inviter_name": "Inviter"})

    monkeypatch.setattr(member_guard, "resolve_text_channel", lambda *args, **kwargs: channel)

    record = {"user_id": "77", "name": "Oldie", "left_at": "2024-01-01T00:00:00"}
    cog._set_member_record(guild.id, 77, record)

    member = DummyMember(77, "Oldie", [default_role], guild)
    guild.add_member(member)

    await cog.on_member_join(member)

    assert channel.sent
    content = channel.sent[0]["content"]
    assert "Attention le joueur" in content
    assert "Oldie" in content
    assert "Invite par" in content
    message = channel.sent[0]["message"]
    assert member_guard.CHECK_EMOJI in message.reactions
    assert member_guard.CROSS_EMOJI in message.reactions
    assert message.id in cog.pending_alerts


@pytest.mark.asyncio
async def test_reaction_cross_kicks_member():
    default_role = DummyRole(1, "@everyone")
    staff_role = DummyRole(3, "Staff")
    guild = DummyGuild(789, roles=[default_role, staff_role])
    staff_member = DummyMember(111, "Staffer", [default_role, staff_role], guild)
    target_member = DummyMember(222, "Returner", [default_role], guild)
    guild.add_member(staff_member)
    guild.add_member(target_member)
    channel = DummyChannel()
    bot = DummyBot([guild], channels={channel.id: channel})

    cog = member_guard.FormerMemberGuardCog(bot)
    cog.initialized = True
    cog._persist_data = AsyncMock()
    alert_message_id = 3333
    cog.pending_alerts[alert_message_id] = (guild.id, target_member.id)

    payload = DummyPayload(
        message_id=alert_message_id,
        user_id=staff_member.id,
        guild_id=guild.id,
        channel_id=channel.id,
        emoji=member_guard.CROSS_EMOJI,
    )

    await cog.on_raw_reaction_add(payload)

    target_member.kick.assert_awaited_once_with(reason=member_guard.KICK_REASON)
    assert alert_message_id not in cog.pending_alerts
    record = cog._get_member_record(guild.id, target_member.id)
    assert record["decision"] == "kicked"


@pytest.mark.asyncio
async def test_load_from_console_handles_rate_limit(monkeypatch):
    monkeypatch.setenv("FORMER_MEMBERS_HISTORY_LIMIT", "10")
    monkeypatch.setenv("FORMER_MEMBERS_HISTORY_RETRIES", "0")
    monkeypatch.setenv("FORMER_MEMBERS_HISTORY_BACKOFF", "0")

    bot_user = object()
    bot = SimpleNamespace(user=bot_user)
    cog = member_guard.FormerMemberGuardCog(bot)
    console = DummyConsoleChannel(history_error=DummyRateLimitError())

    result = await cog._load_from_console(console)

    assert result is None
