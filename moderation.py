#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import datetime
import discord
from discord.ext import commands

WARNINGS_FILE = os.path.join(os.path.dirname(__file__), "warnings_data.json")
STAFF_CHANNEL_NAME = "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff"
STAFF_ROLE_NAME = "Staff"

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.warnings = {}
        self.load_warnings()

    def load_warnings(self):
        if os.path.isfile(WARNINGS_FILE):
            try:
                with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
                    self.warnings = json.load(f)
            except Exception as e:
                print(f"[Moderation] Erreur chargement {WARNINGS_FILE}: {e}")

    def save_warnings(self):
        try:
            with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.warnings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Moderation] Erreur sauvegarde {WARNINGS_FILE}: {e}")

    def increment_warning(self, user_id: str) -> int:
        self.warnings.setdefault(user_id, 0)
        self.warnings[user_id] += 1
        self.save_warnings()
        return self.warnings[user_id]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ia_cog = self.bot.get_cog("IACog")
        if ia_cog is None:
            return

        intent = ia_cog.detect_intention(message.content)
        if intent not in ("serious_insult", "discrimination", "threat"):
            return

        try:
            await message.delete()
        except discord.DiscordException:
            pass

        warn_count = self.increment_warning(str(message.author.id))

        try:
            dm = await message.author.create_dm()
            await dm.send(
                "⚠️ Votre message a été supprimé car il enfreint le règlement du serveur."
            )
        except discord.Forbidden:
            pass

        staff_channel = discord.utils.get(
            message.guild.text_channels, name=STAFF_CHANNEL_NAME
        )
        if staff_channel:
            await staff_channel.send(
                f"Infraction {intent} par {message.author.mention} (avertissement {warn_count}/2)\n> {message.content}"
            )

        if warn_count >= 2:
            try:
                until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                await message.author.timeout(until, reason="Avertissements multiples")
                if staff_channel:
                    await staff_channel.send(
                        f"{message.author.mention} a été timeout 1h après plusieurs avertissements."
                    )
            except Exception as e:
                if staff_channel:
                    await staff_channel.send(f"⚠️ Échec du timeout : {e}")

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="warnings")
    async def warnings_command(self, ctx: commands.Context, member: discord.Member):
        count = self.warnings.get(str(member.id), 0)
        await ctx.send(f"{member.display_name} a {count} avertissement(s).")

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="resetwarnings")
    async def reset_warnings_command(self, ctx: commands.Context, member: discord.Member):
        if str(member.id) in self.warnings:
            del self.warnings[str(member.id)]
            self.save_warnings()
        await ctx.send(f"Les avertissements de {member.display_name} ont été réinitialisés.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
