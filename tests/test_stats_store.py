import asyncio
import os
import sys
import types
from itertools import count

# Insert repo root in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import discord as fake_discord
    USING_REAL_DISCORD = True
except ImportError:
    fake_discord = sys.modules.setdefault("discord", types.ModuleType("discord"))
    fake_discord.Client = object
    USING_REAL_DISCORD = False


class _Attachment:
    def __init__(self, filename, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


class _Message:
    _ids = count(1)

    def __init__(self, content="", author=None, attachments=None):
        self.id = next(self._ids)
        self.content = content
        self.author = author
        self.attachments = list(attachments or [])
        self.pinned = False
        self.deleted = False

    async def edit(self, *, content=None, attachments=None):
        if content is not None:
            self.content = content
        if attachments is not None:
            self.attachments = list(attachments)

    async def delete(self):
        self.deleted = True

    async def pin(self, *, reason=None):
        self.pinned = True


class _Channel:
    def __init__(self, name="console", messages=None, bot_user=None):
        self.id = 123
        self.name = name
        self._messages = list(messages or [])
        self.bot_user = bot_user

    async def history(self, limit=200):
        yielded = 0
        for msg in list(self._messages):
            if yielded >= limit:
                break
            yielded += 1
            yield msg

    async def pins(self):
        return [m for m in self._messages if getattr(m, "pinned", False)]

    async def fetch_message(self, message_id):
        for msg in self._messages:
            if msg.id == message_id and not msg.deleted:
                return msg
        raise fake_discord.NotFound

    async def send(self, content, *, file=None):
        attachments = []
        if file is not None:
            data = b""
            path = getattr(file, "fp", None)
            if isinstance(path, str) and os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
            elif hasattr(path, "read"):
                position = path.tell()
                path.seek(0)
                data = path.read()
                path.seek(position)
            attachments.append(_Attachment(getattr(file, "filename", ""), data))
        msg = _Message(content, author=self.bot_user, attachments=attachments)
        self._messages.insert(0, msg)
        return msg


class _Utils:
    @staticmethod
    def get(iterable, **attrs):
        for item in iterable:
            if all(getattr(item, k, None) == v for k, v in attrs.items()):
                return item
        return None

    @staticmethod
    def utcnow():
        import datetime
        return datetime.datetime.utcnow()


class _File:
    def __init__(self, fp, filename=None):
        self.fp = fp
        self.filename = filename


if not USING_REAL_DISCORD:
    fake_discord.Message = _Message
    fake_discord.TextChannel = _Channel
    fake_discord.File = _File
    fake_discord.Forbidden = type("Forbidden", (Exception,), {})
    fake_discord.NotFound = type("NotFound", (Exception,), {})
    fake_discord.utils = _Utils

from utils.stats_store import StatsStore


class _Bot:
    def __init__(self, channel):
        self._channel = channel
        self.user = object()
        channel.bot_user = self.user
        self.guilds = [types.SimpleNamespace(text_channels=[channel])]

    def get_all_channels(self):
        return [self._channel]


def test_stats_store_save_and_load(tmp_path, monkeypatch):
    monkeypatch.delenv("STATS_PIN_MESSAGES", raising=False)
    monkeypatch.delenv("CONSOLE_PIN_SNAPSHOTS", raising=False)
    chan = _Channel()
    bot = _Bot(chan)
    store = StatsStore(bot)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        saved = loop.run_until_complete(store.save({"val": 1}))
        assert saved is True
        assert len(chan._messages) == 1
        msg = chan._messages[0]
        assert msg.content.startswith("===BOTSTATS===")
        assert msg.pinned is False

        # simulate reload with a fresh store
        new_store = StatsStore(bot)
        loaded = loop.run_until_complete(new_store.load())
        assert loaded == {"val": 1}
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_stats_store_can_pin_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("STATS_PIN_MESSAGES", "1")
    chan = _Channel()
    bot = _Bot(chan)
    store = StatsStore(bot)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        saved = loop.run_until_complete(store.save({"val": 1}))
        assert saved is True
        assert chan._messages[0].pinned is True
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_stats_store_edits_existing_file_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("STATS_PIN_MESSAGES", raising=False)
    monkeypatch.delenv("CONSOLE_PIN_SNAPSHOTS", raising=False)
    chan = _Channel()
    bot = _Bot(chan)
    store = StatsStore(bot)
    store.min_interval = 0
    first_payload = {f"k{i}": "x" * 80 for i in range(40)}
    second_payload = {**first_payload, "changed": "yes"}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        assert loop.run_until_complete(store.save(first_payload)) is True
        first_message = chan._messages[0]
        assert len(chan._messages) == 1
        assert first_message.attachments[0].filename == "stats_data.json"

        assert loop.run_until_complete(store.save(second_payload)) is True

        assert len(chan._messages) == 1
        assert chan._messages[0] is first_message
        assert first_message.deleted is False
        assert first_message.attachments[0].filename == "stats_data.json"
    finally:
        loop.close()
        asyncio.set_event_loop(None)
