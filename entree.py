#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import timezone
from typing import Any

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WELCOME_CHANNEL_FALLBACK = os.getenv("WELCOME_CHANNEL_NAME") or "𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞"
GENERAL_CHANNEL_FALLBACK = os.getenv("GENERAL_CHANNEL_NAME") or "📄 Général 📄"
ENTREE_IMAGE_PATH = os.getenv("ENTREE_IMAGE_PATH", os.path.join(BASE_DIR, "entree.png"))
QUITTER_IMAGE_PATH = os.getenv("QUITTER_IMAGE_PATH", os.path.join(BASE_DIR, "quitter.png"))
ENTREE_PUBLIC_JOIN_ENABLED = (os.getenv("ENTREE_PUBLIC_JOIN_ENABLED", "1") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
ENTREE_PUBLIC_LEAVE_ENABLED = (os.getenv("ENTREE_PUBLIC_LEAVE_ENABLED", "1") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
EVERYONE_HERE_RE = re.compile(r"@(?=everyone\b|here\b)", re.IGNORECASE)
DISCORD_MENTION_RE = re.compile(r"<(@!?|@&|#)\d+>")


def _sanitize_display_text(value: str | None, *, default: str = "inconnu", max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip()
    text = CONTROL_CHARS_RE.sub("", text)
    text = EVERYONE_HERE_RE.sub("@\u200b", text)
    text = DISCORD_MENTION_RE.sub("[mention retirée]", text)
    text = text.replace("`", "ʼ")
    text = " ".join(text.split())
    if max_len > 0 and len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text or default


def _format_membership_duration(member: discord.Member) -> str:
    joined_at = getattr(member, "joined_at", None)
    if joined_at is None:
        return "durée inconnue"

    if joined_at.tzinfo is None:
        joined_at = joined_at.replace(tzinfo=timezone.utc)

    duration = discord.utils.utcnow() - joined_at
    total_seconds = max(int(duration.total_seconds()), 0)

    days, remainder = divmod(total_seconds, 24 * 3600)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} jour{'s' if days > 1 else ''}")
    if hours:
        parts.append(f"{hours} heure{'s' if hours > 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")

    return ", ".join(parts) if parts else "moins d’une minute"


class EntreeCog(commands.Cog):
    """Annonces publiques d'arrivée et de départ.

    Le parcours MP détaillé reste dans `welcome.py`. Ce cog ne fait que publier
    un message public propre, avec image optionnelle et mentions maîtrisées.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _resolve_public_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel = resolve_text_channel(
            guild,
            id_env="WELCOME_CHANNEL_ID",
            name_env="WELCOME_CHANNEL_NAME",
            default_name=WELCOME_CHANNEL_FALLBACK,
        )
        if channel is not None:
            return channel
        return resolve_text_channel(
            guild,
            id_env="GENERAL_CHANNEL_ID",
            name_env="GENERAL_CHANNEL_NAME",
            default_name=GENERAL_CHANNEL_FALLBACK,
        )

    async def _send_embed_with_optional_image(
        self,
        channel: discord.TextChannel,
        *,
        content: str | None,
        embed: discord.Embed,
        image_path: str,
        image_filename: str,
        allowed_mentions: discord.AllowedMentions,
    ) -> None:
        kwargs: dict[str, Any] = {
            "content": content,
            "embed": embed,
            "allowed_mentions": allowed_mentions,
        }

        if os.path.isfile(image_path):
            file = discord.File(image_path, filename=image_filename)
            embed.set_image(url=f"attachment://{image_filename}")
            kwargs["file"] = file
        else:
            log.debug("Image d'annonce introuvable: %s", image_path)

        try:
            await channel.send(**kwargs)
        except discord.Forbidden:
            log.warning("Permissions insuffisantes pour envoyer l'annonce dans #%s.", getattr(channel, "name", channel.id))
        except discord.HTTPException as exc:
            log.warning("Erreur Discord lors de l'envoi de l'annonce dans #%s: %s", getattr(channel, "name", channel.id), exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not ENTREE_PUBLIC_JOIN_ENABLED:
            return

        public_channel = self._resolve_public_channel(member.guild)
        if public_channel is None:
            log.warning("Aucun salon public d'accueil trouvé pour guild=%s.", getattr(member.guild, "id", "unknown"))
            return

        display_name = _sanitize_display_text(member.display_name, default="nouveau membre", max_len=80)
        embed = discord.Embed(
            title="Un nouveau joueur vient de nous rejoindre !",
            description=(
                f"Bienvenue à **{display_name}** sur le serveur **Evolution**.\n\n"
                "Le bot va lui envoyer un message privé pour finaliser son accès. "
                "S’il a désactivé ses MP, le staff pourra relancer l’accueil avec `!accueil relance @membre`."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Accueil public automatique")

        await self._send_embed_with_optional_image(
            public_channel,
            content=f"🎉 Bienvenue {member.mention} !",
            embed=embed,
            image_path=ENTREE_IMAGE_PATH,
            image_filename="entree.png",
            allowed_mentions=discord.AllowedMentions(
                users=[member],
                roles=False,
                everyone=False,
                replied_user=False,
            ),
        )
        log.debug("Annonce d'arrivée envoyée pour member=%s.", member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot or not ENTREE_PUBLIC_LEAVE_ENABLED:
            return

        public_channel = self._resolve_public_channel(member.guild)
        if public_channel is None:
            log.warning("Aucun salon public d'accueil trouvé pour guild=%s.", getattr(member.guild, "id", "unknown"))
            return

        display_name = _sanitize_display_text(member.display_name, default="membre inconnu", max_len=80)
        duration_text = _format_membership_duration(member)

        embed = discord.Embed(
            title="Un joueur nous quitte",
            description=(
                f"**{display_name}** a quitté le serveur.\n"
                f"Présence sur le serveur : **{duration_text}**.\n\n"
                "Nous lui souhaitons une bonne continuation."
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Discord ID: {member.id}")

        await self._send_embed_with_optional_image(
            public_channel,
            content=None,
            embed=embed,
            image_path=QUITTER_IMAGE_PATH,
            image_filename="quitter.png",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        log.debug("Annonce de départ envoyée pour member=%s.", member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EntreeCog(bot))
