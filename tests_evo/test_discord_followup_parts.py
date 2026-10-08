"""Les morceaux d'une réponse partagent le suivi, sans élargir le contexte Discord."""
from itertools import count
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, Mock

import discord
import pytest

from evo import EvoCog, MAX_REPLY_PARTS
from tests_evo.helpers import config, context
from utils.evo_agent import Sessions


@pytest.fixture
def followups(monkeypatch):
    monkeypatch.setenv("EVO_ENABLED", "1")
    ctx = context(config(cooldown=0, channel_ids=frozenset({10, 30})))
    for index in (0, 2):
        permissions = ctx.guild.text_channels[index]
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name, channel.guild = permissions.id, permissions.name, ctx.guild
        channel.permissions_for.side_effect = permissions.permissions_for
        ctx.guild.text_channels[index] = channel
    ctx.channel = ctx.guild.get_channel(10)
    ctx.bot.user = ctx.guild.me
    ctx.bot.ensure_evo_leadership = AsyncMock()
    sessions = Sessions(ctx.config)
    state = NS(ctx=ctx, text="Conseil détaillé pour la prochaine sortie. " * 150,
               published=[], sessions=sessions)
    trigger_ids, reply_ids = count(100), count(10000)

    async def answer(current, question, trigger_id, **kwargs):
        sessions.save((current.guild.id, current.channel.id, current.member.id),
                      question, state.text, [], current.sources)
        return state.text

    async def publish(content=None, **kwargs):
        result = NS(id=next(reply_ids), content=content)
        state.published.append(result)
        return result

    agent = NS(answer=AsyncMock(side_effect=answer), sessions=sessions,
               model=NS(close=AsyncMock()))
    cog = EvoCog(ctx.bot)
    cog.config, cog.agent = ctx.config, agent
    cog.budget = NS(check_ready=AsyncMock(), invalidate=Mock(), close=AsyncMock())

    def interaction(*, member_id=2, channel_id=10):
        return NS(id=next(trigger_ids), guild=ctx.guild, guild_id=ctx.guild.id,
                  channel=ctx.guild.get_channel(channel_id), user=ctx.guild.get_member(member_id),
                  response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                  edit_original_response=AsyncMock(side_effect=publish),
                  followup=NS(send=AsyncMock(side_effect=publish)))

    def message(reference_id=None, *, member_id=2, channel_id=10, content="Peux-tu préciser ?"):
        return NS(id=next(trigger_ids), guild=ctx.guild,
                  channel=ctx.guild.get_channel(channel_id), author=ctx.guild.get_member(member_id),
                  content=content, webhook_id=None,
                  reference=NS(message_id=reference_id) if reference_id is not None else None,
                  reply=AsyncMock(side_effect=publish))

    async def seed(route="slash", *, member_id=2, channel_id=10):
        before = len(state.published)
        if route == "slash":
            await cog.evo.callback(cog, interaction(member_id=member_id, channel_id=channel_id),
                                   "Que conseille-tu pour la sortie ?")
        else:
            await cog.on_message(message(member_id=member_id, channel_id=channel_id,
                                         content="<@99> Que conseilles-tu pour la sortie ?"))
        return state.published[before:]

    state.cog, state.agent = cog, agent
    state.interaction, state.message, state.seed = interaction, message, seed
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["slash", "mention"])
@pytest.mark.parametrize("part_index", [0, 1, -1])
async def test_every_part_of_the_latest_answer_accepts_its_members_followup(followups, route, part_index):
    parts = await followups.seed(route)
    assert len(parts) >= 3
    followups.text = "Voici la précision demandée."
    message = followups.message(parts[part_index].id)

    await followups.cog.on_message(message)

    assert followups.agent.answer.await_count == 2
    assert followups.agent.answer.await_args.args[1] == message.content
    message.reply.assert_awaited_once()
    key = (1, 10, 2)
    assert followups.sessions.items[key].last_message_id == followups.published[-1].id


