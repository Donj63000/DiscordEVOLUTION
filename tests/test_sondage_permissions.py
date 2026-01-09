from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sondage import POLL_STORAGE, SondageCog


class FakeContext:
    def __init__(self, author):
        self.author = author
        self.sent = []

    async def send(self, content=None, *, embed=None):
        self.sent.append({"content": content, "embed": embed})


@pytest.mark.asyncio
async def test_manual_close_requires_author_or_staff():
    POLL_STORAGE.clear()
    poll_id = 123
    POLL_STORAGE[poll_id] = {"author_id": 1}
    author = SimpleNamespace(
        id=2,
        roles=[],
        guild_permissions=SimpleNamespace(manage_messages=False, administrator=False),
    )
    ctx = FakeContext(author)
    cog = SondageCog(SimpleNamespace())
    cog.close_poll = AsyncMock()

    await cog.manual_close_poll.callback(cog, ctx, message_id=poll_id)

    assert ctx.sent
    cog.close_poll.assert_not_awaited()
    cog.cog_unload()
    POLL_STORAGE.clear()


@pytest.mark.asyncio
async def test_manual_close_allows_author():
    POLL_STORAGE.clear()
    poll_id = 456
    POLL_STORAGE[poll_id] = {"author_id": 1}
    author = SimpleNamespace(
        id=1,
        roles=[],
        guild_permissions=SimpleNamespace(manage_messages=False, administrator=False),
    )
    ctx = FakeContext(author)
    cog = SondageCog(SimpleNamespace())
    cog.close_poll = AsyncMock()

    await cog.manual_close_poll.callback(cog, ctx, message_id=poll_id)

    cog.close_poll.assert_awaited_once_with(poll_id)
    assert poll_id not in POLL_STORAGE
    cog.cog_unload()
    POLL_STORAGE.clear()
