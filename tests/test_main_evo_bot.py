import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
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
        self.name = "console"
        self._counter = max([m.id for m in self.messages], default=0) + 1

    async def history(self, limit=50, oldest_first=False, after=None):
        subset = [message for message in self.messages if after is None or message.id > after.id]
        if limit is not None:
            subset = subset[-limit:]
        iterable = subset if oldest_first else list(reversed(subset))
        for message in iterable:
            yield message

    async def send(self, content):
        self._counter = max(self._counter, max([message.id for message in self.messages], default=0) + 1)
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
    def __init__(self, channel, guild_id=1):
        self.id = guild_id
        self.text_channels = [channel]
        channel.guild = self

    def get_channel(self, channel_id):
        return next((channel for channel in self.text_channels if channel.id == channel_id), None)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("evo_enabled", [False, True])
@pytest.mark.parametrize("build_enabled", [False, True])
async def test_setup_hook_loads_native_modules_before_slash_adapter(
    bot, monkeypatch, evo_enabled, build_enabled,
):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "0")
    monkeypatch.setenv("EVO_ENABLED", "1" if evo_enabled else "0")
    monkeypatch.setenv("BUILD_ENABLED", "1" if build_enabled else "0")
    synced = []

    async def fake_sync():
        synced.append(True)

    monkeypatch.setattr(bot, "_sync_app_commands", fake_sync)

    await bot.setup_hook()

    assert ("evo" in bot._load_calls) is evo_enabled
    assert ("build" in bot._load_calls) is build_enabled
    assert "job" in bot._load_calls
    assert "activite" in bot._load_calls
    assert "ia" not in bot._load_calls
    assert "iastaff" not in bot._load_calls
    if evo_enabled:
        assert bot._load_calls.index("evo") < bot._load_calls.index("slash_commands")
    if build_enabled:
        assert bot._load_calls.index("build") < bot._load_calls.index("slash_commands")
        if evo_enabled:
            assert bot._load_calls.index("build") < bot._load_calls.index("evo")
    assert synced == [True]


def prepare_evo_leadership(bot, monkeypatch):
    channel = FakeChannel(channel_id=42, sender=bot.user)
    own = FakeMessage(10, bot.user, f"{main.LOCK_TAG} {bot.INSTANCE_ID} 1700000000")
    channel.messages.append(own)
    guild = FakeGuild(channel)
    cog = SimpleNamespace(config=SimpleNamespace(guild_id=guild.id), suspend=AsyncMock())
    monkeypatch.setattr(bot, "get_cog", lambda name: cog if name == "EvoCog" else None)
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: guild if guild_id == guild.id else None)
    monkeypatch.setattr(bot, "get_channel", lambda channel_id: guild.get_channel(channel_id))
    monkeypatch.delenv("CHANNEL_CONSOLE_ID", raising=False)
    monkeypatch.delenv("CHANNEL_CONSOLE", raising=False)
    monkeypatch.delenv("CONSOLE_CHANNEL_NAME", raising=False)
    bot._singleton_ready = True
    bot._evo_connected = True
    bot._lock_channel_id = channel.id
    bot._lock_message_id = own.id
    return channel, own, cog


@pytest.mark.asyncio
async def test_evo_cannot_start_before_singleton_is_ready(bot, monkeypatch):
    channel, _, cog = prepare_evo_leadership(bot, monkeypatch)
    channel.fetch_message = AsyncMock()
    bot._singleton_ready = False

    with pytest.raises(main.EvoError, match="pas encore prête"):
        await bot.ensure_evo_leadership()

    channel.fetch_message.assert_not_awaited()
    cog.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_evo_verifies_latest_lock_again_before_each_operation(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    await bot.ensure_evo_leadership()
    assert bot._evo_leadership_known
    channel.messages.append(FakeMessage(own.id + 1, bot.user, f"{main.LOCK_TAG} rival 1700000001"))

    with pytest.raises(main.EvoError, match="pas vérifiable"):
        await bot.ensure_evo_leadership()

    assert not bot._evo_leadership_known
    cog.suspend.assert_awaited_once()
    assert not asyncio.current_task().cancelling()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["history", "message", "deleted", "malformed"])
