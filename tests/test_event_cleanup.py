"""Legacy event restoration must not authorize destructive cleanup."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from event_conversation import EventConversationCog


@pytest.mark.asyncio
async def test_restore_unsigned_event_is_read_only_on_restart(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.setenv("EVENT_PROVENANCE_KEY", "ab" * 32)
    bot = SimpleNamespace(user=SimpleNamespace(id=900), guilds=[SimpleNamespace(id=1)],
                          fetch_channel=AsyncMock(), add_view=MagicMock())
    cog = EventConversationCog(bot)
    record = {
        "guild_id": 1, "channel_id": 55, "message_id": 1, "role_id": 42,
        "event_id": 7, "event_channel_id": 99,
        "ends_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    assert not await cog._restore_view(record)
    assert cog._records["7"] is record
    bot.fetch_channel.assert_not_awaited()
    bot.add_view.assert_not_called()
    assert not cog._tasks
