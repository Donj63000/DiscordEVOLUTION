import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models import EventData
from utils.storage import EventStore


def make_event():
    return EventData(
        guild_id=1,
        channel_id=2,
        title="Donjon",
        description="Sortie de guilde",
        starts_at=datetime(2026, 10, 21, 18, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_postgres_load_decodes_jsonb_strings_and_keeps_transcript_lists():
    event = make_event()
    transcript = ["Premier message", "Deuxième message"]
    store = EventStore(SimpleNamespace())
    store.backend = "postgres"
    store.db = SimpleNamespace(
        fetch=AsyncMock(
            side_effect=[
                [{"id": "event", "data": event.model_dump_json()}],
                [{"id": "conversation", "data": json.dumps(transcript)}],
            ]
        )
    )

    data = await store.load()

    assert data["events"]["event"] == event
    assert data["conversations"] == {"conversation": transcript}


@pytest.mark.asyncio
async def test_postgres_saves_jsonb_as_json_strings():
    event = make_event()
    store = EventStore(SimpleNamespace())
    store.backend = "postgres"
    store.db = SimpleNamespace(execute=AsyncMock())

    await store.save_event("event", event)
    await store.save_conversation("conversation", ["Bonjour"])
    await store.save_conversation("empty", [])

    event_json = store.db.execute.await_args_list[0].args[2]
    assert isinstance(event_json, str)
    assert EventData.from_dict(json.loads(event_json)) == event
    transcript_json = store.db.execute.await_args_list[1].args[2]
    assert isinstance(transcript_json, str)
    assert json.loads(transcript_json) == ["Bonjour"]
    assert json.loads(store.db.execute.await_args_list[2].args[2]) == []


@pytest.mark.asyncio
async def test_console_saves_datetime_and_restores_event(monkeypatch):
    event = make_event()
    bot_user = object()
    store = EventStore(SimpleNamespace(user=bot_user))
    channel = SimpleNamespace(send=AsyncMock())
    store.console_channel = channel

    await store.save_event("event", event)

    content = channel.send.await_args.args[0]
    message = SimpleNamespace(author=bot_user, content=content, attachments=[])
    monkeypatch.setattr(
        "utils.storage.fetch_channel_history", AsyncMock(return_value=[message])
    )
    store.events.clear()

    data = await store.load()

    assert data["events"]["event"] == event


@pytest.mark.asyncio
async def test_concurrent_console_saves_publish_latest_snapshot_last(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = EventStore(SimpleNamespace())
    first_send_started = asyncio.Event()
    release_first_send = asyncio.Event()
    sent_snapshots = []
    files = []

    async def send(content, *, file):
        files.append(file)
        snapshot = json.loads(file.fp.read())
        if len(files) == 1:
            first_send_started.set()
            await release_first_send.wait()
        sent_snapshots.append(snapshot)

    store.console_channel = SimpleNamespace(send=send)
    first = asyncio.create_task(store.save_conversation("first", ["x" * 2000]))
    second = None
    try:
        await asyncio.wait_for(first_send_started.wait(), timeout=1)
        second = asyncio.create_task(store.save_conversation("second", ["y" * 2000]))
        # Let the second save reach the publication lock while the first is pending.
        await asyncio.sleep(0)
    finally:
        release_first_send.set()
        await asyncio.gather(first, *([second] if second is not None else []))

    assert len(sent_snapshots) == 2
    assert sent_snapshots[-1]["conversations"] == store.conversations
    assert files[0].fp is not files[1].fp
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_console_uses_attachment_when_emojis_exceed_discord_message_limit():
    store = EventStore(SimpleNamespace())
    saved = []

    async def send(content, *, file=None):
        assert len(content.encode("utf-16-le")) // 2 <= 2000
        saved.append(json.loads(file.fp.read()))

    store.console_channel = SimpleNamespace(send=send)

    await store.save_conversation("conversation", ["😀" * 1000])

    assert saved[0]["conversations"] == store.conversations
