import asyncio
from types import SimpleNamespace
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("INSTANCE_ID", "test-instance")

import pytest

import main
from utils import discord_history


class FakeMessage:
    def __init__(self, mid, author, content):
        self.id = mid
        self.author = author
        self.content = content
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit(self, content=None):
        if content is not None:
            self.content = content
            self.edits.append(content)


class FakeChannel:
    def __init__(self, messages=None, channel_id=1, sender=None):
        self.messages = list(messages or [])
        self.id = channel_id
        self.sender = sender
        self._counter = max([m.id for m in self.messages], default=0) + 1

    async def history(self, limit=50, oldest_first=False):
        subset = self.messages[-limit:]
        iterable = subset if oldest_first else list(reversed(subset))
        for message in iterable:
            yield message

    async def send(self, content):
        message = FakeMessage(self._counter, self.sender, content)
        self._counter += 1
        self.messages.append(message)
        return message

    async def fetch_message(self, message_id):
        for message in self.messages:
            if message.id == message_id:
                return message
        raise LookupError("message not found")


class FakeRateLimit(Exception):
    def __init__(self, retry_after=0.0):
        super().__init__("rate limited")
        self.status = 429
        self.retry_after = retry_after


class RateLimitChannel(FakeChannel):
    def __init__(self, messages=None, channel_id=1, sender=None):
        super().__init__(messages=messages, channel_id=channel_id, sender=sender)
        self.calls = 0

    async def history(self, limit=50, oldest_first=False):
        self.calls += 1
        if self.calls == 1:
            raise FakeRateLimit(retry_after=0.01)
        async for message in super().history(limit=limit, oldest_first=oldest_first):
            yield message


class FakeGuild:
    def __init__(self, channel):
        self.text_channels = [channel]


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("INSTANCE_ID", "instance")
    load_calls = []

    async def fake_load(self, name):
        load_calls.append(name)

    close_calls = []

    async def fake_close(self):
        close_calls.append(True)
        self._closed_flag = True

    exit_calls = []

    def fake_exit(code):
        exit_calls.append(code)

    monkeypatch.setattr(main.EvoBot, "load_extension", fake_load, raising=False)
    monkeypatch.setattr(main.EvoBot, "close", fake_close, raising=False)
    monkeypatch.setattr(main.os, "_exit", fake_exit)
    bot = main.EvoBot()
    bot._closed_flag = False
    bot._close_calls = close_calls
    bot._exit_calls = exit_calls
    bot._load_calls = load_calls
    bot.is_closed = lambda: bot._closed_flag
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=1, name="bot"))
    return bot


@pytest.mark.asyncio
async def test_parse_latest_lock_returns_latest_valid_message(bot):
    other = SimpleNamespace(id=2)
    valid = FakeMessage(2, bot.user, f"{main.LOCK_TAG} leader 1700000000")
    channel = FakeChannel([FakeMessage(1, other, "ignored"), valid])
    message, instance, timestamp = await bot.parse_latest_lock(channel)
    assert message is valid
    assert instance == "leader"
    assert timestamp == 1700000000


@pytest.mark.asyncio
async def test_parse_latest_lock_returns_none_when_invalid(bot):
    channel = FakeChannel([FakeMessage(1, bot.user, "not a lock")])
    message, instance, timestamp = await bot.parse_latest_lock(channel)
    assert message is None
    assert instance is None
    assert timestamp is None


@pytest.mark.asyncio
async def test_acquire_leadership_acquires_and_cleans(bot, monkeypatch):
    channel = FakeChannel(channel_id=42, sender=bot.user)
    old_lock = FakeMessage(10, bot.user, f"{main.LOCK_TAG} other 10")
    channel.messages.append(old_lock)

    async def fake_wait_console_channel(timeout=30):
        return channel

    monkeypatch.setattr(bot, "wait_console_channel", fake_wait_console_channel)
    result = await bot.acquire_leadership()
    assert result is True
    assert bot._lock_channel_id == 42
    assert bot._lock_message_id == channel.messages[-1].id
    assert old_lock.deleted is True


@pytest.mark.asyncio
async def test_heartbeat_loop_updates_and_detects_competition(bot, monkeypatch):
    channel = FakeChannel(channel_id=99, sender=bot.user)
    lock_message = FakeMessage(5, bot.user, f"{main.LOCK_TAG} {bot.INSTANCE_ID} 100")
    channel.messages.append(lock_message)
    bot._lock_channel_id = channel.id
    bot._lock_message_id = lock_message.id
    bot.get_channel = lambda cid: channel if cid == channel.id else None

    async def fake_sleep(delay):
        if not hasattr(fake_sleep, "count"):
            fake_sleep.count = 0
        fake_sleep.count += 1
        if fake_sleep.count == 1:
            channel.messages.append(FakeMessage(6, bot.user, f"{main.LOCK_TAG} rival 200"))
        else:
            bot._closed_flag = True

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    await bot.heartbeat_loop()
    assert lock_message.edits
    assert lock_message.edits[-1].startswith(f"{main.LOCK_TAG} {bot.INSTANCE_ID} ")
    assert len(bot._close_calls) == 1
    assert bot._exit_calls == [0]


@pytest.mark.asyncio
async def test_process_commands_only_once_per_message(monkeypatch):
    calls = []

    async def fake_process(self, message):
        calls.append(message.id)

    monkeypatch.setattr(main.commands.Bot, "process_commands", fake_process, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "token2")
    monkeypatch.setenv("INSTANCE_ID", "instance2")
    bot = main.EvoBot()
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=3))
    message = SimpleNamespace(id=123)
    await bot.process_commands(message)
    await bot.process_commands(message)
    assert calls == [123]
    other = SimpleNamespace(id=456)
    await bot.process_commands(other)
    assert calls == [123, 456]


@pytest.mark.asyncio
async def test_fetch_history_retries_on_rate_limit(bot, monkeypatch):
    channel = RateLimitChannel([FakeMessage(1, bot.user, "ok")], channel_id=12, sender=bot.user)
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(discord_history.asyncio, "sleep", fake_sleep)
    messages = await bot._fetch_history(channel, limit=10)
    assert channel.calls == 2
    assert messages and messages[0].content == "ok"
    assert sleeps
