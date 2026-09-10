#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import secrets
from typing import Optional

import discord
from discord.ext import commands
from utils.slash_support import delete_invocation_message, notify_private_workflow
from discord.utils import utcnow

log = logging.getLogger("avis")

DEFAULT_STAFF_NAMES = "avis-joueurs"
DEFAULT_DM_INSTRUCTIONS = (
    "Tu peux écrire librement ton avis sur Evolution : problème, idée ou suggestion.\n"
    "➡️ Réponds à ce message dans un seul bloc (pas de pièce jointe).\n"
    "➡️ Ton avis est anonyme, seul le staff verra le contenu.\n"
    "➡️ Tape `annuler` pour abandonner.\n"
    "⏳ Délai restant : {timeout}."
)


def normalize_name(value: str) -> str:
    """Lowercase channel name and strip non alphanumeric characters."""
    base = value.strip().casefold()
    return "".join(ch for ch in base if ch.isalnum())


def parse_id_list(value: str) -> list[int]:
    ids: list[int] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            continue
    return ids


def is_cancel_message(content: str) -> bool:
    normalized = content.strip().casefold()
    return normalized in {"annuler", "cancel", "stop"}


def build_staff_embed(
    message: str,
    token: str,
    guild_name: str,
    origin_channel_label: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Avis anonyme #{token}",
        description=message.strip(),
        color=discord.Color.blurple(),
        timestamp=utcnow(),
    )
    embed.add_field(
        name="Contexte",
        value=f"Serveur : **{guild_name}**\nCollecté via : {origin_channel_label}",
        inline=False,
    )
    embed.set_footer(text="Transmis automatiquement par #avis")
    return embed


