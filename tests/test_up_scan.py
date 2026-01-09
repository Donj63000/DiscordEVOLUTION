from datetime import datetime
from types import SimpleNamespace

import pytest

from up import UpCog


class FakeHistory:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeChannel:
    def __init__(self):
        self.history_calls = []

    def history(self, *args, **kwargs):
        self.history_calls.append((args, kwargs))
        return FakeHistory()


@pytest.mark.asyncio
async def test_scan_entire_history_uses_limits(monkeypatch):
    monkeypatch.setenv("UP_SCAN_DAYS", "30")
    monkeypatch.setenv("UP_SCAN_LIMIT_PER_CHANNEL", "123")

    channel = FakeChannel()
    guild = SimpleNamespace(text_channels=[channel])
    bot = SimpleNamespace(guilds=[guild])
    cog = UpCog(bot)

    await cog.scan_entire_history()

    assert channel.history_calls
    _, kwargs = channel.history_calls[0]
    assert kwargs["limit"] == 123
    assert isinstance(kwargs["after"], datetime)
