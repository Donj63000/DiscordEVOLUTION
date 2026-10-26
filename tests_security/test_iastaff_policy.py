"""Offline security regressions using real discord.py commands and application code."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
import pytest_asyncio
from discord.ext import commands

import iastaff
from iastaff import IAStaff
from utils.checked_commands import invoke_checked


@pytest.mark.parametrize("configured,expected", [("3600", 300), ("30", 30)])
def test_confirmation_lifetime_configuration_cannot_exceed_five_minutes(configured, expected):
    result = subprocess.run(
        [sys.executable, "-c", "import iastaff; print(iastaff.IASTAFF_CONFIRM_TTL)"],
        env={**os.environ, "IASTAFF_CONFIRM_TTL": configured},
        capture_output=True, text=True, check=True, timeout=15,
    )
    assert int(result.stdout.strip()) == expected


class Role:
    def __init__(self, guild, role_id=5, name="Membre", position=2):
        self.guild, self.id, self.name, self.position = guild, role_id, name, position
        self.permissions = discord.Permissions.none()
        self.managed = False
        self.default = False

    def is_default(self):
        return self.default


def member(guild, member_id, name, position=1, *, staff=False, manage_roles=False):
    return SimpleNamespace(
        guild=guild, id=member_id, name=name, display_name=name, bot=False,
        roles=[SimpleNamespace(name="Staff")] if staff else [],
        top_role=SimpleNamespace(position=position),
        guild_permissions=discord.Permissions(manage_roles=manage_roles),
        add_roles=AsyncMock(), remove_roles=AsyncMock(),
    )


@pytest_asyncio.fixture
async def security(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1000")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    monkeypatch.setenv("IASTAFF_ALLOWED_ROLE_IDS", "5")
    monkeypatch.setattr(IAStaff, "_ensure_client", lambda self: False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.allowed_guild_id = 1000
    guild = SimpleNamespace(id=1000, owner_id=99)
    actor = member(guild, 10, "Operator", 10, staff=True, manage_roles=True)
    target = member(guild, 20, "Player")
    guild.me = member(guild, 30, "Bot", 20, manage_roles=True)
    guild.members = [actor, target, guild.me]
    role = Role(guild)
    guild.roles = [role]
    guild.fetch_roles = AsyncMock(side_effect=lambda: list(guild.roles))
    guild.get_member = lambda uid: next((item for item in guild.members if item.id == uid), None)
    guild.get_role = lambda rid: next((item for item in guild.roles if item.id == rid), None)
    guild.fetch_member = AsyncMock(side_effect=lambda uid: guild.get_member(uid))
    channel = SimpleNamespace(id=40, guild=guild)
    message = SimpleNamespace(
        id=100, author=actor, guild=guild, channel=channel,
        created_at=discord.utils.utcnow(), edited_at=None,
    )
    ctx = SimpleNamespace(
        bot=bot, guild=guild, channel=channel, author=actor, message=message,
        command=None, command_failed=False, permissions=actor.guild_permissions,
    )
    cog = IAStaff(bot)
    cog.enable_tools = True
    effects = []

    @bot.command(name="activite")
    async def activity(context, action: str = None, *, args: str | None = None):
        effects.append((action, args))

    yield SimpleNamespace(
        bot=bot, guild=guild, actor=actor, target=target, role=role,
        ctx=ctx, cog=cog, effects=effects, activity=activity,
    )
    await bot.close()


async def propose_role(security):
    response = await security.cog._dispatch_command_tool(
        security.ctx, "grant_role", {"member": "Player", "role": "Membre"}
    )
    assert "Action sensible" in response
    security.target.add_roles.assert_not_awaited()
    return next(iter(security.cog.pending_confirmations))


@pytest.mark.asyncio
async def test_mutations_need_explicit_confirmation_even_with_legacy_flag_off(security, monkeypatch):
    monkeypatch.setattr(iastaff, "IASTAFF_CONFIRM_DESTRUCTIVE", False)
    arguments = {"title": "Donjon", "datetime": "01/11/2026 20:00", "description": "Guilde"}
    response = await security.cog._dispatch_command_tool(security.ctx, "create_activity", arguments)
    assert security.effects == []
    assert "Donjon" in response and "1000" in response
    nonce = next(iter(security.cog.pending_confirmations))
    arguments["title"] = "CHANGED_AFTER_PROPOSAL"
    await security.cog._confirm_pending_action(security.ctx, nonce)
    assert security.effects == [("creer", "Donjon 01/11/2026 20:00 Guilde")]
    assert not security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_allowed_role_uses_frozen_ids_after_names_change(security):
    nonce = await propose_role(security)
    entry = security.cog.pending_confirmations[nonce]
    assert json.loads(entry.payload_json) == {"member": "20", "role": "5"}
    security.target.name = security.target.display_name = "Renamed"
    security.role.name = "Renamed role"
    impostor = member(security.guild, 21, "Player")
    security.guild.members.append(impostor)
    result = await security.cog._confirm_pending_action(security.ctx, nonce)
    assert "ajouté" in result
    security.target.add_roles.assert_awaited_once_with(security.role, reason="IA Staff role grant")
    impostor.add_roles.assert_not_awaited()


@pytest.mark.parametrize("failure", [
    "no_actor_permission", "no_bot_permission", "empty_allowlist", "managed", "default",
    "administrator", "manage_messages", "staff_role", "actor_hierarchy", "bot_hierarchy",
    "target_hierarchy", "owner_target", "foreign_role", "foreign_target",
])
@pytest.mark.asyncio
async def test_role_policy_fails_closed_without_a_mutation(security, monkeypatch, failure):
    if failure == "no_actor_permission":
        security.actor.guild_permissions.manage_roles = False
    elif failure == "no_bot_permission":
        security.guild.me.guild_permissions.manage_roles = False
    elif failure == "empty_allowlist":
        monkeypatch.delenv("IASTAFF_ALLOWED_ROLE_IDS")
    elif failure in {"managed", "default"}:
        setattr(security.role, failure, True)
    elif failure in {"administrator", "manage_messages"}:
        setattr(security.role.permissions, failure, True)
    elif failure == "staff_role":
        security.role.name = "Staff"
    elif failure == "actor_hierarchy":
        security.role.position = 10
    elif failure == "bot_hierarchy":
        security.role.position = 20
    elif failure == "target_hierarchy":
        security.target.top_role.position = 10
    elif failure == "owner_target":
        security.guild.owner_id = security.target.id
    elif failure == "foreign_role":
        security.role.guild = SimpleNamespace(id=2000)
    elif failure == "foreign_target":
        security.target.guild = SimpleNamespace(id=2000)
    with pytest.raises(commands.CommandError):
        await security.cog._dispatch_command_tool(security.ctx, "grant_role", {"member": "20", "role": "5"})
    security.target.add_roles.assert_not_awaited()
    assert not security.cog.pending_confirmations


@pytest.mark.parametrize("change", ["staff_removed", "permission_removed", "role_changed", "target_missing"])
@pytest.mark.asyncio
async def test_permissions_and_targets_are_checked_again_on_confirmation(security, change):
    nonce = await propose_role(security)
    if change == "staff_removed":
        security.actor.roles = []
    elif change == "permission_removed":
        security.actor.guild_permissions.manage_roles = False
    elif change == "role_changed":
        security.role.permissions.administrator = True
    else:
        security.guild.members.remove(security.target)
    result = await security.cog._confirm_pending_action(security.ctx, nonce)
    assert "refusée" in result
    security.target.add_roles.assert_not_awaited()
    assert not security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_fresh_http_roles_prevent_using_stale_cached_permissions(security):
    nonce = await propose_role(security)
    changed_role = Role(security.guild)
    changed_role.permissions.administrator = True
    security.guild.fetch_roles = AsyncMock(return_value=[changed_role])
    result = await security.cog._confirm_pending_action(security.ctx, nonce)
    assert "refusée" in result
    assert not security.role.permissions.administrator  # cache still looks harmless
    security.target.add_roles.assert_not_awaited()


@pytest.mark.parametrize("field", ["user", "channel", "guild"])
@pytest.mark.asyncio
async def test_confirmation_is_bound_to_its_requester_and_context(security, field):
    nonce = await propose_role(security)
    other = SimpleNamespace(**vars(security.ctx))
    if field == "user":
        other.author = SimpleNamespace(id=123)
    elif field == "channel":
        other.channel = SimpleNamespace(id=123)
    else:
        other.guild = SimpleNamespace(id=123)
    result = await security.cog._confirm_pending_action(other, nonce)
    assert "refusée" in result
    security.target.add_roles.assert_not_awaited()
    assert nonce in security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_expired_and_replayed_confirmations_never_execute(security):
    nonce = await propose_role(security)
    old = security.cog.pending_confirmations[nonce]
    security.cog.pending_confirmations[nonce] = replace(old, created_at=old.created_at - timedelta(seconds=301))
    await security.cog._confirm_pending_action(security.ctx, nonce)
    security.target.add_roles.assert_not_awaited()
    nonce = await propose_role(security)
    await asyncio.gather(
        security.cog._confirm_pending_action(security.ctx, nonce),
        security.cog._confirm_pending_action(security.ctx, nonce),
    )
    await security.cog._confirm_pending_action(security.ctx, nonce)
    security.target.add_roles.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_confirmation_bypass_parameter_exists(security):
    with pytest.raises(TypeError):
        await security.cog._dispatch_command_tool(security.ctx, "grant_role", {}, skip_confirmation=True)
    security.target.add_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabling_tools_invalidates_pending_mutations(security):
    nonce = await propose_role(security)
    security.cog.enable_tools = False
    result = await security.cog._confirm_pending_action(security.ctx, nonce)
    assert "refusée" in result
    security.target.add_roles.assert_not_awaited()
    assert not security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_unknown_tools_and_arguments_are_rejected(security):
    with pytest.raises(commands.BadArgument):
        await security.cog._dispatch_command_tool(security.ctx, "made_up_tool", {})
    with pytest.raises(commands.BadArgument):
        await security.cog._dispatch_command_tool(security.ctx, "grant_role", {"member": "20", "role": "5", "skip_confirmation": True})
    assert not security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_ambiguous_and_missing_numeric_targets_are_not_resolved_by_name(security):
    security.guild.members.append(member(security.guild, 21, "Player"))
    with pytest.raises(commands.BadArgument, match="ambigu"):
        await propose_role(security)
    security.guild.members.append(member(security.guild, 22, "404"))
    assert security.cog._resolve_member_argument(security.ctx, "404") is None


def test_channel_history_is_never_elevated_to_instruction_role():
    cog = object.__new__(IAStaff)
    cog.history = {}
    cog.system_prompt = "Fixed operator instructions"
    messages = cog._make_messages("UNTRUSTED_CHANNEL_MARKER", 1, "Summarize")
    selected = [entry for entry in messages if any("UNTRUSTED_CHANNEL_MARKER" in part.get("text", "") for part in entry["content"])]
    assert selected and all(entry["role"] == "user" for entry in selected)
    converted = cog._to_chat_messages(messages)
    assert all(entry["role"] == "user" for entry in converted if "UNTRUSTED_CHANNEL_MARKER" in entry["content"])


@pytest.mark.asyncio
async def test_command_global_checks_are_applied_before_proposal(security):
    security.bot.add_check(lambda ctx: False, call_once=True)
    with pytest.raises(commands.CheckFailure):
        await security.cog._dispatch_command_tool(security.ctx, "create_activity", {"title": "X", "datetime": "Tomorrow"})
    assert security.effects == [] and not security.cog.pending_confirmations


@pytest.mark.asyncio
async def test_generic_command_uses_real_converters_hooks_and_cooldowns(security, monkeypatch):
    monkeypatch.setattr(iastaff, "IASTAFF_RUN_COMMAND_WHITELIST", {"counter"})
    events = []

    @security.bot.command(name="counter")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def counter(ctx, count: int):
        events.append(("callback", count))

    @counter.before_invoke
    async def before(ctx):
        events.append("before")

    @counter.after_invoke
    async def after(ctx):
        events.append("after")

    payload = {"command": "counter", "positional_args": ["7"]}
    await security.cog._dispatch_command_tool(security.ctx, "run_bot_command", payload)
    assert events == []
    await security.cog._confirm_pending_action(security.ctx, next(iter(security.cog.pending_confirmations)))
    assert events == ["before", ("callback", 7), "after"]
    await security.cog._dispatch_command_tool(security.ctx, "run_bot_command", payload)
    result = await security.cog._confirm_pending_action(security.ctx, next(iter(security.cog.pending_confirmations)))
    assert "refusée" in result
    assert events == ["before", ("callback", 7), "after"]


@pytest.mark.asyncio
async def test_parent_checks_and_concurrency_cleanup_are_not_bypassed(security):
    @security.bot.group()
    @commands.check(lambda ctx: False)
    async def guarded(ctx):
        pass

    @guarded.command()
    async def child(ctx):
        security.effects.append("forbidden")

    with pytest.raises(commands.CheckFailure):
        await invoke_checked(security.ctx, child)
    assert security.effects == []

    @security.bot.command()
    @commands.max_concurrency(1, commands.BucketType.user, wait=False)
    async def failing(ctx):
        raise ValueError("controlled test error")

    for _ in range(2):
        with pytest.raises(commands.CommandInvokeError):
            await invoke_checked(security.ctx, failing)
