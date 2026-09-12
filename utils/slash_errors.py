"""Messages publics maîtrisés : aucune exception technique n'est renvoyée telle quelle."""

from __future__ import annotations

import asyncio
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands


class SlashInputError(ValueError):
    """Erreur de validation dont le texte est destiné explicitement à l'utilisateur."""


def original_error(error: Exception) -> Exception:
    while isinstance(error, (commands.CommandInvokeError, app_commands.CommandInvokeError)):
        error = error.original
    return error


def error_message(error: Exception) -> str:
    error = original_error(error)
    if isinstance(error, SlashInputError):
        return str(error)
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return f"Réessaie dans {max(1, math.ceil(error.retry_after))} seconde(s)."
    if isinstance(error, commands.MaxConcurrencyReached):
        return "Une opération est déjà en cours. Attends sa fin avant de réessayer."
    if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
        return "Il manque des permissions au bot dans ce salon. Demande au Staff de les vérifier."
    if isinstance(error, (commands.NoPrivateMessage, app_commands.NoPrivateMessage)):
        return "Utilise cette commande dans un salon du serveur."
    if isinstance(error, (commands.CheckFailure, app_commands.CheckFailure)):
        return "Tu n'as pas la permission d'utiliser cette commande ici."
    if isinstance(error, (commands.UserInputError, app_commands.TransformerError)):
        return "Un paramètre est invalide. Vérifie les champs proposés ou consulte `/aide`."
    if isinstance(error, (app_commands.CommandNotFound, app_commands.CommandSignatureMismatch)):
        return "Cette commande a été mise à jour. Ferme le menu `/`, rouvre-le et consulte `/aide`."
    if isinstance(error, discord.Forbidden):
        if error.code == 50007:
            return "Tes messages privés sont fermés. Autorise les MP du serveur puis réessaie."
        return "Discord refuse cette action. Le Staff doit vérifier les permissions du bot."
    if isinstance(error, discord.NotFound):
        return "Le message ou la ressource n'existe plus. Rouvre la commande."
    if isinstance(error, discord.HTTPException):
        return "Discord n'a pas pu terminer la demande. Vérifie le résultat avant de réessayer."
    return "La commande a rencontré une erreur. Le Staff peut consulter les logs."


def log_command_error(logger: logging.Logger, error: Exception, *, command: str) -> None:
    original = original_error(error)
    expected = isinstance(original, (
        SlashInputError, commands.UserInputError, commands.CheckFailure,
        commands.CommandOnCooldown, commands.MaxConcurrencyReached,
        app_commands.CheckFailure, app_commands.TransformerError,
        app_commands.CommandOnCooldown, app_commands.CommandNotFound,
    ))
    logger.log(
        logging.DEBUG if expected else logging.ERROR,
        "Slash : échec de %s (%s).", command, type(original).__name__,
        exc_info=None if expected else (type(original), original, original.__traceback__),
    )


async def send_interaction_error(interaction, message: str) -> bool:
    """Finish loading responses on errors too, without ever leaking a private error publicly."""
    if interaction.is_expired():
        return False

    async def deliver():
        if interaction.response.is_done():
            if getattr(interaction.response, "type", None) is discord.InteractionResponseType.deferred_channel_message:
                original = await interaction.original_response()
                if original.flags.loading:
                    if original.flags.ephemeral:
                        await interaction.edit_original_response(
                            content=message, embed=None, view=None, attachments=[],
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return
                    await interaction.edit_original_response(
                        content="Une réponse à cette commande t'est envoyée en privé.",
                        embed=None, view=None, attachments=[],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            await interaction.followup.send(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )

    try:
        await asyncio.wait_for(deliver(), timeout=10)
    except (discord.HTTPException, asyncio.TimeoutError):
        logging.getLogger(__name__).debug("Slash : interaction devenue indisponible.", exc_info=True)
        return False
    return True
