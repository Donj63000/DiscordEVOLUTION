from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import iastaff
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


@pytest.mark.asyncio
async def test_send_long_reply_sanitizes_mentions(monkeypatch, iastaff_cog):
    allowed_mentions = object()

    class DummyChannel:
        def __init__(self):
            self.sent: list[dict] = []

        async def send(self, *, embed=None, files=None, allowed_mentions=None):
            self.sent.append(
                {
                    "embed": embed,
                    "files": files,
                    "allowed_mentions": allowed_mentions,
                }
            )

    channel = DummyChannel()
    monkeypatch.setattr("iastaff._allowed_mentions", lambda: allowed_mentions)
    monkeypatch.setattr(
        iastaff_cog,
        "_make_embed",
        lambda part, idx, total: (f"embed-{part}", None),
    )

    await iastaff_cog._send_long_reply(
        channel, "Attention @everyone, ping <@123>!"
    )

    assert channel.sent, "The reply should be sent through the channel."
    embed_text = channel.sent[0]["embed"]
    assert "@everyone" not in embed_text
    assert "<@123" not in embed_text
    assert channel.sent[0]["allowed_mentions"] is allowed_mentions


def test_make_messages_rules_mode(monkeypatch, iastaff_cog):
    monkeypatch.setattr("iastaff.IASTAFF_RULES_MODE", "auto")
    channel_ctx = "Discussion sur les avertissements et le règlement"
    user_msg = "Peux-tu rappeler le règlement warn ?"

    messages = iastaff_cog._make_messages(channel_ctx, 123, user_msg)

    assert any(
        msg.get("role") == "developer"
        and any(
            part.get("text") == iastaff.GUILD_RULES for part in msg.get("content", [])
        )
        for msg in messages
    )

    no_rule_messages = iastaff_cog._make_messages("Contexte neutre", 123, "Salut")

    assert not any(
        msg.get("role") == "developer"
        and any(
            part.get("text") == iastaff.GUILD_RULES for part in msg.get("content", [])
        )
        for msg in no_rule_messages
    )


def test_make_messages_trims_context_and_history(monkeypatch, iastaff_cog):
    monkeypatch.setattr("iastaff.INPUT_MAX_CHARS", 20)
    monkeypatch.setattr("iastaff.MODERATION_PROMPT", "MOD")
    monkeypatch.setattr("iastaff.GUILD_RULES", "RULES")
    iastaff_cog.system_prompt = "SYS"
    channel_ctx = "Contexte trop long"  # 17 chars
    channel_id = 999
    iastaff_cog.history[channel_id] = [
        {"role": "user", "text": "hist1"},
        {"role": "assistant", "text": "hist2"},
    ]

    messages = iastaff_cog._make_messages(channel_ctx, channel_id, "user")

    payload = [
        content.get("text")
        for message in messages
        for content in message.get("content", [])
        if isinstance(content, dict)
    ]

    assert channel_ctx not in payload, "Channel context should be trimmed when overflowing."
    assert "hist1" not in payload and "hist2" not in payload
