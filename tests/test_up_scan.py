from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import up
from up import PromotionOutcome, UpCog, VeteranPromotionButton


class FakeHistory:
    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class FakeChannel:
    def __init__(self, messages=None, channel_id=123, name="general"):
        self.id = channel_id
        self.name = name
        self.messages = messages or []
        self.history_calls = []
        self.sent_messages = []

    def history(self, *args, **kwargs):
        self.history_calls.append((args, kwargs))
        return FakeHistory(self.messages)

    async def send(self, *args, **kwargs):
        message = SimpleNamespace(id=len(self.sent_messages) + 1000, edit=AsyncMock())
        self.sent_messages.append({"args": args, "kwargs": kwargs, "message": message})
        return message


class FakeRole:
    def __init__(self, name, position=1):
        self.name = name
        self.mention = f"@{name}"
        self.position = position

    def __lt__(self, other):
        return self.position < getattr(other, "position", 0)


class FakeMember:
    def __init__(
        self,
        member_id,
        guild=None,
        joined_at=None,
        *,
        roles=None,
        bot=False,
        display_name=None,
    ):
        self.id = member_id
        self.guild = guild
        self.joined_at = joined_at or datetime.now(timezone.utc)
        self.bot = bot
        self.mention = f"<@{member_id}>"
        self.display_name = display_name or f"Member {member_id}"
        self.roles = roles if roles is not None else [FakeRole(up.VALID_MEMBER_ROLE_NAME)]
        self.guild_permissions = SimpleNamespace(administrator=False, manage_roles=False)
        self.top_role = FakeRole("Top", position=100)
        self.display_avatar = SimpleNamespace(url="https://example.invalid/avatar.png")
        self.added_roles = []

    async def add_roles(self, role, *, reason=None):
        self.roles.append(role)
        self.added_roles.append({"role": role, "reason": reason})

    def __str__(self):
        return self.display_name


class FakeGuild:
    def __init__(self, members=None, text_channels=None, roles=None, me=None):
        self.id = 42
        self.members = members or []
        self.roles = roles or [
            FakeRole(up.STAFF_ROLE_NAME, position=10),
            FakeRole(up.VETERAN_ROLE_NAME, position=20),
        ]
        self.text_channels = text_channels or []
        self.me = me
        for member in self.members:
            member.guild = self

    def get_channel(self, channel_id):
        return next((channel for channel in self.text_channels if channel.id == channel_id), None)

    def get_member(self, member_id):
        return next((member for member in self.members if member.id == member_id), None)

    async def fetch_member(self, member_id):
        member = self.get_member(member_id)
        if member is None:
            raise up.discord.NotFound(response=None, message="missing")
        return member


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeContext:
    def __init__(self, guild):
        self.guild = guild
        self.sent_messages = []

    def typing(self):
        return FakeTyping()

    async def send(self, *args, **kwargs):
        message = SimpleNamespace(id=len(self.sent_messages) + 2000, edit=AsyncMock())
        self.sent_messages.append({"args": args, "kwargs": kwargs, "message": message})
        return message


def make_initialized_cog(bot):
    cog = UpCog(bot)
    cog.initialized = True
    cog._persist_state = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_scan_entire_history_uses_limits_and_counts_messages(monkeypatch):
    monkeypatch.setenv("UP_SCAN_DAYS", "30")
    monkeypatch.setenv("UP_SCAN_LIMIT_PER_CHANNEL", "123")
    monkeypatch.setenv("UP_SCAN_DELAY_SECONDS", "0")

    author = SimpleNamespace(id=777, bot=False)
    bot_author = SimpleNamespace(id=888, bot=True)
    channel = FakeChannel(
        messages=[
            SimpleNamespace(author=author),
            SimpleNamespace(author=bot_author),
            SimpleNamespace(author=author),
        ]
    )
    guild = SimpleNamespace(text_channels=[channel])
    bot = SimpleNamespace(guilds=[guild])
    cog = UpCog(bot)

    await cog.scan_entire_history()

    assert channel.history_calls
    _, kwargs = channel.history_calls[0]
    assert kwargs["limit"] == 123
    assert isinstance(kwargs["after"], datetime)
    assert cog.user_message_count[str(author.id)] == 2
    assert str(bot_author.id) not in cog.user_message_count


@pytest.mark.asyncio
async def test_post_ready_init_cleans_legacy_vote_state_silently():
    cog = UpCog(SimpleNamespace(guilds=[]))
    cog.promotions_data = {
        "101": {"status": "voting", "vote": {"message_id": 1}},
        "202": {"status": "promoted", "vote": {"message_id": 2}},
    }
    cog.load_promotions_data = AsyncMock()
    cog._persist_state = AsyncMock()

    await cog._post_ready_init()

    assert cog.initialized is True
    assert cog.promotions_data["101"] == {"status": "postponed"}
    assert cog.promotions_data["202"] == {"status": "promoted"}
    cog._persist_state.assert_awaited_once()


