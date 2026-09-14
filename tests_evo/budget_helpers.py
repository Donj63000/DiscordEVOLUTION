"""Faux salon Discord pour tester le vrai registre console sans réseau ni fichiers."""
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

from tests_evo.helpers import config as make_config
from utils.evo_budget import Budget
from utils.evo_budget_store import ConsoleBudgetStore
from utils.evo_config import EvoError


def missing_message():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown message")


class FakeAttachment:
    def __init__(self, filename, raw):
        self.filename, self.raw = filename, raw
        self.size = len(raw)

    async def read(self):
        return self.raw


class FakeMessage:
    def __init__(self, channel, identifier, content, attachments=()):
        self.channel = channel
        self.id = identifier
        self.author = channel.bot.user
        self.content = content
        self.attachments = list(attachments)
        self.pinned = False
        self.created_at = datetime.now(timezone.utc)
        self.edited_at = None
        self.deleted = False

    async def pin(self, **kwargs):
        if self.channel.pin_error:
            raise self.channel.pin_error
        self.pinned = True

    async def edit(self, *, content, attachments, **kwargs):
        if self.deleted:
            raise missing_message()
        if self.channel.write_error:
            raise self.channel.write_error
        if not self.channel.ignore_edits:
            self.content = content
            self.attachments = [FakeAttachment(file.filename, file.fp.read()) for file in attachments]
            self.edited_at = datetime.now(timezone.utc)
        self.channel.edits += 1
        if self.channel.after_write:
            await self.channel.after_write()
        if self.channel.commit_error:
            raise self.channel.commit_error
        return self

    async def delete(self):
        self.deleted = True
        self.channel.messages.remove(self)


class FakeConsole:
    def __init__(self, bot, guild):
        self.id = 999
        self.name = "console"
        self.guild, self.bot = guild, bot
        self.messages = []
        self.write_error = self.commit_error = self.read_error = self.pin_error = None
        self.after_write = None
        self.ignore_edits = False
        self.edits = self.sends = self.reads = 0

    async def pins(self):
        if self.read_error:
            raise self.read_error
        return [message for message in self.messages if message.pinned]

    async def fetch_message(self, identifier):
        self.reads += 1
        if self.read_error:
            raise self.read_error
        for message in self.messages:
            if message.id == identifier:
                return message
        raise missing_message()

    async def history(self, *, limit=None, **kwargs):
        if self.read_error:
            raise self.read_error
        messages = list(reversed(self.messages))
        for message in messages if limit is None else messages[:limit]:
            yield message

    async def send(self, content, *, file=None, **kwargs):
        if self.write_error:
            raise self.write_error
        self.sends += 1
        attachments = [FakeAttachment(file.filename, file.fp.read())] if file else []
        message = FakeMessage(self, self.sends, content, attachments)
        self.messages.append(message)
        if self.after_write:
            await self.after_write()
        if self.commit_error:
            raise self.commit_error
        return message


class FakeBudgetBot:
    def __init__(self, guild_id=1):
        self.user = SimpleNamespace(id=9999)
        self.guild = SimpleNamespace(id=guild_id, filesize_limit=8 * 1024 * 1024)
        self.console = FakeConsole(self, self.guild)
        self.guild.text_channels = [self.console]
        self.guild.get_channel = lambda identifier: self.console if identifier == self.console.id else None
        self.guilds = [self.guild]
        self.leader = True
        self.leadership_checks = 0

    def get_guild(self, identifier):
        return self.guild if identifier == self.guild.id else None

    async def ensure_evo_leadership(self):
        self.leadership_checks += 1
        if not self.leader:
            raise EvoError("Une autre instance est active : Evo est suspendu.")


def make_store(guild_id=1):
    bot = FakeBudgetBot(guild_id)
    return ConsoleBudgetStore(bot, guild_id)


async def create_budget(config=None, *, clock=None):
    settings = config or make_config()
    budget = Budget(settings, make_store(settings.guild_id), clock=clock)
    await budget.initialize()
    return budget
