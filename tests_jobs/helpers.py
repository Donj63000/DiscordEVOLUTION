"""Doubles du transport pour tester les corps originaux, jamais une copie de /job.

Le mode isolé charge l'AST de job.py et de discord_history.py sans leurs imports
Discord ni les décorateurs du SDK. Le mode SDK exécute les mêmes scénarios avec
les classes réelles quand discord.py est installé. Aucun module global Discord
n'est remplacé et aucun appel réseau réel n'est effectué.
"""
from __future__ import annotations

import ast
import asyncio
from collections import defaultdict
from contextlib import suppress
import copy
import hashlib
import inspect
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace as NS
import unicodedata
from unittest.mock import AsyncMock

import aiohttp

from utils.job_snapshot import (
    InvalidJobSnapshot, JobSnapshotReader, MAX_SNAPSHOT_BYTES, validate_jobs_payload,
)

ROOT = Path(__file__).resolve().parents[1]


class HTTPErrorDouble(Exception):
    def __init__(self, response, data):
        super().__init__(data.get("message", "test"))
        self.status, self.code = response.status, data.get("code", 0)


class NotFoundDouble(HTTPErrorDouble):
    pass


class ForbiddenDouble(HTTPErrorDouble):
    pass


class FileDouble:
    def __init__(self, fp, *, filename):
        self.fp, self.filename = fp, filename

    def close(self):
        self.fp.close()


class EmbedDouble:
    def __init__(self, *, title=None, description=None, color=None):
        self.title, self.description, self.color = title, description, color
        self.fields = []

    def add_field(self, **kwargs):
        self.fields.append(NS(**kwargs))

    def set_thumbnail(self, **kwargs):
        return self


def discord_double():
    colors = NS(**{name: lambda: 0 for name in (
        "red", "orange", "green", "light_grey", "blurple", "purple", "gold", "blue",
    )})
    return NS(
        HTTPException=HTTPErrorDouble, NotFound=NotFoundDouble, Forbidden=ForbiddenDouble,
        ClientException=RuntimeError, File=FileDouble, Embed=EmbedDouble, Color=colors,
        Object=lambda **kwargs: NS(**kwargs),
        AllowedMentions=NS(none=lambda: None),
        utils=NS(find=lambda predicate, rows: next((row for row in rows if predicate(row)), None)),
    )


def load_ast(path: str, namespace: dict) -> NS:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ClassDef) and node.name == "JobCog":
            node.bases = []
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method.decorator_list = [
                        decorator for decorator in method.decorator_list
                        if isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
                    ]
        body.append(node)
    body.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    namespace = {**namespace, "__file__": str(ROOT / path)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(ROOT / path), "exec"), namespace)
    return NS(**namespace)


def isolated_job():
    wire = discord_double()
    history = load_ast("utils/discord_history.py", {
        "asyncio": asyncio, "logging": logging, "os": os, "time": time, "discord": wire,
    })
    module = load_ast("job.py", {
        "asyncio": asyncio, "copy": copy, "os": os, "json": json, "re": re,
        "unicodedata": unicodedata, "tempfile": tempfile, "hashlib": hashlib,
        "io": io, "inspect": inspect, "logging": logging, "time": time, "suppress": suppress,
        "discord": wire, "aiohttp": aiohttp, "defaultdict": defaultdict,
        "fetch_channel_history": history.fetch_channel_history,
        "JobSnapshotReader": JobSnapshotReader, "InvalidJobSnapshot": InvalidJobSnapshot,
        "MAX_SNAPSHOT_BYTES": MAX_SNAPSHOT_BYTES, "validate_jobs_payload": validate_jobs_payload,
    })
    # Le NS expose le dictionnaire des globals utilisé par les fonctions pour monkeypatch.
    module.globals = module.JobCog.__init__.__globals__
    return module, wire


def http_error(wire, kind="HTTPException", status=500, code=0):
    return getattr(wire, kind)(NS(status=status, reason="test"), {"code": code, "message": "erreur synthétique"})


class Attachment:
    def __init__(self, raw: bytes, filename="jobs_data.json"):
        self.filename, self.raw, self.size = filename, raw, len(raw)
        self.error = None
        self.reads = 0

    async def read(self):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.raw


def encoded(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def header(payload, suffix=""):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"===BOTJOBS=== etag:{hashlib.md5(canonical.encode('utf-8')).hexdigest()}{suffix}"


class Message:
    def __init__(self, channel, identifier, content="", attachments=(), *, pinned=False, author=None):
        self.channel, self.guild = channel, channel.guild
        self.id, self.content, self.attachments, self.pinned = identifier, content, list(attachments), pinned
        self.author = author or channel.bot.user
        self.webhook_id = None
        self.deleted = False

    async def pin(self, **kwargs):
        self.channel.pin_calls += 1
        if self.channel.pin_error is not None:
            raise self.channel.pin_error
        self.pinned = True

    async def edit(self, *, content, attachments, **kwargs):
        if self.channel.write_error is not None:
            raise self.channel.write_error
        self.channel.edits += 1
        if not self.channel.ignore_edits:
            self.content = content
            self.attachments = [Attachment(file.fp.read(), file.filename) for file in attachments]
        await self.channel.after_write()
        return self

    async def delete(self):
        self.deleted = True


class Console:
    def __init__(self, bot, guild, wire):
        self.id, self.name = 555, "console"
        self.bot, self.guild, self.wire = bot, guild, wire
        self.messages = []
        self.history_calls, self.fetches, self.sends, self.edits, self.pin_calls = [], 0, 0, 0, 0
        self.pin_error = self.write_error = self.history_error = self.pins_error = self.fetch_error = None
        self.after_write = AsyncMock()
        self.ignore_edits = self.ignore_before = False
        self.viewable = self.readable = True

    def permissions_for(self, member):
        return NS(view_channel=self.viewable, read_message_history=self.readable)

    def add(self, payload, identifier=10, *, form="attachment", pinned=False, suffix=""):
        if form == "attachment":
            message = Message(self, identifier, header(payload, suffix), [Attachment(encoded(payload))], pinned=pinned)
        elif form == "inline":
            message = Message(self, identifier, f"{header(payload)}\n```json\n{encoded(payload).decode()}\n```", pinned=pinned)
        else:
            raise AssertionError(form)
        self.messages.append(message)
        return message

    async def pins(self):
        if self.pins_error is not None:
            raise self.pins_error
        return [message for message in self.messages if message.pinned and not message.deleted]

    async def fetch_message(self, identifier):
        self.fetches += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        for message in self.messages:
            if message.id == identifier and not message.deleted:
                return message
        raise http_error(self.wire, "NotFound", 404, 10008)

    async def history(self, *, limit, before=None, oldest_first=False, **kwargs):
        self.history_calls.append((limit, before.id if before else None))
        if self.history_error is not None:
            raise self.history_error
        rows = sorted(
            (m for m in self.messages if not m.deleted and (
                before is None or self.ignore_before or m.id < before.id
            )),
            key=lambda m: m.id, reverse=not oldest_first,
        )
        for message in rows if limit is None else rows[:limit]:
            await asyncio.sleep(0)
            yield message

    async def send(self, content, *, file=None, **kwargs):
        if self.write_error is not None:
            raise self.write_error
        self.sends += 1
        message = Message(
            self, max((m.id for m in self.messages), default=0) + 1, content,
            [Attachment(file.fp.read(), file.filename)] if file is not None else [],
        )
        self.messages.append(message)
        await self.after_write()
        return message