def test_automatic_vote_api_is_removed():
    cog = UpCog(SimpleNamespace(guilds=[]))

    assert not hasattr(cog, "_run_monthly_promotion_campaign")
    assert not hasattr(cog, "lancer_vote")
    assert not hasattr(cog, "_finalize_vote")
    assert not hasattr(cog, "check_up_status")
    assert not hasattr(up, "UP_PROMOTION_AUTO_VOTES")


@pytest.mark.asyncio
async def test_verifier_membres_eligibles_returns_candidates_without_sending_messages(monkeypatch):
    now_utc = datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(up.discord.utils, "utcnow", lambda: now_utc)

    announcement_channel = FakeChannel(name=up.ANNONCE_CHANNEL_NAME)
    guild = FakeGuild(text_channels=[announcement_channel])
    member = FakeMember(404, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 1))
    guild.members = [member]
    bot = SimpleNamespace(guilds=[guild])
    cog = make_initialized_cog(bot)
    cog.user_message_count[str(member.id)] = up.MESSAGE_THRESHOLD

    candidates = await cog.verifier_membres_eligibles()

    assert [candidate["member"].id for candidate in candidates] == [member.id]
    assert announcement_channel.sent_messages == []
    cog._persist_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_veteran_command_lists_candidates_with_promotion_buttons(monkeypatch):
    now_utc = datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(up.discord.utils, "utcnow", lambda: now_utc)

    guild = FakeGuild()
    member = FakeMember(505, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 5))
    guild.members = [member]
    bot = SimpleNamespace(guilds=[guild])
    cog = make_initialized_cog(bot)

    async def scan_history(guild_arg):
        assert guild_arg is guild
        cog.user_message_count[str(member.id)] = up.MESSAGE_THRESHOLD + 7

    cog.scan_entire_history = AsyncMock(side_effect=scan_history)
    ctx = FakeContext(guild)

    await cog.veteran_command.callback(cog, ctx)

    assert len(ctx.sent_messages) == 1
    sent = ctx.sent_messages[0]
    embed = sent["kwargs"]["embed"]
    view = sent["kwargs"]["view"]
    assert embed.title == "Candidats Vétéran"
    assert member.mention in embed.description
    assert "27 message(s)" in embed.description
    assert len(view.children) == 1
    assert isinstance(view.children[0], VeteranPromotionButton)
    assert view.children[0].label == "Promouvoir 01 · Member 505"
    assert view.message is sent["message"]
    cog._persist_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_promouvoir_veteran_assigns_role_persists_and_announces(monkeypatch):
    now_utc = datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(up.discord.utils, "utcnow", lambda: now_utc)

    announcement_channel = FakeChannel(name=up.ANNONCE_CHANNEL_NAME)
    veteran_role = FakeRole(up.VETERAN_ROLE_NAME, position=20)
    guild = FakeGuild(text_channels=[announcement_channel], roles=[veteran_role])
    member = FakeMember(606, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 5))
    promoted_by = FakeMember(
        707,
        guild,
        now_utc,
        roles=[FakeRole(up.STAFF_ROLE_NAME)],
        display_name="Staff 707",
    )
    guild.members = [member, promoted_by]
    bot = SimpleNamespace(guilds=[guild])
    cog = make_initialized_cog(bot)

    outcome = await cog.promouvoir_veteran(
        member,
        promoted_by,
        candidate_snapshot={
            "message_count": up.MESSAGE_THRESHOLD + 3,
            "join_days": up.JOINED_THRESHOLD_DAYS + 5,
        },
    )

    assert isinstance(outcome, PromotionOutcome)
    assert outcome.ok is True
    assert outcome.disable_button is True
    assert outcome.announcement_sent is True
    assert veteran_role in member.roles
    assert member.added_roles[0]["reason"] == "Promotion Vétéran via !veteran par Staff 707 (707)"
    assert cog.promotions_data[str(member.id)]["status"] == "promoted"
    assert cog.promotions_data[str(member.id)]["promoted_by"] == promoted_by.id
    assert cog.promotions_data[str(member.id)]["message_count_at_promotion"] == up.MESSAGE_THRESHOLD + 3
    assert len(announcement_channel.sent_messages) == 1
    assert announcement_channel.sent_messages[0]["kwargs"]["embed"].title == "🏅 Nouveau Vétéran"
    cog._persist_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_promouvoir_veteran_refuses_when_announcement_channel_is_missing(monkeypatch):
    now_utc = datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(up.discord.utils, "utcnow", lambda: now_utc)

    veteran_role = FakeRole(up.VETERAN_ROLE_NAME, position=20)
    guild = FakeGuild(roles=[veteran_role])
    member = FakeMember(808, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 5))
    promoted_by = FakeMember(909, guild, now_utc, roles=[FakeRole(up.STAFF_ROLE_NAME)])
    bot = SimpleNamespace(guilds=[guild])
    cog = make_initialized_cog(bot)

    outcome = await cog.promouvoir_veteran(
        member,
        promoted_by,
        candidate_snapshot={
            "message_count": up.MESSAGE_THRESHOLD,
            "join_days": up.JOINED_THRESHOLD_DAYS,
        },
    )

    assert outcome.ok is False
    assert "Canal d'annonces introuvable" in outcome.message
    assert veteran_role not in member.roles
    cog._persist_state.assert_not_awaited()
