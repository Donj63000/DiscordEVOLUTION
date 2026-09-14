"""Fixtures synthétiques, sans secret ni serveur Discord réel."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from utils.dofus_wiki import WikiEntry, WikiDetail, search_key
from utils.evo_config import EvoConfig
from utils.evo_safety import ToolContext


def config(**changes):
    return replace(EvoConfig(1, frozenset({10}), "unit-test-placeholder"), **changes)


def entry(identifier="1", name="Coiffe Test", category="Chapeau", level=100, kind="item"):
    section = "items" if kind == "item" else "monsters"
    return WikiEntry(kind, identifier, name, category, level, f"/{section}/test-{identifier}/", search_key(name))


class Member:
    def __init__(self, identifier, name, bot=False):
        self.id, self.name, self.display_name, self.bot = identifier, name, name, bot
        self.guild_permissions = NS(manage_guild=False)


class Channel:
    def __init__(self, guild, identifier, name="general", public=True):
        self.guild, self.id, self.name, self.public = guild, identifier, name, public
        self.denied = set()
        self.history_allowed = True
        self.messages = []
        self.history_calls = 0

    def permissions_for(self, subject):
        allowed = subject.id not in self.denied and (subject.id != 0 or self.public)
        return NS(view_channel=allowed, send_messages=allowed, read_message_history=self.history_allowed)

    def history(self, *, limit):
        self.history_calls += 1
        async def iterate():
            for row in self.messages[:limit]:
                yield row
        return iterate()


class Guild:
    def __init__(self, identifier=1):
        self.id, self.name, self.description = identifier, "Evolution Test", "Description publique"
        self.default_role = NS(id=0)
        self.me = Member(99, "Evo", True)
        self.members = [Member(2, "Val"), Member(3, "Alex"), self.me]
        self.member_count = len(self.members)
        self.text_channels = [Channel(self, 10), Channel(self, 20, "staff", False), Channel(self, 30, "sorties")]

    def get_member(self, identifier):
        return next((m for m in self.members if m.id == identifier), None)

    def get_channel(self, identifier):
        return next((c for c in self.text_channels if c.id == identifier), None)


class Bot:
    def __init__(self, guild, cogs=None):
        self.guilds = [guild]
        self.cogs = cogs or {}
        self.tree = NS(walk_commands=lambda: [])

    def get_cog(self, name):
        return self.cogs.get(name)


def context(settings=None, *, private=False, cogs=None, member_id=2):
    settings = settings or config()
    guild = Guild()
    bot = Bot(guild, cogs)
    return ToolContext(bot, guild, guild.get_channel(10), guild.get_member(member_id),
                       settings, allow_private_fm=private)


def response(outputs, *, usage=None, model="gpt-5.6-luna"):
    return {
        "id": "resp_unit", "status": "completed", "model": model, "output": outputs,
        "usage": {"input_tokens": 800, "output_tokens": 60} if usage is None else usage,
    }


def function(name, args, identifier="call_one"):
    import json
    return {"type": "function_call", "id": "fc_" + identifier, "call_id": identifier,
            "name": name, "arguments": json.dumps(args), "status": "completed"}


def answer(text):
    return response([{"type": "message", "id": "msg_one", "role": "assistant",
                      "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}])


class Transport:
    def __init__(self, replies=(), count=800):
        self.replies = list(replies)
        self.input_count = count
        self.calls = []
        self.count_calls = []
        self.closed = False

    async def count(self, payload):
        self.count_calls.append(payload)
        return self.input_count

    async def create(self, payload):
        self.calls.append(payload)
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        self.closed = True
