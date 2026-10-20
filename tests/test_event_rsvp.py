import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from event_conversation import EventConversationCog, RSVPView


def interaction(user):
    return SimpleNamespace(
        user=user,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def parent():
    return SimpleNamespace(console=None, _persist_store_data=AsyncMock())


@pytest.mark.asyncio
async def test_last_rsvp_slot_is_reserved_once_under_simultaneous_clicks():
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_add(*args, **kwargs):
        started.set()
        await release.wait()

    first = SimpleNamespace(id=1, add_roles=AsyncMock(side_effect=slow_add))
    second = SimpleNamespace(id=2, add_roles=AsyncMock())
    data = {"going": []}
    view = RSVPView(SimpleNamespace(id=42), 1, parent_cog=parent(), store_data=data)
    task_one = asyncio.create_task(view.rsvp_yes.callback(interaction(first)))
    await started.wait()
    task_two = asyncio.create_task(view.rsvp_yes.callback(interaction(second)))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(task_one, task_two)

    assert data["going"] == [1]
    second.add_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_role_removal_keeps_the_member_and_capacity_reserved():
    forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    user = SimpleNamespace(id=1, remove_roles=AsyncMock(side_effect=forbidden))
    data = {"going": [1]}
    cog = parent()
    view = RSVPView(SimpleNamespace(id=42), 1, parent_cog=cog, store_data=data)

    await view.rsvp_no.callback(interaction(user))

    assert view._going == {1}
    assert data["going"] == [1]
    cog._persist_store_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_homonymous_events_create_their_own_role():
    existing = SimpleNamespace(id=42, name="Participe à l'event Raid")
    created = SimpleNamespace(id=43, name=existing.name)
    guild = SimpleNamespace(roles=[existing], create_role=AsyncMock(return_value=created))
    cog = EventConversationCog(SimpleNamespace())

    role = await cog._create_event_role(guild, SimpleNamespace(name="Raid"))

    assert role is created
    guild.create_role.assert_awaited_once()


@pytest.mark.asyncio
async def test_homonymous_events_create_their_own_private_channel():
    category = SimpleNamespace(name="Événements", text_channels=[SimpleNamespace(name="event-raid")])
    guild = SimpleNamespace(
        categories=[category], roles=[], default_role=object(),
        create_text_channel=AsyncMock(), create_category=AsyncMock(return_value=category),
    )
    role = object()
    cog = EventConversationCog(SimpleNamespace())

    channel = await cog._create_event_channel(guild, SimpleNamespace(name="Raid"), role)

    assert channel is guild.create_text_channel.return_value
    guild.create_text_channel.assert_awaited_once()
