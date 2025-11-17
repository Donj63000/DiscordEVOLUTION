from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import annonce


class DummyChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name


class DummyGuild:
    def __init__(self, channels) -> None:
        self.text_channels = list(channels)

    def get_channel(self, channel_id: int):
        for channel in self.text_channels:
            if channel.id == channel_id:
                return channel
        return None


class DummyContext:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply(self, content, *, mention_author=False):
        self.replies.append(content)


def make_cog(monkeypatch) -> annonce.AnnonceCog:
    monkeypatch.setattr(annonce, "AsyncOpenAI", None)
    cog = object.__new__(annonce.AnnonceCog)
    cog.bot = MagicMock()
    cog.model = annonce.DEFAULT_MODEL
    cog.client = None
    return cog


def test_find_channel_from_display_name(monkeypatch):
    monkeypatch.delenv("ANNONCE_CHANNEL_ID", raising=False)
    monkeypatch.delenv("ANNONCE_CHANNEL", raising=False)
    monkeypatch.setenv("ANNONCE_CHANNEL_NAME", "📣 annonces 📣")
    cog = make_cog(monkeypatch)
    guild = DummyGuild(
        [
            DummyChannel(1, "general"),
            DummyChannel(2, "📣-annonces-📣"),
        ]
    )

    found = cog._find_announcement_channel(guild)

    assert found is guild.text_channels[1]


def test_find_channel_from_id_string(monkeypatch):
    monkeypatch.delenv("ANNONCE_CHANNEL_ID", raising=False)
    monkeypatch.delenv("ANNONCE_CHANNEL", raising=False)
    monkeypatch.setenv("ANNONCE_CHANNEL_NAME", "<#42>")
    cog = make_cog(monkeypatch)
    guild = DummyGuild(
        [
            DummyChannel(41, "general"),
            DummyChannel(42, "annonce"),
        ]
    )

    found = cog._find_announcement_channel(guild)

    assert found is guild.text_channels[1]


def test_find_channel_from_plain_digits(monkeypatch):
    monkeypatch.delenv("ANNONCE_CHANNEL_ID", raising=False)
    monkeypatch.delenv("ANNONCE_CHANNEL", raising=False)
    monkeypatch.setenv("ANNONCE_CHANNEL_NAME", "42")
    cog = make_cog(monkeypatch)
    guild = DummyGuild(
        [
            DummyChannel(41, "general"),
            DummyChannel(42, "annonce"),
        ]
    )

    found = cog._find_announcement_channel(guild)

    assert found is guild.text_channels[1]


@pytest.mark.asyncio
async def test_annonce_model_switches_runtime_choice(monkeypatch):
    cog = make_cog(monkeypatch)
    ctx = DummyContext()

    await annonce.AnnonceCog.annonce_model(cog, ctx, model="GPT5 mini")

    assert cog.model == "gpt-5-mini"
    assert ctx.replies and "gpt-5-mini" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_annonce_model_requires_identifier(monkeypatch):
    cog = make_cog(monkeypatch)
    ctx = DummyContext()

    await annonce.AnnonceCog.annonce_model(cog, ctx, model=" ")

    assert ctx.replies and "Précise un identifiant" in ctx.replies[-1]
