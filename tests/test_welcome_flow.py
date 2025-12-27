from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import welcome


class DummyRole:
    def __init__(self, name: str) -> None:
        self.name = name


class DummyGuild:
    def __init__(self, roles=None) -> None:
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

    def get_cog(self, name):
        return None


@pytest.mark.asyncio
async def test_dm_oui_without_state_prompts_status(monkeypatch):
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

    await cog.on_message(message)

    assert cog.pending_welcomes[42]["stage"] == "status"
    assert any("membre" in text.lower() for text in dm_channel.sent)


@pytest.mark.asyncio
async def test_dm_membre_without_state_prompts_pseudo(monkeypatch):
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

    await cog.on_message(message)

    member.add_roles.assert_awaited_once_with(invite_role)
    assert 12 in cog.already_welcomed