async def test_evo_suspends_on_uncertain_lock_reads(bot, monkeypatch, failure):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    await bot.ensure_evo_leadership()
    if failure == "history":
        async def unavailable_history(**kwargs):
            raise PermissionError("history unavailable")
            yield
        monkeypatch.setattr(channel, "history", unavailable_history)
    elif failure == "message":
        monkeypatch.setattr(channel, "fetch_message", AsyncMock(side_effect=PermissionError()))
    elif failure == "deleted":
        channel.messages.clear()
    elif failure == "malformed":
        own.content = f"{main.LOCK_TAG} {bot.INSTANCE_ID} invalid"

    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()

    assert not bot._evo_leadership_known
    cog.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_evo_handover_waits_without_sleep_then_rechecks_lock(bot, monkeypatch):
    channel, old, _ = prepare_evo_leadership(bot, monkeypatch)
    old.content = f"{main.LOCK_TAG} previous 1700000000"
    clock = [100.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: 1700000001, monotonic=lambda: clock[0]))
    monkeypatch.setattr(bot, "wait_console_channel", AsyncMock(return_value=channel))

    assert await bot.acquire_leadership()
    assert bot._evo_resume_at == 225.0
    with pytest.raises(main.EvoError, match="instance précédente"):
        await bot.ensure_evo_leadership()
    clock[0] = 225.0
    await bot.ensure_evo_leadership()
    assert bot._evo_leadership_known

    channel.messages.append(FakeMessage(99, bot.user, f"{main.LOCK_TAG} rival 1700000002"))
    with pytest.raises(main.EvoError, match="pas vérifiable"):
        await bot.ensure_evo_leadership()


@pytest.mark.asyncio
async def test_evo_disconnect_suspends_and_resume_requires_recovery_delay(bot, monkeypatch):
    _, _, cog = prepare_evo_leadership(bot, monkeypatch)
    await bot.ensure_evo_leadership()
    await bot.on_disconnect()
    assert not bot._evo_connected
    assert not bot._evo_leadership_known
    cog.suspend.assert_awaited_once()
    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()
    await bot.on_resumed()
    with pytest.raises(main.EvoError, match="instance précédente"):
        await bot.ensure_evo_leadership()


@pytest.mark.asyncio
async def test_missing_console_allows_classic_bot_but_keeps_evo_suspended(bot, monkeypatch):
    _, _, cog = prepare_evo_leadership(bot, monkeypatch)
    bot._lock_channel_id = None
    bot._lock_message_id = None
    monkeypatch.setattr(bot, "wait_console_channel", AsyncMock(return_value=None))
    assert await bot.acquire_leadership()
    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()
    cog.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_acquisition_does_not_treat_unreadable_history_as_empty(bot, monkeypatch):
    channel = FakeChannel(sender=bot.user)
    channel.send = AsyncMock()
    monkeypatch.setattr(bot, "wait_console_channel", AsyncMock(return_value=channel))
    async def unavailable_history(**kwargs):
        raise PermissionError("history unavailable")
        yield
    monkeypatch.setattr(channel, "history", unavailable_history)
    with pytest.raises(PermissionError):
        await bot.acquire_leadership()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_evo_lock_requires_same_console_as_its_guild_budget(bot, monkeypatch):
    channel, _, cog = prepare_evo_leadership(bot, monkeypatch)
    bot._lock_channel_id = 999
    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()
    cog.suspend.assert_awaited_once()
    bot._lock_channel_id = channel.id
    bot._evo_resume_at = 0
    monkeypatch.setenv("CONSOLE_CHANNEL_NAME", "budget-console")
    channel.name = "budget-console"
    await bot.ensure_evo_leadership()


@pytest.mark.asyncio
async def test_heartbeat_suspends_evo_when_lock_cannot_be_read(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    monkeypatch.setattr(channel, "fetch_message", AsyncMock(side_effect=PermissionError()))

    async def stop_after_iteration(delay):
        bot._closed_flag = True

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_iteration)
    await bot.heartbeat_loop()

    cog.suspend.assert_awaited_once()
    assert own.edits == []
    assert bot._close_calls == []
    assert not bot._evo_leadership_known


