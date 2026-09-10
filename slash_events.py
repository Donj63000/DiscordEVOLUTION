# -*- coding: utf-8 -*-
"""
Slash‑command cog : événements (inscriptions) et votes à réactions.

• /event-rapide type:outing … → embed stylé + réactions ✅ ❔ ❌ + MAJ dynamique
• /event-rapide type:poll   … → bulletin de vote numéroté, décompte automatique
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import discord
from discord import app_commands
from discord.ext import commands

from utils import parse_duration, parse_fr_datetime

# --------------------------------------------------------------------------- #
# ------------------------------- CONSTANTS --------------------------------- #
# --------------------------------------------------------------------------- #

NUM_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

OUTING_EMOJIS = ("✅", "❔", "❌")

# --------------------------------------------------------------------------- #
# ------------------------------- DATA CLASS -------------------------------- #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OutingEvent:
    message_id: int
    channel_id: int                # 🔄 nécessaire pour fetch le message
    title: str
    date: Optional[datetime] = None
    seats: int = 0
    going: Set[int] = field(default_factory=set)
    maybe: Set[int] = field(default_factory=set)


@dataclass(slots=True)
class Poll:
    message_id: int
    channel_id: int
    options: List[str]
    closes_at: float                # timestamp Unix
    votes: Dict[str, Set[int]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# ---------------------------------- COG ------------------------------------ #
# --------------------------------------------------------------------------- #


class SlashEventCog(commands.Cog):
    """Gestion slash‑commands /event-rapide pour sorties et votes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.outings: Dict[int, OutingEvent] = {}
        self.polls: Dict[int, Poll] = {}

    # ================================ COMMAND =============================== #

    @app_commands.command(name="event-rapide", description="Proposer une sortie simple ou un vote à réactions.")
    @app_commands.describe(
        type="outing = inscription, poll = vote",
        titre="Intitulé ou (pour un vote) liste d'options séparées par |",
        quand="Ex : 'samedi 21h' ou 'dans 3 jours' (ignored pour poll)",
        duree_vote="Durée du vote, ex : '30m', '2h' (type=poll)",
        places="Nombre de places (type=outing)",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Sortie (inscription)", value="outing"),
            app_commands.Choice(name="Vote (choix)", value="poll"),
        ]
    )
    async def event_command(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        titre: str,
        quand: Optional[str] = None,
        duree_vote: Optional[str] = None,
        places: Optional[int] = None,
    ) -> None:
        if type.value == "outing":
            await self._create_outing(interaction, titre, quand, places)
        else:
            await self._create_poll(interaction, titre, duree_vote)

    # ============================ OUTING WORKFLOW ========================== #

    async def _create_outing(
        self,
        inter: discord.Interaction,
        titre: str,
        quand: Optional[str],
        places: Optional[int],
    ) -> None:
        date = parse_fr_datetime(quand) if quand else None

        desc = []
        if date:
            ts = int(date.timestamp())
            desc.append(f"**Quand :** <t:{ts}:F> • <t:{ts}:R>")
        desc.append(f"**Places :** {places if places else '∞'}")
        desc.append("")
        desc.append("**Participants :** *(aucun)*")

        embed = discord.Embed(
            title=f"📅 {titre}",
            description="\n".join(desc),
            colour=0x00AEEF,
        )

        await inter.response.send_message(embed=embed, ephemeral=False)
        msg = await inter.original_response()

        for emoji in OUTING_EMOJIS:
            try:
                await msg.add_reaction(emoji)
            except discord.Forbidden:
                pass

        ev = OutingEvent(
            message_id=msg.id,
            channel_id=msg.channel.id,
            title=titre,
            date=date,
            seats=places or 0,
        )
        self.outings[msg.id] = ev

    # ============================== POLL WORKFLOW ========================== #

    async def _create_poll(
        self,
        inter: discord.Interaction,
        titre: str,
        duree: Optional[str],
    ) -> None:
        options = [o.strip() for o in titre.split("|") if o.strip()]
        if not 2 <= len(options) <= 10:
            return await inter.response.send_message(
                "Donne **entre 2 et 10** options séparées par `|`.", ephemeral=True
            )

        duration = parse_duration(duree or "30m").total_seconds()
        closes_at = time.time() + duration

        body = "\n".join(f"{NUM_EMOJIS[i]} {opt}" for i, opt in enumerate(options))
        embed = discord.Embed(title="🗳️ Vote ouvert !", description=body, colour=0xF1C40F)
        embed.add_field(name="Clôture", value=f"<t:{int(closes_at)}:R>")

        await inter.response.send_message(embed=embed, ephemeral=False)
        msg = await inter.original_response()
        for i in range(len(options)):
            try:
                await msg.add_reaction(NUM_EMOJIS[i])
            except discord.Forbidden:
                pass

        poll = Poll(
            message_id=msg.id,
            channel_id=msg.channel.id,
            options=options,
            closes_at=closes_at,
        )
        self.polls[msg.id] = poll
        self.bot.loop.create_task(self._poll_timer(poll))

    # ========================= REACTION  LISTENERS ========================= #

    @commands.Cog.listener()  # add
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self.outings:
            await self._handle_outing_reaction(payload, add=True)
        elif payload.message_id in self.polls:
            self._handle_poll_reaction(payload, add=True)

    @commands.Cog.listener()  # remove
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self.outings:
            await self._handle_outing_reaction(payload, add=False)
        elif payload.message_id in self.polls:
            self._handle_poll_reaction(payload, add=False)

    # ------------------------ OUTING reaction logic ----------------------- #

    async def _handle_outing_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        ev = self.outings.get(payload.message_id)
        if not ev:
            return

        emoji, uid = str(payload.emoji), payload.user_id

        # gestion des ensembles
        if emoji == "✅":
            add and ev.going.add(uid) or ev.going.discard(uid)
            if add:
                ev.maybe.discard(uid)
        elif emoji == "❔":
            add and ev.maybe.add(uid) or ev.maybe.discard(uid)
            if add:
                ev.going.discard(uid)
        elif emoji == "❌" and add:
            ev.going.discard(uid)
            ev.maybe.discard(uid)

        # limite de places
        if ev.seats and len(ev.going) > ev.seats:
            # retire la réaction en trop
            channel = self.bot.get_channel(ev.channel_id)
            if not channel:
                return
            try:
                message = await channel.fetch_message(ev.message_id)
                user = await self.bot.fetch_user(uid)
                await message.remove_reaction("✅", user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            ev.going.discard(uid)
            return

        await self._update_outing_message(ev)

    async def _update_outing_message(self, ev: OutingEvent) -> None:
        channel = self.bot.get_channel(ev.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(ev.message_id)
        except discord.NotFound:
            return

        def fmt(users: Set[int]) -> str:
            return ", ".join(f"<@{u}>" for u in users) if users else "*(aucun)*"

        new_embed = msg.embeds[0].copy()
        new_embed.clear_fields()
        new_embed.add_field(name="Participants ✅", value=fmt(ev.going), inline=False)
        new_embed.add_field(name="Peut‑être ❔", value=fmt(ev.maybe), inline=False)

        await msg.edit(embed=new_embed)

    # -------------------------- POLL reaction logic ----------------------- #

    def _handle_poll_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        poll = self.polls.get(payload.message_id)
        if not poll:
            return
        emoji, uid = str(payload.emoji), payload.user_id
        if emoji not in NUM_EMOJIS[: len(poll.options)]:
            return

        if add:
            # un seul vote par personne
            for voters in poll.votes.values():
                voters.discard(uid)
            poll.votes.setdefault(emoji, set()).add(uid)
        else:
            poll.votes.get(emoji, set()).discard(uid)

    # -------------------------- POLL timer ------------------------------- #

    async def _poll_timer(self, poll: Poll) -> None:
        await asyncio.sleep(max(0, poll.closes_at - time.time()))
        # le poll peut avoir été supprimé
        if poll.message_id not in self.polls:
            return

        channel = self.bot.get_channel(poll.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._close_poll(channel, poll)
        self.polls.pop(poll.message_id, None)

    async def _close_poll(self, channel: discord.TextChannel, poll: Poll) -> None:
        try:
            msg = await channel.fetch_message(poll.message_id)
        except discord.NotFound:
            return

        counts = [
            (e, len(poll.votes.get(e, set())))
            for e in NUM_EMOJIS[: len(poll.options)]
        ]
        counts.sort(key=lambda t: t[1], reverse=True)

        result_lines = [f"{e} → {c} vote(s)" for e, c in counts]
        winner_emoji, _ = counts[0]
        winner_idx = NUM_EMOJIS.index(winner_emoji)

        embed = msg.embeds[0].copy()
        embed.title = "🗳️ Vote terminé !"
        embed.clear_fields()
        embed.add_field(name="Répartition", value="\n".join(result_lines), inline=False)
        embed.add_field(
            name="Gagnant",
            value=f"{winner_emoji} **{poll.options[winner_idx]}**",
            inline=False,
        )
        embed.colour = 0x2ECC71

        await msg.edit(embed=embed)
        await channel.send(f"✅ Option gagnante : **{poll.options[winner_idx]}**")

# --------------------------------------------------------------------------- #

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlashEventCog(bot))
