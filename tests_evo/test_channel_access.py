"""Accès aux discussions du serveur sans fuite entre salons ni appel payant après retrait."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from evo import EvoCog
from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Transport, answer, config, context, function, response
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoError
from utils.evo_tools import EvoTools


def thread_context(*, private=False, allowlist=(), parent_id=10):
    ctx = context(config(channel_ids=frozenset(allowlist), cooldown=0))
    thread = MagicMock(spec=discord.Thread)
    thread.id, thread.parent_id, thread.guild = 50, parent_id, ctx.guild
    thread.archived = False
    thread.is_private.return_value = private
    permissions = discord.Permissions(view_channel=True, send_messages_in_threads=True)
    thread.permissions_for.return_value = permissions
    thread.fetch_member = AsyncMock(side_effect=lambda identifier: NS(id=identifier))
    ctx.channel = thread
    return ctx, permissions


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [discord.TextChannel, discord.VoiceChannel, discord.StageChannel])
async def test_server_discussion_types_work_with_member_and_bot_permissions(kind):
    ctx = context(config(channel_ids=frozenset()))
    channel = MagicMock(spec=kind)
    channel.id, channel.guild = 20, ctx.guild
    channel.permissions_for.side_effect = ctx.guild.get_channel(20).permissions_for
    cog = EvoCog(ctx.bot)
    cog.config = ctx.config
    with patch.dict("os.environ", {"EVO_ENABLED": "1"}):
        selected = cog._context(ctx.guild, channel, ctx.member)
    await selected.ensure_access()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_kind", [discord.TextChannel, discord.ForumChannel])
async def test_threads_and_forum_posts_use_thread_send_permission(parent_kind):
    ctx, permissions = thread_context(allowlist={10})
    ctx.channel.parent = MagicMock(spec=parent_kind)
    assert not permissions.send_messages
    await ctx.ensure_access()
    ctx.channel.fetch_member.assert_not_awaited()
    cog = EvoCog(ctx.bot)
    cog.config = ctx.config
    with patch.dict("os.environ", {"EVO_ENABLED": "1"}):
        assert cog._context(ctx.guild, ctx.channel, ctx.member).channel is ctx.channel
    permissions.send_messages = True
    permissions.send_messages_in_threads = False
    with pytest.raises(EvoError, match="Permission"):
        await ctx.ensure_access()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowlist,allowed", [({50}, True), ({10}, True), ({30}, False)])
async def test_thread_allowlist_accepts_thread_or_parent(allowlist, allowed):
    ctx, _ = thread_context(allowlist=allowlist)
    if allowed:
        await ctx.ensure_access()
    else:
        with pytest.raises(EvoError, match="autorisé"):
            await ctx.ensure_access()


@pytest.mark.asyncio
async def test_console_threads_and_archived_threads_are_excluded():
    ctx, _ = thread_context(allowlist={50})
    ctx.guild.get_channel(10).name = "console"
    with pytest.raises(EvoError, match="#console"):
        await ctx.ensure_access()
    ctx.guild.get_channel(10).name = "general"
    ctx.channel.archived = True
    with pytest.raises(EvoError, match="Rouvre"):
        await ctx.ensure_access()


@pytest.mark.asyncio
async def test_private_thread_verifies_both_memberships_and_honors_manage_threads():
    ctx, permissions = thread_context(private=True)
    await ctx.ensure_access()
    assert {call.args[0] for call in ctx.channel.fetch_member.await_args_list} == {2, 99}
    ctx.channel.fetch_member.reset_mock()
    permissions.manage_threads = True
    await ctx.ensure_access()
    ctx.channel.fetch_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [2, 99])
async def test_inaccessible_private_thread_never_reaches_model(missing):
    ctx, _ = thread_context(private=True)

    async def membership(identifier):
        if identifier == missing:
            raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Member")
        return NS(id=identifier)

    ctx.channel.fetch_member.side_effect = membership
    transport = Transport([answer("Salut !")])
    budget = await create_budget(ctx.config)
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        with pytest.raises(EvoError, match="fil privé"):
            await agent.answer(ctx, "Salut", 700)
        assert not transport.count_calls
        assert not transport.calls
    finally:
        await budget.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("private_thread", [False, True])
async def test_access_revoked_during_reservation_prevents_paid_generation(private_thread):
    ctx, _ = thread_context(private=True) if private_thread else (context(), None)
    budget = await create_budget(ctx.config)
    reserve = budget.reserve

    async def revoke_after_save(*args, **kwargs):
        identifier = await reserve(*args, **kwargs)
        if private_thread:
            ctx.channel.fetch_member.side_effect = discord.NotFound(
                NS(status=404, reason="Not Found"), "Unknown Member",
            )
        else:
            ctx.channel.denied.add(ctx.member.id)
        return identifier

    budget.reserve = AsyncMock(side_effect=revoke_after_save)
    transport = Transport([answer("Salut !")])
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        with pytest.raises(EvoError):
            await agent.answer(ctx, "Salut", 701)
        assert len(transport.count_calls) == 1
        budget.reserve.assert_awaited_once()
        assert not transport.calls
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_membership_revoked_during_model_response_prevents_tool_execution():
    ctx, _ = thread_context(private=True)
    budget = await create_budget(ctx.config)
    transport = Transport([response([function("guilde", {})])])
    create = transport.create

    async def revoke_after_response(payload):
        result = await create(payload)
        ctx.channel.fetch_member.side_effect = TimeoutError()
        return result

    transport.create = AsyncMock(side_effect=revoke_after_response)
    tools = EvoTools()
    tools.execute = AsyncMock()
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport), tools)
        with pytest.raises(EvoError, match="fil privé"):
            await agent.answer(ctx, "Parle de la guilde", 702)
        tools.execute.assert_not_awaited()
        assert len(transport.calls) == 1
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_private_thread_access_rechecked_before_publication():
    ctx, _ = thread_context(private=True)
    ctx.bot.ensure_evo_leadership = AsyncMock()

    async def revoke_on_answer(*args, **kwargs):
        ctx.channel.fetch_member.side_effect = TimeoutError()
        return "Un détail Staff à garder dans ce fil."

    cog = EvoCog(ctx.bot)
    cog.config = ctx.config
    cog.agent = NS(answer=AsyncMock(side_effect=revoke_on_answer))
    cog.budget = NS(check_ready=AsyncMock())
    send = AsyncMock(return_value=NS(id=900))
    await cog._run(ctx, "Parle de la guilde", 703, send)
    send.assert_awaited_once()
    assert "fil privé" in send.await_args.args[0]
    assert "détail Staff" not in send.await_args.args[0]
    assert not cog._last_messages


def test_other_private_threads_never_supply_conversation_data():
    ctx, _ = thread_context(private=True)
    current = ctx.channel
    ctx.channel = ctx.guild.get_channel(10)
    assert current.permissions_for(ctx.guild.default_role).view_channel
    assert not ctx.readable_here(current)
    ctx.channel = current
    assert ctx.readable_here(current)
