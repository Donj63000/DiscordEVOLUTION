import json
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import activite


class FakeMessage:
    def __init__(self, author, content):
        self.author = author
        self.content = content
        self.attachments = []
        self.id = id(self)
        self.created_at = datetime.now(timezone.utc)
        self.edited_at = None
        self.pinned = False
        self.components = []
        self.embeds = []

    async def edit(self, *, content, attachments, **kwargs):
        self.content = content
        self.edited_at = datetime.now(timezone.utc)
        self.attachments = []
        for file in attachments:
            file.fp.seek(0)
            self.attachments.append(SimpleNamespace(
                filename=file.filename, read=AsyncMock(return_value=file.fp.read()),
            ))
        return self

    async def pin(self, **kwargs):
        self.pinned = True



class FakeHistory:
    def __init__(self, messages):
        self._messages = list(messages)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


class FakeConsoleChannel:
    def __init__(self, messages, name="console"):
        self._messages = list(messages)
        self.name = name
        self.id = 400
        self.guild = SimpleNamespace(id=100, filesize_limit=8 * 1024 * 1024)

    async def pins(self):
        return [message for message in self._messages if message.pinned]


    def history(self, limit=1000, oldest_first=False):
        return FakeHistory(self._messages)


@pytest.mark.asyncio
async def test_initialize_data_from_console_sets_initialized(monkeypatch, tmp_path):
    payload = {"next_id": 2, "events": {}}
    content = f"{activite.MARKER_TEXT}\n```json\n{json.dumps(payload)}\n```"
    bot_user = object()
    console = FakeConsoleChannel([FakeMessage(bot_user, content)])
    bot = SimpleNamespace(user=bot_user, guilds=[SimpleNamespace(id=100)])
    cog = activite.ActiviteCog(bot)
    cog._resolve_console_channel = lambda _guild: console
    monkeypatch.setattr(activite, "DATA_FILE", str(tmp_path / "missing.json"))

    await cog.initialize_data()

    assert cog.initialized is True
    assert cog.activities_data == activite.migrate_snapshot(payload, 100)
    assert console._messages[0].pinned
