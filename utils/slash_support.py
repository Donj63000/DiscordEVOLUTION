"""Je conserve les contrôles des commandes texte lors d'un appel depuis le menu Discord."""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands.view import StringView

log = logging.getLogger(__name__)
PRIVATE_WORKFLOWS = {"ticket", "event", "avis", "ia", "iaend", "profil set", "profil import"}


async def delete_invocation_message(ctx, **kwargs):
    """Je supprime uniquement le message réel d'une commande envoyée avec le préfixe."""
    if getattr(ctx, "interaction", None) is not None:
        log.debug("Slash: skip_synthetic_message_delete command=%s", ctx.command)
        return
    await ctx.message.delete(**kwargs)


async def notify_private_workflow(ctx):
    """Je confirme l'ouverture des MP sans laisser le menu Discord en attente."""
    if getattr(ctx, "interaction", None) is not None and not getattr(ctx, "response_count", 0):
        try:
            await ctx.send("📩 La conversation privée est ouverte. Regarde tes messages privés.", ephemeral=True)
        except discord.HTTPException:
            log.debug("Slash: private_workflow_ack_failed command=%s", ctx.command, exc_info=True)


class EvolutionCommandTree(app_commands.CommandTree):
    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.CommandOnCooldown):
            message = f"Réessaie dans {original.retry_after:.0f} seconde(s)."
        elif isinstance(original, app_commands.CheckFailure):
            message = "Tu n’as pas la permission d’utiliser cette commande ici."
        elif isinstance(original, (app_commands.TransformerError, ValueError)):
            message = f"Paramètre invalide : {original}"
        else:
            message = "La commande a rencontré une erreur. Le Staff peut consulter les logs."
        log.debug(
            "Slash: command_failed guild_id=%s user_id=%s command=%s",
            interaction.guild_id,
            interaction.user.id,
            getattr(interaction.command, "qualified_name", "unknown"),
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SlashContext(commands.Context):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.response_count = 0
        self.private_response = False

    async def send(self, content=None, **kwargs):
        kwargs.setdefault("ephemeral", self.private_response)
        if self.private_response and self.interaction.is_expired():
            kwargs.pop("ephemeral", None)
            message = await self.author.send(content, **kwargs)
        else:
            message = await super().send(content, **kwargs)
        self.response_count += 1
        return message


def parse_message_reference(value: str, *, guild_id: int, channel_id: int) -> discord.MessageReference:
    text = value.strip()
    if text.isdigit() and int(text) > 0:
        message_id = int(text)
    else:
        match = re.fullmatch(
            r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)/?",
            text,
        )
        if not match:
            raise ValueError("Indique le lien Discord ou l’identifiant du message à importer.")
        source_guild, source_channel, message_id = map(int, match.groups())
        if (source_guild, source_channel) != (guild_id, channel_id):
            raise ValueError("Utilise cette commande dans le salon qui contient le message à importer.")
    return discord.MessageReference(
        message_id=message_id, guild_id=guild_id, channel_id=channel_id
    )


async def invoke_from_slash(
    bot: commands.Bot,
    interaction: discord.Interaction,
    target: str,
    arguments: str,
    *,
    values: dict[str, object] | None = None,
    message_reference: str | None = None,
) -> SlashContext | None:
    """J'exécute conversions, permissions, cooldowns et hooks avant le traitement existant."""
    command = bot.get_command(target)
    if command is None:
        await interaction.response.send_message(
            "Cette commande est temporairement indisponible.", ephemeral=True
        )
        return None
    if interaction.guild is None:
        await interaction.response.send_message(
            "Utilise cette commande depuis un salon du serveur.", ephemeral=True
        )
        return None
    reference = None
    if message_reference is not None:
        reference = parse_message_reference(
            message_reference, guild_id=interaction.guild_id, channel_id=interaction.channel_id
        )
    if target in PRIVATE_WORKFLOWS:
        await interaction.response.defer(thinking=True, ephemeral=True)
    else:
        await interaction.response.defer(thinking=True)
    ctx = await SlashContext.from_interaction(interaction)
    ctx.private_response = target in PRIVATE_WORKFLOWS
    ctx.command = command
    ctx.invoked_with = command.name
    ctx.invoked_parents = [parent.name for parent in reversed(command.parents)]
    ctx.view = StringView(arguments)
    ctx.message.content = f"/{target} {arguments}".rstrip()
    ctx.message.reference = reference
    members = {
        value.id: value for value in (values or {}).values() if isinstance(value, discord.Member)
    }
    for member_id in re.findall(r"<@!?(\d+)>", arguments):
        member = interaction.guild.get_member(int(member_id))
        if member is not None:
            members[member.id] = member
    ctx.message.mentions = list(members.values())
    log.debug(
        "Slash: invoke target=%s guild_id=%s channel_id=%s user_id=%s",
        target, interaction.guild_id, interaction.channel_id, interaction.user.id,
    )
    bot.dispatch("command", ctx)
    try:
        if not await bot.can_run(ctx, call_once=True):
            raise commands.CheckFailure("Le contrôle global a refusé cette commande.")
        for parent in reversed(command.parents):
            if not await parent.can_run(ctx):
                raise commands.CheckFailure("Le contrôle du groupe a refusé cette commande.")
        await commands.Command.invoke(command, ctx)
    except commands.CommandError as error:
        ctx.command_failed = True
        await command.dispatch_error(ctx, error)
    else:
        bot.dispatch("command_completion", ctx)
        if ctx.response_count == 0:
            await ctx.send("Commande terminée.")
    return ctx
