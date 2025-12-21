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
    cog.dump_data_to_console.assert_awaited_once()
    assert channel.messages == [
        "Le membre **TestRecruit** a quitté le serveur. Sa fiche a été supprimée."
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

