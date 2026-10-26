"""Single-guild security boundary shared by commands, services and listeners.

The configured guild is never inferred from Discord's cache.  An empty cache at
startup is normal; it cannot authorize a different guild or console.
"""
from __future__ import annotations

import os

from discord.ext import commands


class GuildScopeError(commands.CheckFailure):
    """The request does not belong to the bot's configured guild."""


def _snowflake(value) -> int | None:
    if type(value) is int:
        return value if 0 < value < 2**64 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
        return parsed if 0 < parsed < 2**64 else None
    return None


def get_allowed_guild_id() -> int:
    guild_id = _snowflake(os.getenv("BOT_GUILD_ID", "").strip())
    if guild_id is None:
        raise GuildScopeError("BOT_GUILD_ID doit désigner le serveur Evolution autorisé.")
    legacy = os.getenv("EVO_GUILD_ID", "").strip()
    if legacy and _snowflake(legacy) != guild_id:
        raise GuildScopeError("EVO_GUILD_ID doit correspondre à BOT_GUILD_ID.")
    return guild_id


def configured_guild_id(bot=None) -> int:
    # Only a real integer is accepted: mocks, missing attributes and arbitrary
    # truthy objects must not become a source of authorization.
    cached = _snowflake(getattr(bot, "allowed_guild_id", None))
    return cached if cached is not None else get_allowed_guild_id()


def is_allowed_guild(guild_or_id, bot=None) -> bool:
    value = guild_or_id if isinstance(guild_or_id, (int, str)) else getattr(guild_or_id, "id", None)
    try:
        return _snowflake(value) == configured_guild_id(bot)
    except GuildScopeError:
        return False


def require_guild(guild_or_id, bot=None) -> int:
    if not is_allowed_guild(guild_or_id, bot):
        raise GuildScopeError("Cette action est réservée au serveur Evolution configuré.")
    return configured_guild_id(bot)


def require_context(ctx) -> int:
    return require_guild(getattr(ctx, "guild", None), getattr(ctx, "bot", None))


def allowed_guilds(bot) -> list:
    return [guild for guild in list(getattr(bot, "guilds", ()) or ()) if is_allowed_guild(guild, bot)]


async def check_interaction_guild(interaction, bot=None) -> bool:
    """Private, normal rejection for views and modals, including old panels."""
    guild = getattr(interaction, "guild_id", None) or getattr(interaction, "guild", None)
    if is_allowed_guild(guild, bot):
        return True
    expired = getattr(interaction, "is_expired", None)
    if callable(expired) and expired():
        return False
    import discord
    text = "Cette action est réservée au serveur Evolution configuré."
    options = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text, **options)
    else:
        await interaction.response.send_message(text, **options)
    return False


def event_is_allowed(event: str, args, bot) -> bool:
    """Reject foreign guild events before their business listeners run."""
    for index, value in enumerate(args):
        guild = getattr(value, "guild", None)
        if guild is None:
            guild = getattr(getattr(value, "message", None), "guild", None)
        guild_id = getattr(value, "guild_id", None)
        if guild is not None and not is_allowed_guild(guild, bot):
            return False
        if guild_id is not None and not is_allowed_guild(guild_id, bot):
            return False
        if index == 0 and event in {
            "guild_join", "guild_remove", "guild_update", "guild_available",
            "guild_unavailable", "guild_integrations_update",
        } and not is_allowed_guild(value, bot):
            return False
    return True
