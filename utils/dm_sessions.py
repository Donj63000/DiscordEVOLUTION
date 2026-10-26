"""Exclusive routing of private input, shared by every conversational workflow.

The owner of a message is frozen synchronously before Client.dispatch wakes any
waiter. A completed waiter cannot therefore make that same message available to
an AI or to onboarding. No private content is retained in the routing cache.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
import secrets
import os
import time

import discord


class DMFlowBusy(RuntimeError):
    pass


@dataclass
class DMLease:
    user_id: int
    guild_id: int
    kind: str
    token: str
    deadline: float
    duration: float
    since_id: int
    channel_id: int | None = None
    passive: bool = False
    task: asyncio.Task | None = None


@dataclass(frozen=True)
class DMRoute:
    user_id: int
    channel_id: int
    token: str | None


_input_lease: ContextVar[DMLease | None] = ContextVar("evolution_dm_lease", default=None)


class DMCoordinator:
    def __init__(self, *, clock=time.monotonic, max_routes: int = 8192, prefixes=("!",)):
        self.prefixes = tuple(prefix for prefix in prefixes if isinstance(prefix, str) and prefix) + ("/",)
        self.clock = clock
        self.max_routes = max_routes
        self.leases: dict[int, DMLease] = {}
        self.routes: OrderedDict[int, DMRoute] = OrderedDict()

    def current(self, user_id: int) -> DMLease | None:
        lease = self.leases.get(user_id)
        if lease and lease.deadline <= self.clock():
            self.release(lease)
            return None
        return lease

    def acquire(self, user_id: int, guild_id: int, kind: str, *, timeout: float = 900,
                passive: bool = False) -> DMLease:
        current = self.current(user_id)
        if current and not current.passive:
            raise DMFlowBusy("Une conversation privée est déjà en cours. Termine-la ou annule-la avant de recommencer.")
        if current:
            self.release(current)
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        lease = DMLease(
            user_id=int(user_id), guild_id=int(guild_id), kind=kind,
            token=secrets.token_hex(16), deadline=self.clock() + timeout,
            duration=timeout, since_id=discord.utils.time_snowflake(datetime.now(timezone.utc)),
            passive=passive, task=task,
        )
        self.leases[lease.user_id] = lease
        return lease

    def bind(self, lease: DMLease, channel) -> None:
        if self.current(lease.user_id) is not lease:
            raise DMFlowBusy("Cette conversation a expiré. Relance sa commande.")
        channel_id = int(channel.id)
        if lease.channel_id is not None and lease.channel_id != channel_id:
            raise DMFlowBusy("Le canal privé ne correspond pas à cette conversation.")
        lease.channel_id = channel_id

    def release(self, lease: DMLease | None) -> None:
        if lease and self.leases.get(lease.user_id) is lease:
            del self.leases[lease.user_id]

    def freeze(self, message) -> None:
        if getattr(message, "guild", None) is not None:
            return
        if not isinstance(getattr(message, "channel", None), discord.DMChannel):
            return
        mid = int(message.id)
        if mid in self.routes:
            return
        user_id = int(message.author.id)
        channel_id = int(message.channel.id)
        lease = self.current(user_id)
        content = (getattr(message, "content", "") or "").lstrip()
        token = None
        if (lease and lease.channel_id == channel_id and mid > lease.since_id
                and not getattr(message.author, "bot", False)
                and not content.startswith(self.prefixes)):
            token = lease.token
        self.routes[mid] = DMRoute(user_id, channel_id, token)
        while len(self.routes) > self.max_routes:
            self.routes.popitem(last=False)

    def accepts(self, message, lease: DMLease | None, *, refresh: bool = True) -> bool:
        if lease is None or self.current(lease.user_id) is not lease:
            return False
        route = self.routes.get(getattr(message, "id", None))
        accepted = bool(route and route.token == lease.token
                        and route.user_id == lease.user_id
                        and route.channel_id == lease.channel_id)
        if accepted and refresh:
            lease.deadline = self.clock() + lease.duration
        return accepted

    def cancel_kind(self, kind: str) -> None:
        for lease in list(self.leases.values()):
            if lease.kind == kind:
                self.release(lease)
                if not lease.passive and lease.task and not lease.task.done():
                    lease.task.cancel()


def get_dm_coordinator(bot) -> DMCoordinator:
    coordinator = getattr(bot, "_dm_coordinator", None)
    if not isinstance(coordinator, DMCoordinator):
        prefix = getattr(bot, "command_prefix", None)
        prefixes = (prefix,) if isinstance(prefix, str) else prefix if isinstance(prefix, (list, tuple)) else (os.getenv("BOT_PREFIX", "!"),)
        coordinator = DMCoordinator(prefixes=prefixes)
        bot._dm_coordinator = coordinator
    return coordinator


def freeze_dm_message(bot, message) -> None:
    get_dm_coordinator(bot).freeze(message)


def dm_check_for(bot):
    """Capture the lease here; waiter predicates run in the dispatch context."""
    coordinator = get_dm_coordinator(bot)
    lease = _input_lease.get()
    def check(message):
        if not _member_in_scope(bot, lease):
            coordinator.release(lease)
            return False
        return coordinator.accepts(message, lease)
    return check


def finish_dm_input(bot) -> None:
    get_dm_coordinator(bot).release(_input_lease.get())


def cancel_dm_flows(bot, kind: str) -> None:
    get_dm_coordinator(bot).cancel_kind(kind)


def source_guild_id(ctx):
    from utils.guild_scope import configured_guild_id, require_guild
    gid = require_guild(ctx.guild, ctx.bot) if ctx.guild is not None else configured_guild_id(ctx.bot)
    guild = ctx.guild if ctx.guild is not None else ctx.bot.get_guild(gid)
    if guild is None or guild.get_member(ctx.author.id) is None:
        raise DMFlowBusy("Cette conversation est réservée aux membres du serveur Evolution.")
    return gid


def dm_workflow(kind: str, *, timeout: float = 900):
    """Reserve input before the callback's first await; release on every exit."""
    def decorate(callback):
        @wraps(callback)
        async def run(self, ctx, *args, **kwargs):
            coordinator = get_dm_coordinator(self.bot)
            try:
                gid = source_guild_id(ctx)
                lease = coordinator.acquire(ctx.author.id, gid, kind, timeout=timeout)
            except DMFlowBusy as exc:
                await ctx.send(str(exc))
                return None
            marker = _input_lease.set(lease)
            try:
                dm = getattr(ctx.author, "dm_channel", None) or await ctx.author.create_dm()
                coordinator.bind(lease, dm)
                return await callback(self, ctx, *args, **kwargs)
            except discord.HTTPException:
                await ctx.send("Impossible d'ouvrir ou de poursuivre les messages privés. Vérifie tes réglages puis réessaie.")
                return None
            finally:
                coordinator.release(lease)
                _input_lease.reset(marker)
        return run
    return decorate


