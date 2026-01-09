import json
from types import SimpleNamespace

import pytest

import activite


class FakeMessage:
    def __init__(self, author, content):
        self.author = author
        self.content = content
        self.attachments = []


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

    def history(self, limit=1000, oldest_first=False):
        return FakeHistory(self._messages)


@pytest.mark.asyncio
async def test_initialize_data_from_console_sets_initialized(monkeypatch, tmp_path):
    payload = {"next_id": 2, "events": {}}
    content = f"{activite.MARKER_TEXT}\n```json\n{json.dumps(payload)}\n```"
    bot_user = object()
    console = FakeConsoleChannel([FakeMessage(bot_user, content)])
    bot = SimpleNamespace(user=bot_user, guilds=[SimpleNamespace()])
    cog = activite.ActiviteCog(bot)
    cog._resolve_console_channel = lambda _guild: console
    monkeypatch.setattr(activite, "DATA_FILE", str(tmp_path / "missing.json"))

    await cog.initialize_data()

    assert cog.initialized is True
    assert cog.activities_data == payload