@pytest.mark.asyncio
async def test_main_console_resolution_uses_alias_and_evo_target_guild(bot, monkeypatch):
    channel, _, _ = prepare_evo_leadership(bot, monkeypatch)
    channel.name = "budget-console"
    other_channel = FakeChannel(channel_id=43)
    other_channel.name = "budget-console"
    other_guild = FakeGuild(other_channel, guild_id=2)
    bot._connection.guilds = [other_guild, channel.guild]
    monkeypatch.setenv("EVO_ENABLED", "1")
    monkeypatch.setenv("CONSOLE_CHANNEL_NAME", "budget-console")

    assert await bot.wait_console_channel(timeout=1) is channel
    await bot.ensure_console_channel()


@pytest.mark.asyncio
async def test_evo_keeps_its_lock_after_many_regular_console_messages(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    channel.messages.extend(FakeMessage(own.id + index, bot.user, "snapshot") for index in range(1, 151))

    await bot.ensure_evo_leadership()

    assert bot._lock_scan_message_id == own.id + 150
    assert bot._evo_leadership_known
    cog.suspend.assert_not_awaited()


@pytest.mark.asyncio
async def test_evo_detects_rival_before_more_than_fifty_regular_messages(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    channel.messages.append(FakeMessage(own.id + 1, bot.user, f"{main.LOCK_TAG} rival 1700000001"))
    channel.messages.extend(FakeMessage(own.id + index, bot.user, "snapshot") for index in range(2, 151))

    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()

    assert bot._lock_scan_message_id is None
    cog.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_evo_history_watermark_advances_only_after_complete_success(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    await bot.ensure_evo_leadership()
    assert bot._lock_scan_message_id == own.id
    channel.messages.append(FakeMessage(own.id + 1, bot.user, "snapshot"))

    async def interrupted_history(**kwargs):
        yield channel.messages[-1]
        raise PermissionError("second page unavailable")

    original_history = channel.history
    monkeypatch.setattr(channel, "history", interrupted_history)
    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()
    assert bot._lock_scan_message_id == own.id
    channel.messages.append(FakeMessage(own.id + 2, bot.user, f"{main.LOCK_TAG} rival 1700000001"))
    monkeypatch.setattr(channel, "history", original_history)
    with pytest.raises(main.EvoError):
        await bot.ensure_evo_leadership()
    assert bot._lock_scan_message_id == own.id
    assert cog.suspend.await_count == 2


@pytest.mark.asyncio
async def test_acquisition_finds_old_lock_below_hundred_snapshots(bot, monkeypatch):
    channel, own, _ = prepare_evo_leadership(bot, monkeypatch)
    own.content = f"{main.LOCK_TAG} previous 1700000000"
    channel.messages.extend(FakeMessage(own.id + index, bot.user, "snapshot") for index in range(1, 151))
    monkeypatch.setattr(bot, "wait_console_channel", AsyncMock(return_value=channel))

    assert await bot.acquire_leadership()
    assert bot._evo_resume_at > main.time.monotonic()
    with pytest.raises(main.EvoError, match="instance précédente"):
        await bot.ensure_evo_leadership()


@pytest.mark.asyncio
async def test_startup_resolves_the_only_guild_before_evo_configuration(bot, monkeypatch):
    channel, _, cog = prepare_evo_leadership(bot, monkeypatch)
    cog.config = None
    bot._connection.guilds = [channel.guild]
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    monkeypatch.setenv("EVO_ENABLED", "1")

    assert bot._evo_guild_id() == channel.guild.id
    assert await bot.wait_console_channel(timeout=1) is channel
    await bot.ensure_evo_leadership()


@pytest.mark.asyncio
async def test_heartbeat_closes_old_instance_after_its_lock_was_replaced(bot, monkeypatch):
    channel, own, cog = prepare_evo_leadership(bot, monkeypatch)
    channel.messages = [FakeMessage(own.id + 1, bot.user, f"{main.LOCK_TAG} rival 1700000001")]
    missing = main.discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown message")
    monkeypatch.setattr(channel, "fetch_message", AsyncMock(side_effect=missing))

    async def stop_after_iteration(delay):
        bot._closed_flag = True

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_iteration)
    await bot.heartbeat_loop()

    cog.suspend.assert_awaited_once()
    assert bot._close_calls == [True]
    assert bot._exit_calls == [0]