@pytest.mark.asyncio
async def test_new_answer_invalidates_every_part_of_the_previous_turn(followups):
    previous = await followups.seed()
    current = await followups.seed()

    for part in previous:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 2

    await followups.cog.on_message(followups.message(current[0].id))
    assert followups.agent.answer.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["member", "channel", "guild"])
async def test_split_answer_references_cannot_cross_contexts(followups, scope):
    parts = await followups.seed()
    for part in parts:
        message = followups.message(part.id, member_id=3 if scope == "member" else 2,
                                   channel_id=30 if scope == "channel" else 10)
        if scope == "guild":
            message.guild = NS(id=2)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 1


@pytest.mark.asyncio
async def test_every_part_expires_at_the_session_deadline(followups, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("evo.time", NS(monotonic=lambda: now[0]))
    parts = await followups.seed()
    now[0] += followups.ctx.config.session_seconds

    for part in parts:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 1
    assert not followups.cog._last_messages


@pytest.mark.asyncio
async def test_session_limit_evicts_all_parts_together(followups):
    followups.cog.config = config(cooldown=0, max_sessions=1)
    previous = await followups.seed(member_id=2)
    current = await followups.seed(member_id=3)
    assert len(followups.cog._last_messages) == 1

    for part in previous:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 2

    await followups.cog.on_message(followups.message(current[0].id, member_id=3))
    assert followups.agent.answer.await_count == 3


@pytest.mark.asyncio
async def test_unusually_fragmented_answers_keep_a_bounded_number_of_references(followups, monkeypatch):
    monkeypatch.setattr("evo.split_messages", lambda text: ["Petit morceau"] * (MAX_REPLY_PARTS + 5))
    parts = await followups.seed()
    assert len(followups.cog._last_messages[(1, 10, 2)][0]) == MAX_REPLY_PARTS

    for part in parts[:-MAX_REPLY_PARTS]:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 1

    await followups.cog.on_message(followups.message(parts[-MAX_REPLY_PARTS].id))
    assert followups.agent.answer.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_id", [9101, 9102])
async def test_partial_publication_keeps_only_successfully_sent_answer_parts(followups, reference_id):
    previous = await followups.seed()
    send = AsyncMock(side_effect=[NS(id=9101), NS(id=9102),
                                 RuntimeError("Discord unavailable"), NS(id=9104)])
    await followups.cog._run(followups.ctx, "Une autre réponse détaillée ?", 9000, send)
    assert send.await_count == 4

    for part_id in [*(part.id for part in previous), 9104]:
        message = followups.message(part_id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 2

    followups.text = "Voici la suite."
    await followups.cog.on_message(followups.message(reference_id))
    assert followups.agent.answer.await_count == 3


@pytest.mark.asyncio
async def test_missing_message_result_does_not_append_references_from_the_old_turn(followups):
    previous = await followups.seed()
    send = AsyncMock(side_effect=[None, NS(id=9202), NS(id=9203), NS(id=9204)])
    await followups.cog._run(followups.ctx, "Une autre réponse détaillée ?", 9200, send)

    for part in previous:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 2

    await followups.cog.on_message(followups.message(9202))
    assert followups.agent.answer.await_count == 3


@pytest.mark.asyncio
async def test_forget_removes_all_members_parts_in_all_channels_only(followups):
    parts = [*(await followups.seed()), *(await followups.seed(channel_id=30))]
    other = await followups.seed(member_id=3)

    await followups.cog.forget.callback(followups.cog, followups.interaction())

    for part in parts:
        for channel_id in (10, 30):
            message = followups.message(part.id, channel_id=channel_id)
            await followups.cog.on_message(message)
            message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 3

    await followups.cog.on_message(followups.message(other[0].id, member_id=3))
    assert followups.agent.answer.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["suspend", "cog_unload"])
async def test_suspension_and_unload_remove_all_answer_parts(followups, operation):
    parts = await followups.seed()

    await getattr(followups.cog, operation)()

    assert not followups.cog._last_messages
    assert not followups.sessions.items
    for part in parts:
        message = followups.message(part.id)
        await followups.cog.on_message(message)
        message.reply.assert_not_awaited()
    assert followups.agent.answer.await_count == 1
