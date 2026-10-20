import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.payload import IOBasePayload
from discord.http import handle_message_parameters

from utils.console_json_store import ConsoleJSONSnapshotStore


def snapshot_store():
    bot = SimpleNamespace(user=object())
    store = ConsoleJSONSnapshotStore(bot, marker="===TEST===", filename="test.json")
    return store, bot


def snapshot_message(bot, identifier, payload, *, month, edited_month=None):
    return SimpleNamespace(
        id=identifier, author=bot.user, webhook_id=None, attachments=[],
        content=f"===TEST===\n```json\n{json.dumps(payload)}\n```",
        created_at=datetime(2026, month, 1, tzinfo=timezone.utc),
        edited_at=(datetime(2026, edited_month, 1, tzinfo=timezone.utc)
                   if edited_month else None),
        edit=AsyncMock(side_effect=OSError("édition impossible")), delete=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"value": 2}, {"value": "x" * 2500}])
async def test_failed_replacement_preserves_previous_snapshot(payload):
    store, bot = snapshot_store()
    previous = snapshot_message(bot, 1, {"value": 1}, month=1)
    channel = SimpleNamespace(send=AsyncMock(side_effect=OSError("envoi impossible")))
    store.load_latest = AsyncMock(return_value=(previous, {"value": 1}))

    with pytest.raises(OSError, match="envoi impossible"):
        await store.save(payload, channel=channel)

    previous.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_snapshot_uses_last_edit_as_its_version():
    store, bot = snapshot_store()
    current = snapshot_message(bot, 1, {"value": 2}, month=1, edited_month=9)
    stale = snapshot_message(bot, 2, {"value": 1}, month=8)
    channel = SimpleNamespace(id=1234, pins=AsyncMock(return_value=[current, stale]))
    store._history_limit = lambda: 0

    message, payload = await store.load_latest(channel=channel)

    assert message is current
    assert payload == {"value": 2}


@pytest.mark.asyncio
async def test_inline_payload_with_backticks_roundtrips():
    store, _ = snapshot_store()
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=1)))
    store.load_latest = AsyncMock(return_value=(None, None))
    payload = {"title": "Texte ``` exemple"}

    await store.save(payload, channel=channel)

    content = channel.send.await_args.args[0]
    assert store._extract_inline_json(content) == payload


@pytest.mark.asyncio
async def test_message_size_accounts_for_non_bmp_characters():
    store, _ = snapshot_store()
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=1)))
    store.load_latest = AsyncMock(return_value=(None, None))

    await store.save({"title": "😀" * 1000}, channel=channel)

    args, kwargs = channel.send.await_args
    assert len(args[0].encode("utf-16-le")) // 2 <= 2000
    assert "file" in kwargs


def test_snapshots_require_a_known_bot_author():
    store, bot = snapshot_store()
    message = snapshot_message(bot, 1, {}, month=1)
    bot.user = None
    assert not store._is_snapshot_message(message)


@pytest.mark.asyncio
async def test_all_paginated_pins_are_considered():
    store, bot = snapshot_store()
    current = snapshot_message(bot, 1, {"value": 2}, month=1, edited_month=9)

    def pins(*, limit):
        assert limit is None

        async def pages():
            yield current

        return pages()

    store._history_limit = lambda: 0
    message, payload = await store.load_latest(channel=SimpleNamespace(pins=pins))

    assert message is current
    assert payload == {"value": 2}


@pytest.mark.asyncio
async def test_file_replacement_survives_discord_transport_retry():
    store, bot = snapshot_store()
    previous = snapshot_message(bot, 1, {"value": 1}, month=1)
    payload = {"value": "x" * 2500}
    store.load_latest = AsyncMock(return_value=(previous, {"value": 1}))
    edited_files = []
    sent_bodies = []

    async def edit(*, content, attachments):
        edited_files.extend(attachments)
        # discord.Message.edit closes its File wrapper when an HTTP request fails.
        with handle_message_parameters(content=content, attachments=attachments):
            raise OSError("édition impossible")

    async def send(content, *, file):
        with handle_message_parameters(content=content, file=file):
            for attempt in range(2):
                file.reset(seek=attempt)
                chunks = []

                async def write(chunk):
                    chunks.append(chunk)

                # aiohttp closes the stream after each multipart attempt. A File
                # must keep its close guard until Discord has finished retrying.
                await IOBasePayload(file.fp).write(SimpleNamespace(write=write))
                sent_bodies.append(b"".join(chunks))
        assert file is not edited_files[0]
        return SimpleNamespace(id=2)

    previous.edit = edit
    result = await store.save(payload, channel=SimpleNamespace(send=send))

    assert result.id == 2
    assert len(sent_bodies) == 2
    assert all(json.loads(body) == payload for body in sent_bodies)
    previous.delete.assert_awaited_once()
