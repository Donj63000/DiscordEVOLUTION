#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Gestion du statut des percepteurs via une commande slash."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands
from utils.channel_resolver import resolve_text_channel
from utils.discord_history import fetch_channel_history

log = logging.getLogger(__name__)

STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
ANNOUNCE_CHANNEL_FALLBACK = os.getenv("ANNONCE_CHANNEL_NAME") or "annonces"
CONSOLE_CHANNEL_FALLBACK = os.getenv("CHANNEL_CONSOLE") or "console"
PERCO_TAG = "===PERCO==="

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_CONFIG = {
    "good": {
        "title": "Percepteurs : situation normale",
        "colour": 0x2ECC71,
        "check_message": (
            "✅ Les percepteurs ne sont pas saturés. Vous pouvez gérer les poses et les défenses "
            "avec souplesse tant que cela reste raisonnable et conforme au règlement."
        ),
        "announcement": (
            "✨ **Bonne nouvelle !**\n"
            "Les percepteurs repassent en **GOOD**.\n\n"
            "🔹 Vous pouvez reprendre une gestion plus souple des poses.\n"
            "🔹 Continuez à respecter le règlement de guilde.\n"
            "🔹 Prévenez le staff en cas de doute."
        ),
        "content": "@everyone ✨ **Percepteurs — Situation GOOD** ✨",
        "extra_fields": (
            {
                "name": "Rappel",
                "value": "Restez vigilants et coordonnés avec vos camarades pour éviter une nouvelle saturation.",
                "inline": False,
            },
        ),
        "image": os.path.join(BASE_DIR, "perco1.png"),
    },
    "full": {
        "title": "Percepteurs : saturation",
        "colour": 0xE74C3C,
        "check_message": (
            "🚨 Les percepteurs sont actuellement saturés. Merci d'appliquer les règles spécifiques "
            "aux percepteurs (poses limitées, défense organisée, etc.)."
        ),
        "announcement": (
            "🚨 **Alerte saturation !**\n"
            "Les percepteurs passent en mode **FULL**.\n\n"
            "⚠️ Merci de respecter immédiatement les consignes suivantes :\n"
            "• Limitez les nouvelles poses.\n"
            "• Organisez les défenses prioritairement.\n"
            "• Tenez le staff informé de toute attaque."
        ),
        "content": "@everyone 🚨 **Percepteurs — MODE FULL** 🚨",
        "extra_fields": (
            {
                "name": "Consignes prioritaires",
                "value": "Communication rapide et coordination sont essentielles pour protéger nos percepteurs.",
                "inline": False,
            },
        ),
        "image": os.path.join(BASE_DIR, "perco2.png"),
    },
}


@dataclass(slots=True)
class PercoState:
    status: str = "good"
    updated_by: Optional[int] = None
    updated_at: Optional[int] = None
    console_message_id: Optional[int] = None


