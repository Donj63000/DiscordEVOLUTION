import pytest

from utils import discord_history


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeRateLimit(Exception):
    def __init__(self, retry_after: float = 0.01) -> None:
        super().__init__("rate limited")
        self.status = 429
        self.retry_after = retry_after


class RateLimitChannel:
    def __init__(self) -> None:
        self.calls = 0

    async def history(self, limit=50, oldest_first=False, before=None, after=None):
        self.calls += 1
        if self.calls == 1:
            raise FakeRateLimit()
        yield FakeMessage("ok")


@pytest.mark.asyncio
async def test_fetch_channel_history_retries_on_rate_limit(monkeypatch):
    channel = RateLimitChannel()
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(discord_history.asyncio, "sleep", fake_sleep)

    messages = await discord_history.fetch_channel_history(channel, limit=5, reason="test")

    assert channel.calls == 2
    assert messages
    assert messages[0].content == "ok"
    assert sleeps
