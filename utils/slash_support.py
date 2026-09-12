"""Je conserve les contrôles des commandes texte lors d'un appel depuis le menu Discord."""

from __future__ import annotations

import copy
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands.view import StringView

from utils.command_policy import unavailable_reason
from utils.slash_errors import (
    SlashInputError, error_message, log_command_error, send_interaction_error,
)

log = logging.getLogger(__name__)
PRIVATE_WORKFLOWS = {
    "aide", "ticket", "event", "avis", "ia", "iaend", "profil set", "profil import",
    "warnings", "resetwarnings", "defenderstatus", "clear", "recrutement",
    "accueil", "accueil statut", "accueil relance", "accueil reset",
    "membre principal", "membre addmule", "membre delmule", "membre del",
    "profil delete", "stats reset", "stats on", "stats off",
    "annonce-list", "annonce-cancel", "organisation-sync", "close_sondage", "sondage",
}


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
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Cette barrière couvre aussi les commandes natives et les anciens menus Discord.
        name = (interaction.data or {}).get("name", "")
        reason = unavailable_reason(name)
        if reason is not None:
            await send_interaction_error(interaction, reason)
            return False
        if interaction.guild is None:
            await send_interaction_error(interaction, "Utilise cette commande dans un salon du serveur.")
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        name = getattr(interaction.command, "qualified_name", (interaction.data or {}).get("name", "inconnue"))
        message = unavailable_reason(name) or error_message(error)
        log_command_error(log, error, command=name)
        await send_interaction_error(interaction, message)


class SlashContext(commands.Context):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.response_count = 0
        self.private_response = False
        self.slash_error_handled = False
        self.slash_values: dict[str, object] = {}
        self._public_deferred = False

    async def send(self, content=None, **kwargs):
        # Le caractère privé est irréversible, y compris après expiration du jeton.
        private = self.private_response or self.command_failed or kwargs.pop("ephemeral", False)
        kwargs["ephemeral"] = private
        if private and self.interaction.is_expired():
            kwargs.pop("ephemeral", None)
            kwargs.pop("reference", None)
            kwargs.pop("mention_author", None)
            message = await self.author.send(content, **kwargs)
        else:
            if private and self._public_deferred and self.response_count == 0:
                # L'éphémérité du premier message différé ne peut pas être modifiée.
                # On termine donc publiquement l'attente sans divulguer le détail.
                await self.interaction.edit_original_response(
                    content="Une réponse à cette commande t'est envoyée en privé.",
                    embed=None, view=None, attachments=[],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._public_deferred = False
            message = await super().send(content, **kwargs)
        self.response_count += 1
        return message


def parse_message_reference(value: str, *, guild_id: int, channel_id: int) -> discord.MessageReference:
    text = value.strip()
    if len(text) > 200:
        raise SlashInputError("Le lien ou l'identifiant du message est trop long.")
    if text.isascii() and text.isdigit() and 0 < int(text) < 2**64:
        message_id = int(text)
    else:
        match = re.fullmatch(
            r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/([0-9]+)/([0-9]+)/([0-9]+)/?",
            text,
        )
        if not match:
            raise SlashInputError("Indique le lien Discord ou l’identifiant du message à importer.")
        source_guild, source_channel, message_id = map(int, match.groups())
        if not all(0 < identifier < 2**64 for identifier in (source_guild, source_channel, message_id)):
            raise SlashInputError("Le lien contient un identifiant Discord invalide.")
        if (source_guild, source_channel) != (guild_id, channel_id):
            raise SlashInputError("Utilise cette commande dans le salon qui contient le message à importer.")
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
    private: bool = False,
    defer_response: bool = True,
) -> SlashContext | None:
    """J'exécute conversions, permissions, cooldowns et hooks avant le traitement existant."""
    reason = unavailable_reason(target)
    if reason is not None:
        await send_interaction_error(interaction, reason)
        return None
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
    private_response = private or target in PRIVATE_WORKFLOWS
    if defer_response:
        if private_response:
            await interaction.response.defer(thinking=True, ephemeral=True)
        else:
            await interaction.response.defer(thinking=True)
    ctx = await SlashContext.from_interaction(interaction)
    ctx.private_response = private_response
    ctx._public_deferred = defer_response and not private_response
    ctx.slash_values = dict(values or {})
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
    return await _invoke_checked_context(bot, command, ctx)


async def _invoke_checked_context(bot, command, ctx):
    """Conserve conversions, contrôles, hooks et événements sans doubler les erreurs."""
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
        ctx.slash_error_handled = True
        before = ctx.response_count
        log_command_error(log, error, command=command.qualified_name)
        try:
            # Les gestionnaires métiers gardent la priorité. Le gestionnaire global
            # de main.py ignore cette erreur, déjà prise en charge par le pont.
            await command.dispatch_error(ctx, error)
        except Exception as handler_error:
            log_command_error(log, handler_error, command=command.qualified_name)
        if ctx.response_count == before:
            try:
                await ctx.send(error_message(error))
            except discord.HTTPException:
                log.warning("Slash : réponse d'erreur impossible pour %s.", command.qualified_name,
                            exc_info=True)
    else:
        bot.dispatch("command_completion", ctx)
        if ctx.response_count == 0:
            await ctx.send("Commande terminée.")
    return ctx


class ComponentContext(SlashContext):
    """Répond en privé sans notifier les membres, les rôles ni tout le serveur."""

    async def send(self, content=None, **kwargs):
        kwargs["ephemeral"] = True
        kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        return await super().send(content, **kwargs)


async def invoke_from_component(
    bot: commands.Bot, interaction: discord.Interaction, target: str, arguments: str,
    *, values: dict[str, object] | None = None, defer_response: bool = True,
) -> SlashContext | None:
    """Exécute la commande avec les droits du membre qui clique, jamais ceux du bot."""
    reason = unavailable_reason(target)
    if reason is not None:
        await send_interaction_error(interaction, reason)
        return None
    command = bot.get_command(target)
    if command is None or interaction.guild is None or interaction.message is None:
        message = "Cette action est temporairement indisponible. Rouvre la commande depuis le menu /."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return None
    if defer_response and not interaction.response.is_done():
        await interaction.response.defer()
    message = copy.copy(interaction.message)
    message.author = interaction.user
    message.content = f"/{target} {arguments}"
    message.mentions = [interaction.user, *[
        value for value in (values or {}).values()
        if isinstance(value, discord.Member) and value.id != interaction.user.id
    ]]
    ctx = ComponentContext(
        bot=bot, message=message, view=StringView(arguments),
        interaction=interaction, prefix="/", command=command,
        invoked_with=command.name, invoked_parents=[p.name for p in reversed(command.parents)],
    )
    ctx.private_response = True
    ctx.slash_values = dict(values or {})
    log.debug("Slash component: invoke target=%s guild_id=%s user_id=%s",
              target, interaction.guild_id, interaction.user.id)
    return await _invoke_checked_context(bot, command, ctx)
