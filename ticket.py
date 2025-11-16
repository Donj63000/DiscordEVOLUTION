#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import datetime

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel
from utils.ticket_text import (
    TICKET_FIELD_MAX_CHARS,
    format_ticket_body,
)

log = logging.getLogger("ticket")

open_tickets: set[int] = set()
TICKET_CHANNEL_FALLBACK = os.getenv("TICKET_CHANNEL_NAME") or "🎫 ticket 🎫"
DM_TIMEOUT_SECONDS = float(os.getenv("TICKET_DM_TIMEOUT", "300"))


def _upsert_field(embed: discord.Embed, *, name: str, value: str, inline: bool = True) -> None:
    """Update (or insert) a field while preserving order."""
    target = name.lower()
    for index, field in enumerate(embed.fields):
        if field.name.lower() == target:
            embed.set_field_at(index, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


class TicketView(discord.ui.View):
    def __init__(self, author: discord.User, staff_role_name: str):
        super().__init__(timeout=None)
        self.author = author
        self.staff_role_name = staff_role_name
        self.taken_by: discord.abc.User | None = None
        self.pending_reply: str | None = None

    @discord.ui.button(label="Prendre en charge ✅", style=discord.ButtonStyle.primary, custom_id="ticket:take")
    async def take_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ Cette action ne peut pas être réalisée en DM.", ephemeral=True)
        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send("❌ Vous ne disposez pas des autorisations requises (rôle 'Staff') pour prendre en charge ce ticket.", ephemeral=True)
        if self.taken_by is not None:
            return await interaction.followup.send("⚠️ Ce ticket est déjà en cours de traitement par un autre membre du staff.", ephemeral=True)

        message = interaction.message
        if not message or not message.embeds:
            return await interaction.followup.send("⚠️ Impossible de mettre à jour ce ticket car l'embed original est introuvable.", ephemeral=True)

        embed = message.embeds[0]
        _upsert_field(embed, name="Statut", value="En cours 🔄", inline=True)
        _upsert_field(embed, name="Pris en charge par", value=interaction.user.display_name, inline=False)
        embed.color = discord.Color.gold()
        self.taken_by = interaction.user
        button.disabled = True
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("Le ticket a été pris en charge avec succès. 🛠️", ephemeral=True)

    @discord.ui.button(label="Résolu ✅", style=discord.ButtonStyle.success, custom_id="ticket:resolve")
    async def mark_resolved(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ Cette action ne peut pas être réalisée en DM.", ephemeral=True)
        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send("❌ Vous n'êtes pas autorisé à modifier le statut de ce ticket (rôle 'Staff' requis).", ephemeral=True)
        if self.taken_by is None:
            return await interaction.followup.send("⚠️ Veuillez d'abord prendre en charge le ticket avant de le marquer comme résolu.", ephemeral=True)
        message = interaction.message
        if not message or not message.embeds:
            return await interaction.followup.send("⚠️ Impossible de mettre à jour ce ticket car l'embed original est introuvable.", ephemeral=True)
        embed = message.embeds[0]
        _upsert_field(embed, name="Statut", value="Résolu ✅", inline=True)
        embed.color = discord.Color.green()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=embed, view=self)

        await interaction.followup.send(
            "Écris le message de réponse au joueur (ou `annuler`). Ce message sera envoyé en MP avec la confirmation.",
            ephemeral=True,
        )
        try:
            followup = await interaction.client.wait_for(
                "message",
                timeout=180,
                check=lambda m: m.author == interaction.user and m.channel == interaction.channel,
            )
        except asyncio.TimeoutError:
            self.pending_reply = None
        else:
            content = (followup.content or "").strip()
            if content.lower() == "annuler" or not content:
                self.pending_reply = None
            else:
                self.pending_reply = content

        open_tickets.discard(self.author.id)
        try:
            base_msg = f"Votre ticket a été résolu par le staff : **{self.taken_by.display_name}**.\nNous vous remercions pour votre patience."
            if self.pending_reply:
                base_msg += f"\n\nRéponse du staff :\n{self.pending_reply}"
            await self.author.send(base_msg)
        except discord.Forbidden:
            pass
        await interaction.followup.send(f"Le ticket de {self.author.mention} a été marqué comme résolu. ✅", ephemeral=True)
        self.stop()


class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot, staff_role_name: str = "Staff"):
        self.bot = bot
        self.staff_role_name = staff_role_name

    @commands.command(name="ticket")
    async def create_ticket(self, ctx: commands.Context):
        log.info("Commande !ticket appelée par %s (id=%s)", ctx.author, ctx.author.id)
        if ctx.guild is None:
            return

        user = ctx.author
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            log.debug("Impossible de supprimer le message !ticket de %s", user)

        if user.id in open_tickets:
            try:
                await user.send("Vous avez déjà un ticket en cours. Merci de patienter jusqu'à sa résolution avant d'en ouvrir un autre.")
            except discord.Forbidden:
                pass
            return

        open_tickets.add(user.id)
        try:
            timeout_text = (
                f"{int(DM_TIMEOUT_SECONDS // 60)} minutes"
                if DM_TIMEOUT_SECONDS >= 120
                else f"{int(DM_TIMEOUT_SECONDS)} secondes"
            )
            await user.send(
                "Bonjour ! Tu viens d'ouvrir un ticket de support.\n"
                "Explique ton souci ou ta demande en donnant un maximum de détails (étapes, pseudos, captures, etc.).\n"
                f"*(Tu disposes de {timeout_text} pour répondre à ce message.)*"
            )
        except discord.Forbidden:
            open_tickets.discard(user.id)
            await ctx.send(f"{user.mention}, impossible de créer le ticket car tu bloques les messages privés.")
            return

        def check_dm(message: discord.Message) -> bool:
            return message.author == user and isinstance(message.channel, discord.DMChannel)

        try:
            dm_message = await self.bot.wait_for("message", timeout=DM_TIMEOUT_SECONDS, check=check_dm)
        except asyncio.TimeoutError:
            open_tickets.discard(user.id)
            try:
                await user.send("Temps écoulé. Votre ticket a été annulé.")
            except discord.Forbidden:
                pass
            return

        ticket_content = format_ticket_body(dm_message)
        if not ticket_content:
            open_tickets.discard(user.id)
            try:
                await user.send(
                    "Ton message semble vide. Réponds avec du texte ou ajoute une pièce jointe accessible, puis relance `!ticket`."
                )
            except discord.Forbidden:
                pass
            return

        embed = discord.Embed(
            title="🎟 Nouveau Ticket",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Demandeur", value=user.display_name, inline=True)
        embed.add_field(name="Statut", value="En attente ✅", inline=True)
        embed.add_field(name="Contenu du ticket", value=ticket_content, inline=False)

        ticket_channel = resolve_text_channel(
            ctx.guild,
            id_env="TICKET_CHANNEL_ID",
            name_env="TICKET_CHANNEL_NAME",
            default_name=TICKET_CHANNEL_FALLBACK,
        )
        if ticket_channel is None:
            open_tickets.discard(user.id)
            try:
                await user.send("❌ Le ticket n'a pu être créé car le salon d'assistance est introuvable.")
            except discord.Forbidden:
                pass
            log.error("Salon de ticket introuvable pour la guilde %s", ctx.guild.id)
            return

        view = TicketView(user, self.staff_role_name)
        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        mention_staff = staff_role.mention if staff_role else "**[Staff non trouvé]**"
        try:
            await ticket_channel.send(content=f"{mention_staff}, un nouveau ticket a été ouvert !", embed=embed, view=view)
        except discord.HTTPException as exc:
            log.error("Impossible d'envoyer le ticket dans #%s: %s", getattr(ticket_channel, "name", ticket_channel.id), exc)
            open_tickets.discard(user.id)
            try:
                await user.send("Une erreur est survenue lors de l'envoi de ton ticket. Merci de réessayer plus tard ou de prévenir le staff.")
            except discord.Forbidden:
                pass
            return

        try:
            await user.send("Votre ticket a été envoyé au staff avec succès. Vous serez recontacté une fois qu'il sera pris en charge.")
        except discord.Forbidden:
            pass
        log.info("Ticket créé par %s et envoyé dans #%s", user, getattr(ticket_channel, "name", ticket_channel.id))

    @commands.command(name="staff")
    async def staff_list(self, ctx: commands.Context):
        if not self.bot.intents.members:
            return await ctx.send("❌ L'intent 'members' n'est pas activé. Veuillez l'activer pour utiliser cette commande.")
        try:
            members = [member async for member in ctx.guild.fetch_members(limit=None)]
        except discord.HTTPException as exc:
            return await ctx.send(f"Erreur lors de la récupération des membres : {exc}")
        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        if staff_role is None:
            return await ctx.send(f"Le rôle '{self.staff_role_name}' est introuvable sur ce serveur.")
        staff_members = [member for member in members if staff_role in member.roles]
        if not staff_members:
            return await ctx.send("Aucun membre ne détient le rôle 'Staff'.")
        lines = [f"- {member.mention} (ID: {member.id})" for member in staff_members]
        description = "\n".join(lines)
        embed = discord.Embed(title="Liste des membres du Staff", description=description, color=discord.Color.blue())
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot, staff_role_name="Staff"))
