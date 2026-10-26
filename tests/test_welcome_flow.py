from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import welcome
from utils.dm_sessions import get_dm_coordinator, freeze_dm_message


def test_sanitize_display_text_removes_mentions_and_limits_length():
    unsafe = "@everyone <@1234567890> `Pseudo` " + "x" * 100
    sanitized = welcome._sanitize_display_text(unsafe, max_len=32)

    assert "@everyone" not in sanitized
    assert "<@1234567890>" not in sanitized
    assert "`" not in sanitized
    assert len(sanitized) <= 33


class DummyRole:
    def __init__(self, name: str) -> None:
        self.name = name


class DummyGuild:
    def __init__(self, roles=None) -> None:
        self.id = 1
        self.roles = list(roles or [])
        self._members = {}

    def add_member(self, member) -> None:
        self._members[member.id] = member

    def get_member(self, member_id: int):
        return self._members.get(member_id)


class DummyMember:
    def __init__(self, member_id: int, guild: DummyGuild, roles=None, bot: bool = False) -> None:
        self.id = member_id
        self.guild = guild
        self.roles = list(roles or [])
        self.bot = bot
        self.display_name = f"User{member_id}"
        self.add_roles = AsyncMock()
        self.edit = AsyncMock()

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class DummyDMChannel:
    _counter = 1000

    def __init__(self) -> None:
        DummyDMChannel._counter += 1
        self.id = DummyDMChannel._counter
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content or "")


class DummyBot:
    def __init__(self, guilds) -> None:
        self.guilds = list(guilds)
        self.allowed_guild_id = 1

    def get_cog(self, name):
        return None


@pytest.mark.asyncio
async def test_explicit_welcome_oui_prompts_status(monkeypatch):
    monkeypatch.setattr(welcome.discord, "DMChannel", DummyDMChannel)
    bot = DummyBot([])
    guild = DummyGuild()
    member = DummyMember(42, guild)
    guild.add_member(member)
    bot.guilds.append(guild)
    cog = welcome.WelcomeCog(bot)
    cog.save_welcomed_data = lambda: None

    dm_channel = DummyDMChannel()
    message = SimpleNamespace(author=SimpleNamespace(id=42, bot=False), channel=dm_channel, content="oui")

    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(42, guild.id, "welcome", passive=True)
    coordinator.bind(lease, dm_channel)
    message.id = lease.since_id + 1
    freeze_dm_message(bot, message)
    await cog.on_message(message)

    assert cog.pending_welcomes[42]["stage"] == "status"
    assert any("membre" in text.lower() for text in dm_channel.sent)


@pytest.mark.asyncio
async def test_explicit_welcome_membre_prompts_pseudo(monkeypatch):
    monkeypatch.setattr(welcome.discord, "DMChannel", DummyDMChannel)
    bot = DummyBot([])
    guild = DummyGuild()
    member = DummyMember(77, guild)
    guild.add_member(member)
    bot.guilds.append(guild)
    cog = welcome.WelcomeCog(bot)
    cog.save_welcomed_data = lambda: None

    dm_channel = DummyDMChannel()
    message = SimpleNamespace(author=SimpleNamespace(id=77, bot=False), channel=dm_channel, content="membre")

    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(77, guild.id, "welcome", passive=True)
    coordinator.bind(lease, dm_channel)
    message.id = lease.since_id + 1
    freeze_dm_message(bot, message)
    await cog.on_message(message)

    assert cog.pending_welcomes[77]["stage"] == "pseudo"
    assert any("pseudo" in text.lower() for text in dm_channel.sent)


@pytest.mark.asyncio
async def test_status_invite_assigns_role(monkeypatch):
    monkeypatch.setattr(welcome.discord, "DMChannel", DummyDMChannel)
    invite_role = DummyRole(welcome.INVITES_ROLE_NAME)
    bot = DummyBot([])
    guild = DummyGuild(roles=[invite_role])
    member = DummyMember(12, guild)
    guild.add_member(member)
    bot.guilds.append(guild)
    cog = welcome.WelcomeCog(bot)
    cog.save_welcomed_data = lambda: None
    cog.pending_welcomes[12] = {"stage": "status"}

    dm_channel = DummyDMChannel()
    message = SimpleNamespace(author=SimpleNamespace(id=12, bot=False), channel=dm_channel, content="invite")

    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(12, guild.id, "welcome", passive=True)
    coordinator.bind(lease, dm_channel)
    message.id = lease.since_id + 1
    freeze_dm_message(bot, message)
    await cog.on_message(message)

    member.add_roles.assert_awaited_once_with(invite_role)
    assert 12 in cog.already_welcomed
