from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import players


class DummyChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.messages = []

    async def send(self, content: str):
        self.messages.append(content)


class DummyConsoleSnapshot:
    def __init__(self, content: str, message_id: int = 1):
        self.content = content
        self.id = message_id
        self.pinned = False

    async def pin(self):
        self.pinned = True

    async def edit(self, *, content: str):
        self.content = content


class DummyGuild:
    def __init__(self, guild_id: int, text_channels):
        self.id = guild_id
        self.name = f"Guild-{guild_id}"
        self.text_channels = text_channels

    def get_channel(self, channel_id: int):
        for channel in self.text_channels:
            if getattr(channel, "id", None) == channel_id:
                return channel
        return None


class DummyConsoleChannel(DummyChannel):
    def __init__(self, channel_id: int, name: str = "console") -> None:
        super().__init__(name)
        self.id = channel_id
        self.sent_payloads = []

    async def pins(self):
        return []

    async def send(self, content: str, file=None):
        self.sent_payloads.append({"content": content, "file": file})
        self.messages.append(content)
        return DummyConsoleSnapshot(content=content, message_id=len(self.sent_payloads))


@pytest.mark.asyncio
async def test_on_member_remove_persists_and_notifies(monkeypatch):
    bot = MagicMock()
    cog = players.PlayersCog(bot)
    cog.initialized = True
    cog.persos_data = {
        "123": {"discord_name": "TestRecruit", "main": "", "mules": []}
    }
    cog.dump_data_to_console = AsyncMock()

    channel = DummyChannel("📌 Recrutement 📌")
    guild = SimpleNamespace(text_channels=[channel])
    member = SimpleNamespace(id=123, guild=guild, display_name="TestRecruit")

    await cog.on_member_remove(member)

    assert "123" not in cog.persos_data
    cog.dump_data_to_console.assert_awaited_once_with(guild=guild)
    assert channel.messages == [
        "Le membre **TestRecruit** a quitté le serveur. Sa fiche a été supprimée."
    ]


@pytest.mark.asyncio
async def test_on_member_remove_persists_to_member_guild_console(monkeypatch):
    monkeypatch.setattr(players, "sauvegarder_donnees", lambda _data: None)

    console_a = DummyConsoleChannel(channel_id=101)
    recruitment_a = DummyChannel("📌 Recrutement 📌")
    guild_a = DummyGuild(guild_id=1, text_channels=[console_a, recruitment_a])

    console_b = DummyConsoleChannel(channel_id=202)
    recruitment_b = DummyChannel("📌 Recrutement 📌")
    guild_b = DummyGuild(guild_id=2, text_channels=[console_b, recruitment_b])

    bot = MagicMock()
    bot.guilds = [guild_a, guild_b]
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(return_value=None)

    cog = players.PlayersCog(bot)
    cog.initialized = True
    cog.persos_data = {
        "456": {"discord_name": "RecruitB", "main": "", "mules": []}
    }
    cog._get_console_snapshot = AsyncMock(return_value=None)

    member = SimpleNamespace(id=456, guild=guild_b, display_name="RecruitB")

    await cog.on_member_remove(member)

    assert "456" not in cog.persos_data
    assert len(console_b.sent_payloads) == 1
    assert console_a.sent_payloads == []
    assert recruitment_b.messages == [
        "Le membre **RecruitB** a quitté le serveur. Sa fiche a été supprimée."
    ]


@pytest.mark.asyncio
async def test_auto_register_member_merges_placeholder(monkeypatch):
    bot = MagicMock()
    cog = players.PlayersCog(bot)
    cog.initialized = True
    placeholder_id = "recrue_newbie"
    cog.persos_data = {
        placeholder_id: {
            "discord_name": "Newbie",
            "main": "",
            "mules": ["CraMule"],
        }
    }
    cog.dump_data_to_console = AsyncMock()

    await cog.auto_register_member(
        discord_id=42,
        discord_display_name="Newbie-Alpha",
        dofus_pseudo="Newbie-Alpha",
    )

    assert placeholder_id not in cog.persos_data
    assert "42" in cog.persos_data
    record = cog.persos_data["42"]
    assert record["discord_name"] == "Newbie-Alpha"
    assert record["main"] == "Newbie-Alpha"
    assert record["mules"] == ["CraMule"]
    cog.dump_data_to_console.assert_awaited_once()


def test_extract_json_from_message_handles_empty_json_block():
    cog = players.PlayersCog(MagicMock())

    result = cog._extract_json_from_message("===PLAYERSDATA===\n```json\n\n```")

    assert result is None


def test_extract_json_from_message_handles_incomplete_json_block():
    cog = players.PlayersCog(MagicMock())

    result = cog._extract_json_from_message("===PLAYERSDATA===\n```json\n{\"a\": 1")

    assert result is None


def test_extract_json_from_message_parses_valid_json_block():
    cog = players.PlayersCog(MagicMock())

    result = cog._extract_json_from_message(
        "===PLAYERSDATA===\n```json\n{\"123\": {\"discord_name\": \"Alice\", \"main\": \"Iop\", \"mules\": []}}\n```"
    )

    assert result == {"123": {"discord_name": "Alice", "main": "Iop", "mules": []}}
