import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from utils.console_store import ConsoleStore


def event_message(author, title):
    return SimpleNamespace(
        id=id(title), author=author, webhook_id=None,
        content="```event\n" + json.dumps({"event_id": 42, "title": title}) + "\n```",
    )


def store_with_messages(messages):
    store = ConsoleStore(SimpleNamespace(user=object()))

    async def history(*, limit):
        for message in messages:
            yield message

    store._channel = AsyncMock(return_value=SimpleNamespace(id=id(store), history=history))
    return store


@pytest.mark.asyncio
async def test_event_records_cannot_be_injected_by_another_member():
    message = event_message(object(), "Faux événement")
    store = store_with_messages([message])

    assert await store.load_all() == {}


@pytest.mark.asyncio
async def test_newest_duplicate_event_record_wins():
    store = store_with_messages([])
    newest = event_message(store.bot.user, "Titre corrigé")
    older = event_message(store.bot.user, "Ancien titre")

    async def history(*, limit):
        yield newest
        yield older

    store._channel.return_value.history = history
    loaded = await store.load_all()

    assert loaded[42]["title"] == "Titre corrigé"
    assert loaded[42]["_msg"] is newest


@pytest.mark.asyncio
async def test_failed_remote_deletion_keeps_record_in_cache():
    store = store_with_messages([])
    error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Accès refusé")
    message = SimpleNamespace(delete=AsyncMock(side_effect=error))
    store._cache[42] = {"event_id": 42, "_msg": message}

    with pytest.raises(discord.Forbidden):
        await store.delete(42)

    assert store._cache[42]["_msg"] is message
