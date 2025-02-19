#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from collections import defaultdict

CHECK_INTERVAL_HOURS = 24
VOTE_DURATION_SECONDS = 3600
STAFF_ROLE_NAME = "Staff"
VALID_MEMBER_ROLE_NAME = "Membre validé d'Evolution"
INVITE_ROLE_NAME = "Invité"
VETERAN_ROLE_NAME = "Vétéran"
STAFF_CHANNEL_NAME = "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff"
MESSAGE_THRESHOLD = 20
JOINED_THRESHOLD_DAYS = 6 * 30 

class UpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_count = defaultdict(int)
        self.check_up_status.start()

    def cog_unload(self):
        self.check_up_status.cancel()

    @tasks.loop(hours=CHECK_INTERVAL_HOURS)
    async def check_up_status(self):
        await self.bot.wait_until_ready()
        await self.scan_entire_history()
        await self.verifier_membres_eligibles()

    async def scan_entire_history(self):
        self.user_message_count.clear()
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    async for msg in channel.history(limit=None, oldest_first=True):
                        if not msg.author.bot:
                            self.user_message_count[str(msg.author.id)] += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def verifier_membres_eligibles(self):

        for guild in self.bot.guilds:
            staff_channel = discord.utils.get(guild.text_channels, name=STAFF_CHANNEL_NAME)
            if not staff_channel:
                continue
            for member in guild.members:
                if member.bot:
                    continue
                join_days = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else 0
                has_valid_role = any(r.name == VALID_MEMBER_ROLE_NAME for r in member.roles)
                has_invite_role = any(r.name == INVITE_ROLE_NAME for r in member.roles)
                msg_count = self.user_message_count.get(str(member.id), 0)
                if (join_days >= JOINED_THRESHOLD_DAYS
                        and has_valid_role
                        and not has_invite_role
                        and msg_count >= MESSAGE_THRESHOLD):
                    if not any(r.name == VETERAN_ROLE_NAME for r in member.roles):
                        await self.lancer_vote(staff_channel, member)

    async def lancer_vote(self, staff_channel: discord.TextChannel, member: discord.Member):

        mention_staff_role = discord.utils.get(member.guild.roles, name=STAFF_ROLE_NAME)
        if mention_staff_role:
            mention_text = mention_staff_role.mention
        else:
            mention_text = "@Staff"
        embed = discord.Embed(
            title="Vote pour promotion Vétéran",
            description=(
                f"{mention_text}, le joueur **{member.display_name}** est éligible au rang **{VETERAN_ROLE_NAME}**.\n"
                f"- Ancienneté >= 6 mois\n"
                f"- Rôle '{VALID_MEMBER_ROLE_NAME}'\n"
                f"- Message count >= {MESSAGE_THRESHOLD}\n\n"
                "Réagissez ✅ pour valider la promotion, ❌ pour refuser.\n"
                "Le vote dure 1h. À l'issue, le bot agira selon la majorité."
            ),
            color=discord.Color.blue()
        )
        vote_message = await staff_channel.send(embed=embed)
        await vote_message.add_reaction("✅")
        await vote_message.add_reaction("❌")
        await asyncio.sleep(VOTE_DURATION_SECONDS)
        vote_message = await vote_message.channel.fetch_message(vote_message.id)
        yes_count = 0
        no_count = 0
        for reaction in vote_message.reactions:
            if str(reaction.emoji) == "✅":
                yes_count = reaction.count - 1
            elif str(reaction.emoji) == "❌":
                no_count = reaction.count - 1
        if yes_count > no_count:
            await self.promouvoir_veteran(staff_channel, member)
        else:
            await staff_channel.send(
                f"La promotion de **{member.display_name}** au rang **{VETERAN_ROLE_NAME}** a été refusée."
            )

    async def promouvoir_veteran(self, staff_channel: discord.TextChannel, member: discord.Member):
        """
        Assigne le rôle 'Vétéran' au membre, l'informe en MP, et avertit le staff.
        """
        veteran_role = discord.utils.get(member.guild.roles, name=VETERAN_ROLE_NAME)
        if not veteran_role:
            await staff_channel.send(
                "Le rôle 'Vétéran' n'existe pas sur ce serveur. Impossible de promouvoir."
            )
            return
        try:
            await member.add_roles(veteran_role)
            await member.send(
                f"Félicitations, vous êtes promu **{VETERAN_ROLE_NAME}** !\n"
                "Vous disposez désormais du droit de recruter pour la guilde, etc.\n"
                "Merci de respecter les règles et de représenter la guilde avec fierté."
            )
            await staff_channel.send(
                f"Le membre **{member.display_name}** a été promu **{VETERAN_ROLE_NAME}**."
            )
        except discord.Forbidden:
            await staff_channel.send(
                f"Permissions insuffisantes pour promouvoir {member.display_name}."
            )
        except discord.HTTPException as e:
            await staff_channel.send(
                f"Erreur lors de la promotion de {member.display_name} : {e}"
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(UpCog(bot))
