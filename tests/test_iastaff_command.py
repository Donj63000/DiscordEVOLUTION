from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from iastaff import IAStaff, STAFF_ROLE_NAME


class FakeChannel:
    def __init__(self, channel_id=321):
        self.id = channel_id


class FakeContext:
    def __init__(self, channel_id=321):
        self.channel = FakeChannel(channel_id)
        staff_role = SimpleNamespace(name=STAFF_ROLE_NAME)
        self.author = SimpleNamespace(id=42, display_name="Mod", roles=[staff_role])
        self.replies: list[str] = []

    async def reply(self, content, *, mention_author=False):
        self.replies.append(content)


@pytest.fixture
def iastaff_cog(monkeypatch):
    monkeypatch.setattr("iastaff.AsyncOpenAI", None)
    bot = SimpleNamespace(
        guilds=[],
        get_channel=lambda _: None,
    )
    bot.wait_until_ready = AsyncMock(return_value=None)
    bot.is_ready = lambda: True
    return IAStaff(bot)


@pytest.mark.asyncio
async def test_iastaff_reset_clears_history(iastaff_cog):
    ctx = FakeContext(channel_id=555)
    iastaff_cog.history[ctx.channel.id] = [{"role": "user", "text": "hi"}]

    await IAStaff.iastaff_cmd(iastaff_cog, ctx, message="reset")

    assert ctx.channel.id not in iastaff_cog.history
    assert ctx.replies and "Historique" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_iastaff_info_reports_current_model(iastaff_cog):
    ctx = FakeContext()

    await IAStaff.iastaff_cmd(iastaff_cog, ctx, message="info")

    last = ctx.replies[-1]
    assert f"Model: `{iastaff_cog.model}`" in last
    assert "Timeout" in last


@pytest.mark.asyncio
async def test_iastaff_model_switches_runtime_choice(iastaff_cog):
    ctx = FakeContext()

    await IAStaff.iastaff_cmd(iastaff_cog, ctx, message="model GPT 5 MINI")

    assert iastaff_cog.model == "gpt-5-mini"
    assert ctx.replies and "gpt-5-mini" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_iastaff_forwards_regular_message(iastaff_cog):
    ctx = FakeContext()
    iastaff_cog.handle_staff_message = AsyncMock()

    await IAStaff.iastaff_cmd(iastaff_cog, ctx, message="Bonjour IA")

    iastaff_cog.handle_staff_message.assert_awaited_once_with(
        ctx.channel, ctx.author, "Bonjour IA", ctx=ctx
    )
