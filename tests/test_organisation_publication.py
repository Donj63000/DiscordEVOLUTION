import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from organisation import OrganisationCog


@pytest.mark.asyncio
async def test_two_previews_cannot_publish_the_same_draft_twice():
    cog = OrganisationCog(SimpleNamespace())
    draft = SimpleNamespace(id="draft-one", guild_id=100)
    cog._drafts[1] = draft
    started = asyncio.Event()
    release = asyncio.Event()

    async def publish(*args):
        started.set()
        await release.wait()
        return True, ""

    cog._publish_draft = AsyncMock(side_effect=publish)

    def interaction():
        return SimpleNamespace(
            user=SimpleNamespace(id=1), guild=SimpleNamespace(id=100),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    first_view = cog._PreviewView(cog, 1)
    second_view = cog._PreviewView(cog, 1)
    first = asyncio.create_task(first_view.publish.callback(interaction()))
    await started.wait()
    second = asyncio.create_task(second_view.publish.callback(interaction()))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    cog._publish_draft.assert_awaited_once()
    assert 1 not in cog._drafts


@pytest.mark.asyncio
async def test_failed_publication_preserves_draft_and_can_be_retried():
    cog = OrganisationCog(SimpleNamespace())
    draft = SimpleNamespace(id="draft-one", guild_id=100)
    cog._drafts[1] = draft
    cog._publish_draft = AsyncMock(side_effect=[(False, "permission refusée"), (True, "")])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1), guild=SimpleNamespace(id=100),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    view = cog._PreviewView(cog, 1)

    await view.publish.callback(interaction)
    assert cog._drafts[1] is draft
    await view.publish.callback(interaction)

    assert cog._publish_draft.await_count == 2
    assert 1 not in cog._drafts
