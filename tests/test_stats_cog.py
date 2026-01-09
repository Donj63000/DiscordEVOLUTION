from types import SimpleNamespace

import pytest

from stats import StatsCog


@pytest.mark.asyncio
async def test_stats_log_capping():
    bot = SimpleNamespace()
    cog = StatsCog(bot)
    cog.max_logs = 2

    author = SimpleNamespace(id=1, bot=False, roles=[], name="user")
    channel = SimpleNamespace(id=10)
    guild = SimpleNamespace()

    for idx in range(3):
        message = SimpleNamespace(
            id=idx,
            author=author,
            channel=channel,
            guild=guild,
            content="hi",
        )
        await cog.on_message(message)

    assert len(cog.stats_data["logs"]["messages_created"]) == 2
