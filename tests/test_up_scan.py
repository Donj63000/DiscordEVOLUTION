from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import up
from up import UpCog


class FakeHistory:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeChannel:
    def __init__(self):
        self.history_calls = []

    def history(self, *args, **kwargs):
        self.history_calls.append((args, kwargs))
        return FakeHistory()


class FakeRole:
    def __init__(self, name):
        self.name = name
        self.mention = f"@{name}"


class FakeVoteMessage:
    def __init__(self, message_id, reactions=None):
        self.id = message_id
        self.reactions = reactions or []
        self.deleted = False

    async def add_reaction(self, emoji):
        self.reactions.append(SimpleNamespace(emoji=emoji, count=1))

    async def delete(self):
        self.deleted = True


class FakeStaffChannel:
    def __init__(self, channel_id=555):
        self.id = channel_id
        self.sent_messages = []
        self.messages = {}

    async def send(self, *args, **kwargs):
        message = FakeVoteMessage(len(self.sent_messages) + 1000)
        self.sent_messages.append({"args": args, "kwargs": kwargs, "message": message})
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        return self.messages[message_id]


class FakeMember:
    def __init__(self, member_id, guild, joined_at):
        self.id = member_id
        self.guild = guild
        self.joined_at = joined_at
        self.bot = False
        self.mention = f"<@{member_id}>"
        self.display_name = f"Member {member_id}"
        self.roles = [FakeRole(up.VALID_MEMBER_ROLE_NAME)]


class FakeGuild:
    def __init__(self, members=None, staff_channel=None):
        self.id = 42
        self.members = members or []
        self.roles = [FakeRole(up.STAFF_ROLE_NAME), FakeRole(up.VETERAN_ROLE_NAME)]
        self.text_channels = []
        self.staff_channel = staff_channel
        for member in self.members:
            member.guild = self

    def get_channel(self, channel_id):
        if self.staff_channel and self.staff_channel.id == channel_id:
            return self.staff_channel
        return None

    def get_member(self, member_id):
        return next((member for member in self.members if member.id == member_id), None)


def make_initialized_cog(bot):
    cog = UpCog(bot)
    cog.initialized = True
    cog._persist_state = AsyncMock()
    return cog


def campaign_dt(day):
    return datetime(2026, 5, day, up.PROMOTION_CAMPAIGN_HOUR, 0, tzinfo=up.PROMOTION_CAMPAIGN_TIMEZONE)


@pytest.mark.asyncio
async def test_scan_entire_history_uses_limits(monkeypatch):
    monkeypatch.setenv("UP_SCAN_DAYS", "30")
    monkeypatch.setenv("UP_SCAN_LIMIT_PER_CHANNEL", "123")

    channel = FakeChannel()
    guild = SimpleNamespace(text_channels=[channel])
    bot = SimpleNamespace(guilds=[guild])
    cog = UpCog(bot)

    await cog.scan_entire_history()

    assert channel.history_calls
    _, kwargs = channel.history_calls[0]
    assert kwargs["limit"] == 123
    assert isinstance(kwargs["after"], datetime)


@pytest.mark.asyncio
async def test_monthly_campaign_skips_before_configured_day():
    cog = make_initialized_cog(SimpleNamespace(guilds=[]))
    cog.scan_entire_history = AsyncMock()
    cog.verifier_membres_eligibles = AsyncMock()

    ran = await cog._run_monthly_promotion_campaign(campaign_dt(14))

    assert ran is False
    cog.scan_entire_history.assert_not_awaited()
    cog.verifier_membres_eligibles.assert_not_awaited()
    assert up.PROMOTION_META_KEY not in cog.promotions_data


@pytest.mark.asyncio
async def test_monthly_campaign_runs_from_configured_day_once():
    cog = make_initialized_cog(SimpleNamespace(guilds=[]))
    cog.scan_entire_history = AsyncMock()
    cog.verifier_membres_eligibles = AsyncMock()

    ran = await cog._run_monthly_promotion_campaign(campaign_dt(15))

    assert ran is True
    cog.scan_entire_history.assert_awaited_once()
    cog.verifier_membres_eligibles.assert_awaited_once()
    assert cog.promotions_data[up.PROMOTION_META_KEY][up.LAST_CAMPAIGN_MONTH_KEY] == "2026-05"