class AvisFeedback(commands.Cog):
    """Collecte d'avis anonymes envoyés au staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.staff_channel_ids = parse_id_list(os.getenv("AVIS_STAFF_CHANNEL_IDS", ""))
        raw_staff_names = os.getenv("AVIS_STAFF_CHANNEL_NAMES", DEFAULT_STAFF_NAMES)
        self.staff_channel_tokens = self._build_token_set(raw_staff_names, default="avis-joueurs")

        self.min_chars = int(os.getenv("AVIS_MIN_MESSAGE_LENGTH", "20"))
        self.dm_timeout = int(os.getenv("AVIS_DM_TIMEOUT", "240"))
        self.max_attempts = int(os.getenv("AVIS_MAX_ATTEMPTS", "3"))
        self.dm_instructions = os.getenv("AVIS_DM_INSTRUCTIONS", DEFAULT_DM_INSTRUCTIONS)
        self.active_sessions: set[int] = set()

    @staticmethod
    def _build_token_set(raw: str, default: str) -> set[str]:
        tokens = {
            normalize_name(part)
            for part in raw.split(",")
            if part.strip()
        }
        cleaned = {token for token in tokens if token}
        if not cleaned:
            cleaned.add(normalize_name(default))
        return cleaned

    def _normalize_timeout_text(self) -> str:
        if self.dm_timeout >= 90:
            minutes = max(1, round(self.dm_timeout / 60))
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        return f"{self.dm_timeout} secondes"

    def _resolve_staff_channel(self, guild: discord.Guild | None) -> Optional[discord.TextChannel]:
        if guild is None:
            return None
        for channel_id in self.staff_channel_ids:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        for channel in guild.text_channels:
            if normalize_name(channel.name) in self.staff_channel_tokens:
                return channel
        return None

    def _generate_token(self) -> str:
        return secrets.token_hex(3).upper()

    def _format_instructions(self) -> str:
        return self.dm_instructions.format(timeout=self._normalize_timeout_text())

    def _origin_label(self, channel: discord.abc.GuildChannel | None) -> str:
        if isinstance(channel, discord.TextChannel):
            mention = getattr(channel, "mention", None)
            if mention:
                return mention
        if channel is not None:
            name = getattr(channel, "name", None)
            if name:
                return f"#{name}"
        return "#inconnu"

    async def _request_dm(self, user: discord.abc.User) -> Optional[discord.DMChannel]:
        try:
            dm = await user.create_dm()
        except discord.Forbidden:
            return None
        except discord.HTTPException as exc:
            log.warning("Impossible d'ouvrir un MP pour %s: %s", user, exc)
            return None
        return dm

    async def _collect_dm_feedback(self, author: discord.User) -> Optional[str]:
        check = (
            lambda message: message.author.id == author.id
            and isinstance(message.channel, discord.DMChannel)
        )
        attempts = 0
        while attempts < self.max_attempts:
            try:
                response = await self.bot.wait_for("message", check=check, timeout=self.dm_timeout)
            except asyncio.TimeoutError:
                try:
                    await author.send(
                        "⏰ Temps dépassé. Réessaie depuis #avis quand tu seras prêt."
                    )
                except discord.HTTPException:
                    pass
                return None
            content = (response.content or "").strip()
            if is_cancel_message(content):
                try:
                    await author.send("Compris, ton avis n'a pas été envoyé. Tu peux recommencer via #avis quand tu veux.")
                except discord.HTTPException:
                    pass
                return None
            if len(content) < self.min_chars:
                attempts += 1
                try:
                    await author.send(
                        f"Ton avis doit contenir au moins {self.min_chars} caractères. "
                        "Essaie d'apporter un peu plus de détails."
                    )
                except discord.HTTPException:
                    pass
                continue
            if response.attachments:
                attempts += 1
                try:
                    await author.send("Les pièces jointes ne sont pas prises en charge. Réécris ton avis en texte, s'il te plaît.")
                except discord.HTTPException:
                    pass
                continue
            return content
        try:
            await author.send("Je n'ai pas pu enregistrer ton avis. Reviens via #avis si besoin.")
        except discord.HTTPException:
            pass
        return None

    @commands.command(name="avis")
    @commands.guild_only()
    async def avis_cmd(self, ctx: commands.Context):
        if ctx.author.bot:
            return

        try:
            await delete_invocation_message(ctx, delay=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

        if ctx.author.id in self.active_sessions:
            try:
                await ctx.author.send(
                    "Je t'ai déjà ouvert une conversation privée. Termine-la depuis nos DM avant de recommencer."
                )
            except discord.HTTPException:
                pass
            return
        dm_channel = await self._request_dm(ctx.author)
        if dm_channel is None:
            try:
                await ctx.send(
                    f"{ctx.author.mention} je ne peux pas t'envoyer de MP. Active temporairement tes MP depuis ce serveur puis relance `!avis`.",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass
            return
        instructions_embed = discord.Embed(
            title="Avis anonyme Evolution",
            description=(
                "🗨️ Conversation privée ouverte avec EvolutionBOT.\n"
                "Tout ce que tu écriras ici sera transmis anonymement au staff.\n\n"
                + self._format_instructions()
            ),
            color=discord.Color.orange(),
        )
        try:
            await dm_channel.send(embed=instructions_embed)
        except discord.HTTPException as exc:
            log.warning("Impossible d'envoyer les instructions d'avis à %s: %s", ctx.author, exc)
            try:
                await ctx.send(
                    f"{ctx.author.mention} je n'arrive pas à t'envoyer le formulaire d'avis. Merci de prévenir le staff.",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass
            return
        try:
            await dm_channel.send(
                "Lorsque tu es prêt, réponds directement à ce message avec ton avis (tu peux écrire autant de texte que nécessaire)."
            )
        except discord.HTTPException:
            pass

        await notify_private_workflow(ctx)
        self.active_sessions.add(ctx.author.id)
        try:
            feedback_text = await self._collect_dm_feedback(ctx.author)
            if not feedback_text:
                return
            staff_channel = self._resolve_staff_channel(ctx.guild)
            if staff_channel is None:
                log.error("Canal staff 'avis-joueurs' introuvable pour la guilde %s", ctx.guild and ctx.guild.id)
                try:
                    await ctx.author.send("Le canal staff pour recevoir ton avis est introuvable. Préviens un membre du staff.")
                except discord.HTTPException:
                    pass
                return
            token = self._generate_token()
            embed = build_staff_embed(
                feedback_text,
                token,
                ctx.guild.name if ctx.guild else "Serveur inconnu",
                self._origin_label(ctx.channel),
            )
            try:
                await staff_channel.send(embed=embed)
            except discord.HTTPException as exc:
                log.error("Impossible d'envoyer l'avis anonyme au staff: %s", exc)
                await ctx.author.send("Une erreur est survenue en transmettant ton avis. Préviens le staff.")
                return
            try:
                await ctx.author.send(f"Merci ! Ton avis #{token} a été envoyé anonymement au staff.")
            except discord.HTTPException:
                pass
        finally:
            self.active_sessions.discard(ctx.author.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(AvisFeedback(bot))
