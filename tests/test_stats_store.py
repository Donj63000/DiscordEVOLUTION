import asyncio
import os
import sys
import types
from itertools import count

# Insert repo root in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Fake discord module for StatsStore
fake_discord = sys.modules.setdefault("discord", types.ModuleType("discord"))
fake_discord.Client = object


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

    async def edit(self, *, content=None):
        if content is not None:
            self.content = content

    async def delete(self):
        self.deleted = True

    async def pin(self, *, reason=None):
        self.pinned = True


class _Channel:
    def __init__(self, name="console", messages=None, bot_user=None):
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

    async def send(self, content, *, file=None):
        attachments = []
        if file is not None:
            data = b""
            path = getattr(file, "fp", None)
            if isinstance(path, str) and os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
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

    def get_all_channels(self):
        return [self._channel]


def test_stats_store_save_and_load(tmp_path):
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
        assert msg.content.startswith("```json")

        # simulate reload with a fresh store
        new_store = StatsStore(bot)
        loaded = loop.run_until_complete(new_store.load())
        assert loaded == {"val": 1}
    finally:
        loop.close()
        asyncio.set_event_loop(None)
