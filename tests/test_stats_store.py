import os
import sys
import json
import asyncio
import types

# Insert repo root in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Fake discord module for StatsStore
fake_discord = sys.modules.setdefault("discord", types.ModuleType("discord"))
fake_discord.Client = object

class _Message:
    def __init__(self, content="", author=None):
        self.content = content
        self.author = author
        self.pinned = False
    async def edit(self, *, content=None):
        if content is not None:
            self.content = content
    async def pin(self, *, reason=None):
        self.pinned = True

class _Channel:
    def __init__(self, name="🎮 console 🎮", messages=None, bot_user=None):
        self.name = name
        self._messages = list(messages or [])
        self.bot_user = bot_user
    async def history(self, limit=200):
        for m in list(self._messages):
            yield m
    async def send(self, content):
        m = _Message(content, author=self.bot_user)
        self._messages.insert(0, m)
        return m

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

fake_discord.Message = _Message
fake_discord.TextChannel = _Channel
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
    loop.run_until_complete(store.save({"val": 1}))
    assert len(chan._messages) == 1
    msg = chan._messages[0]
    assert "\n  \"val\": 1" in msg.content
