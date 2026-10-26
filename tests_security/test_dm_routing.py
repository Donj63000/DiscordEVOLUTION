"""Private-input regressions using discord.py's real waiter dispatcher."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from utils.dm_sessions import (
    DMCoordinator, DMFlowBusy, cancel_dm_flows, dm_check_for, dm_workflow,
    finish_dm_input, freeze_dm_message, get_dm_coordinator, passive_accepts,
    unowned_dm_message,
)


class LocalDM(discord.DMChannel):
    def __init__(self, channel_id=50):
        self.id = channel_id
        self.send = AsyncMock()


def incoming(dm, user, content="Texte synthétique privé", *, message_id=None):
    return SimpleNamespace(
        id=message_id or discord.utils.time_snowflake(datetime.now(timezone.utc), high=True),
        guild=None, channel=dm, author=user, content=content, attachments=[],
    )


class RoutingClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.none())
        self.allowed_guild_id = 1
        self.command_prefix = "!"

    def dispatch(self, event, *args, **kwargs):
        if event == "message":
            freeze_dm_message(self, args[0])
        return super().dispatch(event, *args, **kwargs)


def context(bot, dm, user_id=10):
    user = SimpleNamespace(id=user_id, bot=False, dm_channel=dm, create_dm=AsyncMock(return_value=dm),
                           send=dm.send, display_name="Synthetic member", mention=f"<@{user_id}>")
    guild = SimpleNamespace(id=1, get_member=lambda uid: user if uid == user_id else None)
    bot.get_guild = lambda gid: guild if gid == guild.id else None
    return SimpleNamespace(bot=bot, guild=guild, author=user, send=AsyncMock(),
                           message=SimpleNamespace(delete=AsyncMock()), interaction=None)


@pytest.mark.asyncio
async def test_real_waiter_dispatch_does_not_reassign_final_message_to_ai():
    bot = RoutingClient()
    await bot._async_setup_hook()
    dm = LocalDM()
    ctx = context(bot, dm)
    collected = []
    started = asyncio.Event()

    class Flow:
        def __init__(self):
            self.bot = bot

        @dm_workflow("ticket", timeout=30)
        async def run(self, ctx):
            check = dm_check_for(bot)
            started.set()
            message = await bot.wait_for("message", check=check, timeout=1)
            collected.append(message.content)
            finish_dm_input(bot)

    task = asyncio.create_task(Flow().run(ctx))
    await started.wait()
    message = incoming(dm, ctx.author)
    bot.dispatch("message", message)
    await task
    coordinator = get_dm_coordinator(bot)
    ai = coordinator.acquire(ctx.author.id, 1, "ia", passive=True)
    coordinator.bind(ai, dm)
    assert collected == [message.content]
    assert not passive_accepts(bot, message, "ia")
    assert coordinator.routes[message.id].token != ai.token
    await bot.close()


@pytest.mark.asyncio
async def test_two_structured_workflows_cannot_collect_same_user():
    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    started = asyncio.Event()
    release = asyncio.Event()
    entered = []

    class Flow:
        def __init__(self):
            self.bot = bot

        @dm_workflow("avis", timeout=30)
        async def run(self, ctx):
            entered.append(ctx.author.id)
            started.set()
            await release.wait()

    flow = Flow()
    task = asyncio.create_task(flow.run(ctx))
    await started.wait()
    await flow.run(ctx)
    assert entered == [ctx.author.id]
    ctx.send.assert_awaited_once()
    release.set()
    await task
    assert get_dm_coordinator(bot).current(ctx.author.id) is None
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
async def test_failed_or_cancelled_callback_releases_only_its_lease(failure):
    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())

    class Flow:
        def __init__(self):
            self.bot = bot

        @dm_workflow("profil")
        async def run(self, ctx):
            raise failure()

    with pytest.raises(failure):
        await Flow().run(ctx)
    assert get_dm_coordinator(bot).current(ctx.author.id) is None
    await bot.close()


@pytest.mark.asyncio
async def test_dm_open_failure_does_not_leave_a_lease():
    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    ctx.author.dm_channel = None
    ctx.author.create_dm.side_effect = RuntimeError("synthetic transport failure")

    class Flow:
        def __init__(self):
            self.bot = bot

        @dm_workflow("ticket")
        async def run(self, ctx):
            pytest.fail("callback must not execute")

    with pytest.raises(RuntimeError):
        await Flow().run(ctx)
    assert get_dm_coordinator(bot).current(ctx.author.id) is None
    await bot.close()


def test_route_requires_exact_channel_new_message_and_configured_prefix():
    coordinator = DMCoordinator(prefixes=("?",))
    lease = coordinator.acquire(10, 1, "avis")
    dm = LocalDM()
    coordinator.bind(lease, dm)
    user = SimpleNamespace(id=10, bot=False)
    old = incoming(dm, user, message_id=lease.since_id - 1)
    command = incoming(dm, user, "?bot explicit command", message_id=lease.since_id + 1)
    foreign = incoming(LocalDM(51), user, message_id=lease.since_id + 2)
    for message in (old, command, foreign):
        coordinator.freeze(message)
        assert not coordinator.accepts(message, lease)
    valid = incoming(dm, user, "Actual form answer", message_id=lease.since_id + 3)
    coordinator.freeze(valid)
    assert coordinator.accepts(valid, lease)


def test_old_release_cannot_release_new_generation_and_expiry_is_enforced():
    clock = [0.0]
    coordinator = DMCoordinator(clock=lambda: clock[0])
    old = coordinator.acquire(10, 1, "ticket", timeout=1)
    clock[0] = 2
    current = coordinator.acquire(10, 1, "avis")
    coordinator.release(old)
    assert coordinator.current(10) is current
    with pytest.raises(DMFlowBusy):
        coordinator.acquire(10, 1, "profil")


def test_foreground_suspends_passive_without_automatic_resume():
    coordinator = DMCoordinator()
    old = coordinator.acquire(10, 1, "welcome", passive=True)
    foreground = coordinator.acquire(10, 1, "ticket")
    coordinator.release(foreground)
    assert coordinator.current(10) is None
    coordinator.release(old)
    assert coordinator.current(10) is None


def test_routes_are_bounded_and_independent_users_have_distinct_owners():
    coordinator = DMCoordinator(max_routes=2)
    dm = LocalDM()
    first = coordinator.acquire(10, 1, "avis")
    second = coordinator.acquire(11, 1, "ticket")
    coordinator.bind(first, dm)
    coordinator.bind(second, LocalDM(51))
    for offset in range(1, 4):
        message = incoming(dm, SimpleNamespace(id=10, bot=False), message_id=first.since_id + offset)
        coordinator.freeze(message)
    assert len(coordinator.routes) == 2
    assert coordinator.current(11) is second


@pytest.mark.asyncio
async def test_leaving_the_source_guild_revokes_passive_input():
    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(ctx.author.id, 1, "ia", passive=True)
    coordinator.bind(lease, ctx.author.dm_channel)
    message = incoming(ctx.author.dm_channel, ctx.author)
    freeze_dm_message(bot, message)
    assert passive_accepts(bot, message, "ia")
    bot.get_guild = lambda gid: None
    assert not passive_accepts(bot, message, "ia")
    assert coordinator.current(ctx.author.id) is None
    await bot.close()


@pytest.mark.asyncio
async def test_custom_prefix_restart_is_not_unowned_onboarding_input():
    bot = RoutingClient()
    await bot._async_setup_hook()
    bot.command_prefix = "?"
    ctx = context(bot, LocalDM())
    message = incoming(ctx.author.dm_channel, ctx.author, "?recommencer")
    freeze_dm_message(bot, message)
    assert not unowned_dm_message(bot, message)
    await bot.close()


@pytest.mark.asyncio
async def test_unloading_owner_cancels_its_collector():
    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    started = asyncio.Event()

    class Flow:
        def __init__(self):
            self.bot = bot

        @dm_workflow("event")
        async def run(self, ctx):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(Flow().run(ctx))
    await started.wait()
    cancel_dm_flows(bot, "event")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_dm_coordinator(bot).current(ctx.author.id) is None
    await bot.close()


@pytest.mark.asyncio
async def test_real_ticket_cancels_without_collecting_an_anonymous_form():
    from ticket import TicketCog, open_tickets
    from avis import AvisFeedback

    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    ticket = TicketCog(bot)
    feedback = AvisFeedback(bot)
    open_tickets.clear()
    task = asyncio.create_task(ticket.create_ticket.callback(ticket, ctx))
    try:
        for _ in range(100):
            if bot._listeners.get("message"):
                break
            await asyncio.sleep(0)
        assert bot._listeners.get("message")
        await feedback.avis_cmd.callback(feedback, ctx)
        assert not feedback.active_sessions
        assert ctx.send.await_count == 1
        message = incoming(ctx.author.dm_channel, ctx.author, "annuler")
        bot.dispatch("message", message)
        await task
        assert ctx.author.id not in open_tickets
        assert get_dm_coordinator(bot).current(ctx.author.id) is None
        assert not any("Avis anonyme" in str(call) for call in ctx.author.send.await_args_list)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        open_tickets.clear()
        await bot.close()


@pytest.mark.asyncio
async def test_real_ticket_timeout_releases_collection_and_open_marker(monkeypatch):
    import ticket as ticket_module

    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    monkeypatch.setattr(ticket_module, "DM_TIMEOUT_SECONDS", 0.01)
    ticket_module.open_tickets.clear()
    cog = ticket_module.TicketCog(bot)
    await cog.create_ticket.callback(cog, ctx)
    assert ctx.author.id not in ticket_module.open_tickets
    assert get_dm_coordinator(bot).current(ctx.author.id) is None
    assert any("Temps écoulé" in str(call) for call in ctx.author.send.await_args_list)
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["Texte privé", "recommencer"])
async def test_existing_ia_and_welcome_listeners_ignore_foreground_message(monkeypatch, content):
    from ia import IACog, IASession
    from welcome import WelcomeCog

    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    monkeypatch.setattr(WelcomeCog, "load_welcomed_data", lambda self: None)
    bot.get_context = AsyncMock()
    ia = IACog(bot)
    now = discord.utils.utcnow()
    ia.sessions[ctx.author.id] = IASession("synthetic", object(), now, now)
    ia._ask_gemini = AsyncMock()
    welcome = WelcomeCog(bot)
    welcome._find_member_for_user = lambda uid: SimpleNamespace(id=uid, guild=ctx.guild)
    welcome._handle_welcome_message = AsyncMock()
    welcome._start_welcome_flow = AsyncMock()
    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(ctx.author.id, 1, "avis")
    coordinator.bind(lease, ctx.author.dm_channel)
    message = incoming(ctx.author.dm_channel, ctx.author, content)
    freeze_dm_message(bot, message)
    coordinator.release(lease)
    await ia.on_message(message)
    await welcome.on_message(message)
    ia._ask_gemini.assert_not_awaited()
    welcome._handle_welcome_message.assert_not_awaited()
    welcome._start_welcome_flow.assert_not_awaited()
    bot.get_context.assert_not_awaited()
    await bot.close()


def ticket_interaction(bot, guild, actor, *, message=None):
    return SimpleNamespace(
        client=bot, guild=guild, user=actor, message=message,
        response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_two_ticket_reply_modals_cannot_share_a_staff_message():
    from ticket import TicketView

    bot = RoutingClient()
    await bot._async_setup_hook()
    staff_role = SimpleNamespace(name="Staff")
    actor = SimpleNamespace(id=30, display_name="Staff synthétique", roles=[staff_role])
    guild = SimpleNamespace(id=1, roles=[staff_role], get_member=lambda uid: actor if uid == actor.id else None)
    users = [SimpleNamespace(id=uid, send=AsyncMock()) for uid in (10, 11)]
    dialogs = []
    for user in users:
        ticket = TicketView(user, "Staff")
        ticket.taken_by = actor
        embed = discord.Embed(title="Ticket synthétique")
        message = SimpleNamespace(embeds=[embed], edit=AsyncMock())
        interaction = ticket_interaction(bot, guild, actor, message=message)
        await ticket.mark_resolved.callback(interaction)
        dialogs.append(interaction.response.send_modal.await_args.args[0])
    dialogs[0].answer._value = "Réponse réservée au premier ticket"
    await dialogs[0].on_submit(ticket_interaction(bot, guild, actor))
    users[0].send.assert_awaited_once()
    users[1].send.assert_not_awaited()
    dialogs[1].answer._value = "Réponse réservée au second ticket"
    await dialogs[1].on_submit(ticket_interaction(bot, guild, actor))
    assert "premier ticket" not in users[1].send.await_args.args[0]
    assert "second ticket" not in users[0].send.await_args.args[0]
    await bot.close()


@pytest.mark.asyncio
async def test_ticket_reply_modal_rechecks_owner_and_staff_rights():
    from ticket import TicketView, TicketResolutionModal

    bot = RoutingClient()
    await bot._async_setup_hook()
    staff_role = SimpleNamespace(name="Staff")
    actor = SimpleNamespace(id=30, display_name="Staff synthétique", roles=[staff_role])
    guild = SimpleNamespace(id=1, roles=[staff_role], get_member=lambda uid: actor if uid == actor.id else None)
    user = SimpleNamespace(id=10, send=AsyncMock())
    ticket = TicketView(user, "Staff")
    ticket.taken_by = actor
    message = SimpleNamespace(embeds=[discord.Embed(title="Ticket synthétique")], edit=AsyncMock())
    modal = TicketResolutionModal(ticket, ticket_interaction(bot, guild, actor, message=message))
    other = SimpleNamespace(id=31, roles=[staff_role])
    await modal.on_submit(ticket_interaction(bot, guild, other))
    actor.roles.clear()
    await modal.on_submit(ticket_interaction(bot, guild, actor))
    message.edit.assert_not_awaited()
    user.send.assert_not_awaited()
    assert not ticket.resolved
    await bot.close()


@pytest.mark.asyncio
async def test_private_profile_panel_requires_its_current_member_owner():
    from cogs.profil import ProfilActionsView

    bot = RoutingClient()
    await bot._async_setup_hook()
    ctx = context(bot, LocalDM())
    profile = SimpleNamespace(guild_id=1, owner_id=ctx.author.id)
    view = ProfilActionsView(SimpleNamespace(bot=bot), profile)
    interaction = ticket_interaction(bot, None, ctx.author)
    assert await view.interaction_check(interaction)
    interaction.user = SimpleNamespace(id=11)
    assert not await view.interaction_check(interaction)
    interaction.user = ctx.author
    bot.get_guild = lambda gid: None
    assert not await view.interaction_check(interaction)
    await bot.close()
