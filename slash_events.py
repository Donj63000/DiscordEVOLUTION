# -*- coding: utf-8 -*-
"""Slash command cog for events and polls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, List

import discord
from discord.ext import commands
from discord import app_commands

from utils import parse_duration, parse_french_datetime

NUM_EMOJIS = [
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "4️⃣",
    "5️⃣",
    "6️⃣",
    "7️⃣",
    "8️⃣",
    "9️⃣",
    "🔟",
]


@dataclass
class OutingEvent:
    message_id: int
    guild_id: int
    title: str
    date: Optional[discord.utils.datetime] = None
    seats: int = 0
    going: Set[int] = field(default_factory=set)
    maybe: Set[int] = field(default_factory=set)


@dataclass
class Poll:
    message_id: int
    guild_id: int
    options: List[str]
    closes_at: float
    votes: Dict[str, Set[int]] = field(default_factory=dict)


class SlashEventCog(commands.Cog):
    """Slash command based event and poll management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.outings: Dict[int, OutingEvent] = {}
        self.polls: Dict[int, Poll] = {}

    # ---- slash command ----
    @app_commands.command(name="event", description="Propose une sortie ou un vote")
    @app_commands.describe(
        type="outing = inscription, poll = vote",
        titre="Intitulé ou liste d'options séparées par |",
        quand="Ex : 'samedi 21h' ou 'dans 2 jours' (ignorer pour un vote)",
        duree_vote="Durée du vote, ex : '30m', '2h' (pour type=poll)",
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

    # ---- outing ----
    async def _create_outing(
        self,
        inter: discord.Interaction,
        titre: str,
        quand: Optional[str],
        places: Optional[int],
    ) -> None:
        date = None
        if quand:
            date = parse_french_datetime(quand)
        desc_lines = []
        if date:
            ts = int(date.timestamp())
            desc_lines.append(f"**Quand :** <t:{ts}:F> (<t:{ts}:R>)")
        if places:
            desc_lines.append(f"**Places :** {places}")
        else:
            desc_lines.append("**Places :** ∞")
        desc_lines.append("\n**Participants :** (aucun)")
        embed = discord.Embed(title=f"📅 {titre}", description="\n".join(desc_lines), colour=0x00AEEF)
        await inter.response.send_message(embed=embed)
        msg = await inter.original_response()
        for emoji in ("✅", "❔", "❌"):
            await msg.add_reaction(emoji)
        ev = OutingEvent(message_id=msg.id, guild_id=msg.guild.id, title=titre, date=date, seats=places or 0)
        self.outings[msg.id] = ev

    # ---- poll ----
    async def _create_poll(
        self,
        inter: discord.Interaction,
        titre: str,
        duree: Optional[str],
    ) -> None:
        options = [opt.strip() for opt in titre.split("|") if opt.strip()]
        if len(options) < 2 or len(options) > 10:
            await inter.response.send_message("Donne entre 2 et 10 options séparées par |", ephemeral=True)
            return
        duration = parse_duration(duree or "30m").total_seconds()
        closes_at = self.bot.loop.time() + duration
        lines = [f"{NUM_EMOJIS[i]} {opt}" for i, opt in enumerate(options)]
        embed = discord.Embed(title="🗳️ Vote ouvert !", description="\n".join(lines), colour=0xF1C40F)
        embed.add_field(name="Clôture", value=f"<t:{int(discord.utils.utcnow().timestamp()+duration)}:R>")
        await inter.response.send_message(embed=embed)
        msg = await inter.original_response()
        for i in range(len(options)):
            await msg.add_reaction(NUM_EMOJIS[i])
        poll = Poll(message_id=msg.id, guild_id=msg.guild.id, options=options, closes_at=closes_at)
        self.polls[msg.id] = poll
        self.bot.loop.create_task(self._poll_timer(msg.channel, poll))

    # ---- reaction listeners ----
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self.outings:
            await self._handle_outing_reaction(payload, True)
        elif payload.message_id in self.polls:
            self._handle_poll_reaction(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self.outings:
            await self._handle_outing_reaction(payload, False)
        elif payload.message_id in self.polls:
            self._handle_poll_reaction(payload, False)

    async def _handle_outing_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        ev = self.outings.get(payload.message_id)
        if not ev:
            return
        emoji = str(payload.emoji)
        uid = payload.user_id
        if emoji == "✅":
            if add:
                ev.going.add(uid)
                ev.maybe.discard(uid)
            else:
                ev.going.discard(uid)
        elif emoji == "❔":
            if add:
                ev.maybe.add(uid)
                ev.going.discard(uid)
            else:
                ev.maybe.discard(uid)
        elif emoji == "❌" and add:
            ev.going.discard(uid)
            ev.maybe.discard(uid)
        await self._update_outing_message(ev)

    async def _update_outing_message(self, ev: OutingEvent) -> None:
        channel = self.bot.get_channel(ev.guild_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(ev.message_id)
        except discord.NotFound:
            return
        def fmt(ids: Set[int]) -> str:
            return ", ".join(f"<@{i}>" for i in ids) if ids else "*(aucun)*"
        embed = msg.embeds[0]
        fields = [
            ("Participants ✅", fmt(ev.going)),
            ("Peut-être ❔", fmt(ev.maybe)),
        ]
        new_embed = embed.copy()
        new_embed.clear_fields()
        for name, value in fields:
            new_embed.add_field(name=name, value=value, inline=False)
        await msg.edit(embed=new_embed)

    def _handle_poll_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        poll = self.polls.get(payload.message_id)
        if not poll:
            return
        emoji = str(payload.emoji)
        if emoji not in NUM_EMOJIS:
            return
        uid = payload.user_id
        if add:
            for s in poll.votes.values():
                s.discard(uid)
            poll.votes.setdefault(emoji, set()).add(uid)
        else:
            poll.votes.get(emoji, set()).discard(uid)

    async def _poll_timer(self, channel: discord.TextChannel, poll: Poll) -> None:
        await asyncio.sleep(max(0, poll.closes_at - self.bot.loop.time()))
        if poll.message_id not in self.polls:
            return
        await self._close_poll(channel, poll)
        self.polls.pop(poll.message_id, None)

    async def _close_poll(self, channel: discord.TextChannel, poll: Poll) -> None:
        try:
            msg = await channel.fetch_message(poll.message_id)
        except discord.NotFound:
            return
        counts = [(e, len(poll.votes.get(e, set()))) for e in NUM_EMOJIS[: len(poll.options)]]
        counts.sort(key=lambda x: x[1], reverse=True)
        desc = "\n".join(f"{e} → {c} vote(s)" for e, c in counts)
        embed = msg.embeds[0].copy()
        embed.title = "🗳️ Vote terminé !"
        embed.clear_fields()
        embed.add_field(name="Répartition", value=desc, inline=False)
        if counts:
            winner_emoji, _ = counts[0]
            idx = NUM_EMOJIS.index(winner_emoji)
            embed.add_field(name="Gagnant", value=f"{winner_emoji} **{poll.options[idx]}**", inline=False)
        embed.colour = 0x2ECC71
        await msg.edit(embed=embed)
        await channel.send(f"✅ Option gagnante : **{poll.options[idx]}**")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlashEventCog(bot))

