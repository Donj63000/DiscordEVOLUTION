from pathlib import Path
import sys
from unittest.mock import MagicMock

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


def make_cog(monkeypatch) -> annonce.AnnonceCog:
    monkeypatch.setattr(annonce, "AsyncOpenAI", None)
    cog = object.__new__(annonce.AnnonceCog)
    cog.bot = MagicMock()
    cog.model = annonce.DEFAULT_MODEL
    cog.client = None
    return cog


def test_find_channel_from_display_name(monkeypatch):
    monkeypatch.setattr(annonce, "ANNONCE_CHANNEL", "📣 annonces 📣")
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
    monkeypatch.setattr(annonce, "ANNONCE_CHANNEL", "<#42>")
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
    monkeypatch.setattr(annonce, "ANNONCE_CHANNEL", "42")
    cog = make_cog(monkeypatch)
    guild = DummyGuild(
        [
            DummyChannel(41, "general"),
            DummyChannel(42, "annonce"),
        ]
    )

    found = cog._find_announcement_channel(guild)

    assert found is guild.text_channels[1]


def test_extract_generated_text_responses_shape():
    resp = {
        "output": [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Bonjour la guilde!"}
                    ],
                },
            }
        ]
    }

    assert annonce.extract_generated_text(resp) == "Bonjour la guilde!"