def passive_lease(bot, user_id: int, guild_id: int, kind: str, channel, *, timeout: float = 900):
    coordinator = get_dm_coordinator(bot)
    lease = coordinator.acquire(user_id, guild_id, kind, timeout=timeout, passive=True)
    coordinator.bind(lease, channel)
    return lease


def _member_in_scope(bot, lease: DMLease | None) -> bool:
    if lease is None:
        return False
    from utils.guild_scope import allowed_guilds, is_allowed_guild
    if not is_allowed_guild(lease.guild_id, bot):
        return False
    get_guild = getattr(bot, "get_guild", None)
    if callable(get_guild):
        guild = get_guild(lease.guild_id)
    else:
        guild = next((g for g in allowed_guilds(bot) if g.id == lease.guild_id), None)
    return guild is not None and guild.get_member(lease.user_id) is not None


def unowned_dm_message(bot, message) -> bool:
    coordinator = get_dm_coordinator(bot)
    route = coordinator.routes.get(getattr(message, "id", None))
    return bool(route and route.token is None
                and not (getattr(message, "content", "") or "").lstrip().startswith(coordinator.prefixes)
                and route.user_id == message.author.id
                and route.channel_id == message.channel.id)


def passive_accepts(bot, message, kind: str) -> bool:
    coordinator = get_dm_coordinator(bot)
    lease = coordinator.current(message.author.id)
    if lease and not _member_in_scope(bot, lease):
        coordinator.release(lease)
        return False
    return bool(lease and lease.kind == kind and coordinator.accepts(message, lease))
