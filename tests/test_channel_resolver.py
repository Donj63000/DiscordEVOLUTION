from pathlib import Path
import sys
import unicodedata

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import channel_resolver


class DummyChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name


class DummyGuild:
    def __init__(self, channels) -> None:
        self.text_channels = list(channels)
        self._channels = {channel.id: channel for channel in self.text_channels}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


def _bold_general_name() -> str:
    letters = [
        unicodedata.lookup("MATHEMATICAL BOLD CAPITAL G"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL E"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL N"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL E"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL R"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL A"),
        unicodedata.lookup("MATHEMATICAL BOLD SMALL L"),
    ]
    return "".join(letters) + "-staff"


def test_resolve_text_channel_normalizes_accents_and_emoji(monkeypatch):
    monkeypatch.delenv("STAFF_CHANNEL_NAME", raising=False)
    emoji = "\U0001F4CA"
    channel_name = f"{emoji}g\u00e9n\u00e9ral-staff{emoji}"
    channel = DummyChannel(1, channel_name)
    guild = DummyGuild([channel])

    found = channel_resolver.resolve_text_channel(
        guild, name_env="STAFF_CHANNEL_NAME", default_name="general-staff"
    )

    assert found is channel


def test_resolve_text_channel_normalizes_bold_letters(monkeypatch):
    monkeypatch.delenv("STAFF_CHANNEL_NAME", raising=False)
    channel = DummyChannel(2, _bold_general_name())
    guild = DummyGuild([channel])

    found = channel_resolver.resolve_text_channel(
        guild, name_env="STAFF_CHANNEL_NAME", default_name="general-staff"
    )

    assert found is channel
