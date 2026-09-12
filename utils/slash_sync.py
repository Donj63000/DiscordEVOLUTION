"""Synchronisation du catalogue sans effacer les commandes d'un autre périmètre."""

from __future__ import annotations

import logging
import os

import discord

from utils.command_policy import enabled_flag, unavailable_roots

log = logging.getLogger(__name__)


def sync_enabled() -> bool:
    return enabled_flag("SYNC_SLASH_COMMANDS", True)


def configured_guild() -> discord.Object | None:
    raw = os.getenv("SYNC_SLASH_GUILD_ID", "").strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**64:
        raise ValueError("SYNC_SLASH_GUILD_ID doit contenir un identifiant Discord positif.")
    return discord.Object(id=int(raw))


async def remove_retired_remote_commands(tree, *, guild=None) -> bool:
    """Supprime seulement les anciennes entrées identifiées, jamais toute une guilde."""
    blocked = unavailable_roots()
    try:
        remote = await tree.fetch_commands(guild=guild)
        for command in remote:
            if command.type is discord.AppCommandType.chat_input and command.name in blocked:
                await command.delete()
                log.info(
                    "Slash : ancienne commande /%s retirée du périmètre %s.",
                    command.name, getattr(guild, "id", "global"),
                )
    except discord.HTTPException:
        log.warning("Slash : nettoyage différé du périmètre %s.",
                    getattr(guild, "id", "global"), exc_info=True)
        return False
    return True


async def sync_application_commands(bot) -> bool:
    bot._slash_sync_succeeded = False
    if not sync_enabled():
        log.info("Slash : synchronisation et nettoyage désactivés (SYNC_SLASH_COMMANDS=0).")
        return False
    try:
        guild = configured_guild()
    except ValueError:
        # Ne surtout pas basculer implicitement vers une publication globale.
        log.error("Slash : identifiant de guilde invalide ; aucune publication.", exc_info=True)
        return False

    try:
        if guild is not None:
            bot.tree.copy_global_to(guild=guild)
            for name in unavailable_roots():
                bot.tree.remove_command(name, guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
    except (discord.HTTPException, discord.app_commands.AppCommandError):
        log.error("Slash : synchronisation échouée ; le catalogue distant peut être ancien.",
                  exc_info=True)
        return False

    bot._slash_sync_succeeded = True
    log.info("Slash : %d commandes synchronisées (%s).", len(synced),
             getattr(guild, "id", "global"))
    if guild is not None and enabled_flag("SLASH_CLEANUP_RETIRED", True):
        # Une ancienne publication globale peut sinon laisser /ia dans le sélecteur.
        await remove_retired_remote_commands(bot.tree)
    return True


async def cleanup_retired_guild_commands(bot) -> None:
    """Après connexion, retire aussi les copies anciennes dans les guildes du bot."""
    if not (
        getattr(bot, "_slash_sync_succeeded", False)
        and sync_enabled()
        and enabled_flag("SLASH_CLEANUP_RETIRED", True)
    ):
        return
    done = getattr(bot, "_slash_cleanup_done", None)
    if done is None:
        done = bot._slash_cleanup_done = set()
    for guild in bot.guilds:
        if guild.id not in done:
            if await remove_retired_remote_commands(bot.tree, guild=guild):
                done.add(guild.id)