@pytest.mark.asyncio
async def test_monthly_campaign_does_not_rerun_already_processed_month():
    cog = make_initialized_cog(SimpleNamespace(guilds=[]))
    cog.promotions_data = {up.PROMOTION_META_KEY: {up.LAST_CAMPAIGN_MONTH_KEY: "2026-05"}}
    cog.scan_entire_history = AsyncMock()
    cog.verifier_membres_eligibles = AsyncMock()

    ran = await cog._run_monthly_promotion_campaign(campaign_dt(20))

    assert ran is False
    cog.scan_entire_history.assert_not_awaited()
    cog.verifier_membres_eligibles.assert_not_awaited()


@pytest.mark.asyncio
async def test_monthly_campaign_posts_all_eligible_members(monkeypatch):
    now_local = campaign_dt(15)
    now_utc = now_local.astimezone(timezone.utc)
    monkeypatch.setattr(up.discord.utils, "utcnow", lambda: now_utc)

    staff_channel = FakeStaffChannel()
    guild = FakeGuild(staff_channel=staff_channel)
    members = [
        FakeMember(101, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 1)),
        FakeMember(202, guild, now_utc - timedelta(days=up.JOINED_THRESHOLD_DAYS + 1)),
    ]
    guild.members = members
    bot = SimpleNamespace(
        guilds=[guild],
        get_guild=lambda guild_id: guild if guild_id == guild.id else None,
    )
    cog = make_initialized_cog(bot)
    cog._schedule_vote_finalization = lambda user_id: None

    async def scan_history():
        for member in members:
            cog.user_message_count[str(member.id)] = up.MESSAGE_THRESHOLD

    cog.scan_entire_history = AsyncMock(side_effect=scan_history)
    monkeypatch.setattr(up, "resolve_text_channel", lambda *args, **kwargs: staff_channel)

    ran = await cog._run_monthly_promotion_campaign(now_local)

    assert ran is True
    assert len(staff_channel.sent_messages) == 2
    assert cog.get_promotion_status(101) == "voting"
    assert cog.get_promotion_status(202) == "voting"
    for member in members:
        vote = cog.get_vote_info(member.id)
        assert vote["ends_at_ts"] == vote["started_at_ts"] + 3600


@pytest.mark.asyncio
async def test_empty_vote_deletes_message_without_staff_followup(monkeypatch):
    monkeypatch.setattr(up.discord, "TextChannel", FakeStaffChannel)
    vote_message = FakeVoteMessage(
        9001,
        reactions=[
            SimpleNamespace(emoji=up.YES_VOTE_EMOJI, count=1),
            SimpleNamespace(emoji=up.NO_VOTE_EMOJI, count=1),
        ],
    )
    staff_channel = FakeStaffChannel(channel_id=777)
    staff_channel.messages[vote_message.id] = vote_message
    guild = FakeGuild(staff_channel=staff_channel)
    member = FakeMember(303, guild, datetime.now(timezone.utc) - timedelta(days=up.JOINED_THRESHOLD_DAYS + 1))
    guild.members = [member]
    bot = SimpleNamespace(
        guilds=[guild],
        get_guild=lambda guild_id: guild if guild_id == guild.id else None,
    )
    cog = make_initialized_cog(bot)
    cog.set_promotion_status(member.id, "voting")
    cog.set_vote_info(
        member.id,
        {
            "guild_id": guild.id,
            "staff_channel_id": staff_channel.id,
            "message_id": vote_message.id,
            "started_at_ts": 1,
            "ends_at_ts": 2,
        },
    )

    await cog._finalize_vote(member.id)

    assert vote_message.deleted is True
    assert staff_channel.sent_messages == []
    assert cog.get_promotion_status(member.id) == "postponed"
    assert cog.get_vote_info(member.id) is None