class PercoCog(commands.Cog):
    """Permet de consulter et mettre à jour le statut des percepteurs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: Dict[int, PercoState] = {}
        self._load_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._load_task = self.bot.loop.create_task(self._initial_load())

    async def cog_unload(self) -> None:
        if self._load_task:
            self._load_task.cancel()

    async def _initial_load(self) -> None:
        try:
            await self.bot.wait_until_ready()
            for guild in self.bot.guilds:
                await self._load_guild_status(guild)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - simple log
            log.warning("Chargement initial du statut perco échoué: %s", exc, exc_info=True)

    async def _load_guild_status(self, guild: discord.Guild) -> PercoState:
        state = self.states.get(guild.id)
        if state is None:
            state = PercoState()
            self.states[guild.id] = state

        channel = resolve_text_channel(
            guild,
            id_env="CHANNEL_CONSOLE_ID",
            name_env="CHANNEL_CONSOLE",
            default_name=CONSOLE_CHANNEL_FALLBACK,
        )
        if not channel:
            log.info(
                "Salon #%s introuvable sur %s pour charger le statut perco.",
                CONSOLE_CHANNEL_FALLBACK,
                guild.name,
            )
            return state

        limit = max(
            int(os.getenv("PERCO_HISTORY_LIMIT", os.getenv("CONSOLE_HISTORY_LIMIT", "200"))),
            0,
        )
        messages = await fetch_channel_history(channel, limit=limit, reason="perco.console")
        for msg in messages:
            if msg.author == self.bot.user and msg.content.startswith(PERCO_TAG):
                payload = self._extract_payload(msg.content)
                if payload:
                    status = payload.get("status")
                    if status not in STATUS_CONFIG:
                        status = "good"
                    state.status = status
                    state.updated_by = payload.get("updated_by")
                    state.updated_at = payload.get("updated_at")
                else:
                    parts = msg.content.split()
                    if len(parts) >= 2 and parts[1] in STATUS_CONFIG:
                        state.status = parts[1]
                state.console_message_id = msg.id
                break
        return state

    @staticmethod
    def _extract_payload(content: str) -> Optional[Dict[str, int]]:
        if "```json" not in content:
            return None
        try:
            start = content.index("```json") + len("```json")
            end = content.index("```", start)
            raw = content[start:end].strip()
            return json.loads(raw)
        except Exception:
            return None

    async def _ensure_state(self, guild: discord.Guild) -> PercoState:
        state = self.states.get(guild.id)
        if state is None:
            state = PercoState()
            self.states[guild.id] = state
            await self._load_guild_status(guild)
            state = self.states[guild.id]
        return state

    def _is_staff(self, user: discord.abc.User) -> bool:
        if isinstance(user, discord.Member):
            return any(role.name == STAFF_ROLE_NAME for role in user.roles)
        return False

    async def _apply_status_update(
        self,
        guild: discord.Guild,
        state: PercoState,
        user: discord.abc.User,
        new_status: str,
    ) -> tuple[discord.Embed, list[str]]:
        state.status = new_status
        state.updated_by = getattr(user, "id", None)
        state.updated_at = int(time.time())

        announced_channel = await self._announce_status(guild, state)
        stored_channel = await self._store_status(guild, state)
        embed = self._build_status_embed(guild, state)

        notes: list[str] = []
        if announced_channel:
            notes.append(f"Annonce publiée dans {announced_channel.mention}.")
        else:
            notes.append("Impossible de publier l'annonce (voir logs).")
        if stored_channel:
            notes.append(f"Statut sauvegardé dans {stored_channel.mention}.")
        else:
            notes.append("Statut non sauvegardé dans #console.")

        return embed, notes

    def _build_status_embed(self, guild: discord.Guild, state: PercoState) -> discord.Embed:
        config = STATUS_CONFIG.get(state.status, STATUS_CONFIG["good"])
        embed = discord.Embed(
            title=config["title"],
            description=config["check_message"],
            colour=discord.Colour(config["colour"]),
        )
        if state.updated_at:
            when = f"<t:{state.updated_at}:F> • <t:{state.updated_at}:R>"
        else:
            when = "Inconnue"
        if state.updated_by:
            member = guild.get_member(state.updated_by)
            who = member.mention if member else f"<@{state.updated_by}>"
        else:
            who = "Inconnu"
        embed.add_field(name="Dernière mise à jour", value=f"{when}\nPar : {who}", inline=False)
        embed.set_footer(text="Statut des percepteurs")
        return embed

    async def _announce_status(self, guild: discord.Guild, state: PercoState) -> Optional[discord.TextChannel]:
        channel = resolve_text_channel(
            guild,
            id_env="ANNONCE_CHANNEL_ID",
            name_env="ANNONCE_CHANNEL_NAME",
            default_name=ANNOUNCE_CHANNEL_FALLBACK,
        )
        if not channel:
            log.warning(
                "Salon #%s introuvable sur %s pour publier le statut perco.",
                ANNOUNCE_CHANNEL_FALLBACK,
                guild.name,
            )
            return None

        config = STATUS_CONFIG.get(state.status, STATUS_CONFIG["good"])
        embed = discord.Embed(
            title=config["title"],
            description=config["announcement"],
            colour=discord.Colour(config["colour"]),
        )
        for field in config.get("extra_fields", ()): 
            embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
        if state.updated_by:
            member = guild.get_member(state.updated_by)
            if member:
                embed.set_footer(text=f"Mise à jour par {member.display_name}")
            else:
                embed.set_footer(text=f"Mise à jour par {state.updated_by}")

        file = None
        image_path = config.get("image")
        if image_path and os.path.exists(image_path):
            filename = os.path.basename(image_path)
            file = discord.File(image_path, filename=filename)
            embed.set_image(url=f"attachment://{filename}")

        content = config.get("content")
        try:
            if file:
                await channel.send(content=content, embed=embed, file=file)
            else:
                await channel.send(content=content, embed=embed)
            return channel
        except discord.Forbidden:
            log.warning("Permissions insuffisantes pour envoyer le statut perco dans #%s.", channel.name)
        except discord.HTTPException as exc:
            log.warning("Impossible d'envoyer l'annonce perco: %s", exc)
        return None

    async def _store_status(self, guild: discord.Guild, state: PercoState) -> Optional[discord.TextChannel]:
        channel = resolve_text_channel(
            guild,
            id_env="CHANNEL_CONSOLE_ID",
            name_env="CHANNEL_CONSOLE",
            default_name=CONSOLE_CHANNEL_FALLBACK,
        )
        if not channel:
            log.warning(
                "Salon #%s introuvable sur %s pour sauvegarder le statut perco.",
                CONSOLE_CHANNEL_FALLBACK,
                guild.name,
            )
            return None

        payload = {
            "status": state.status,
            "updated_by": state.updated_by,
            "updated_at": state.updated_at,
        }
        content = f"{PERCO_TAG}\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"

        if state.console_message_id:
            try:
                message = await channel.fetch_message(state.console_message_id)
                await message.edit(content=content)
                return channel
            except (discord.NotFound, discord.Forbidden):
                state.console_message_id = None
            except discord.HTTPException as exc:
                log.warning("Échec de mise à jour du message console perco: %s", exc)
                return None

        try:
            message = await channel.send(content)
            state.console_message_id = message.id
            return channel
        except discord.Forbidden:
            log.warning("Permissions insuffisantes pour écrire dans #%s.", channel.name)
        except discord.HTTPException as exc:
            log.warning("Impossible d'envoyer le statut perco dans la console: %s", exc)
        return None

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._load_guild_status(guild)

    @app_commands.command(name="perco", description="Voir le statut des percepteurs ; le Staff peut le modifier.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.checks.cooldown(2, 10, key=lambda interaction: (interaction.guild_id, interaction.user.id))
    @app_commands.describe(
        etat="Laisser vide pour consulter. Choisir 'good' ou 'full' pour mettre à jour (staff uniquement)."
    )
    @app_commands.choices(
        etat=[
            app_commands.Choice(name="Disponibles (good)", value="good"),
            app_commands.Choice(name="Pleins (full)", value="full"),
        ]
    )
    async def perco_command(
        self,
        interaction: discord.Interaction,
        etat: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans un serveur.", ephemeral=True
            )
            return

        if etat is not None and not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Seuls les membres du Staff peuvent changer le statut des percepteurs.", ephemeral=True,
            )
            return
        if etat is not None and etat.value not in STATUS_CONFIG:
            await interaction.response.send_message("Choisis un statut proposé par Discord.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=etat is not None)
        state = await self._ensure_state(guild)
        if etat is None:
            await interaction.followup.send(embed=self._build_status_embed(guild, state))
            return
        if etat.value == state.status:
            await interaction.followup.send("Ce statut est déjà enregistré.", ephemeral=True)
            return
        embed, notes = await self._apply_status_update(guild, state, interaction.user, etat.value)
        await interaction.followup.send("\n".join(notes), embed=embed, ephemeral=True)

    @commands.command(name="perco")
    async def perco_prefix_command(self, ctx: commands.Context, etat: Optional[str] = None) -> None:
        guild = ctx.guild
        if guild is None:
            await ctx.reply("Cette commande doit être utilisée dans un serveur.")
            return

        state = await self._ensure_state(guild)

        if etat is None:
            embed = self._build_status_embed(guild, state)
            await ctx.send(embed=embed)
            return

        if not self._is_staff(ctx.author):
            await ctx.reply("Seuls les membres du staff peuvent changer le statut des percepteurs.")
            return

        new_status = etat.lower()
        if new_status not in STATUS_CONFIG:
            await ctx.reply("Statut inconnu. Choisissez `good` ou `full`.")
            return

        if new_status == state.status:
            await ctx.reply(f"Le statut des percepteurs est déjà `{new_status}`.")
            return

        embed, notes = await self._apply_status_update(guild, state, ctx.author, new_status)
        await ctx.send("\n".join(notes), embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PercoCog(bot))
