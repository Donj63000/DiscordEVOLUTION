import os
import json
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from collections import defaultdict

CHECK_INTERVAL_HOURS = 168  # 1 semaine
VOTE_DURATION_SECONDS = 300  # 5 minutes
STAFF_ROLE_NAME = "Staff"
VALID_MEMBER_ROLE_NAME = "Membre validé d'Evolution"
INVITE_ROLE_NAME = "Invité"
VETERAN_ROLE_NAME = "Vétéran"
STAFF_CHANNEL_NAME = "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff"
MESSAGE_THRESHOLD = 20
JOINED_THRESHOLD_DAYS = 6 * 30
PROMOTIONS_FILE = "promotions_data.json"

class UpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_count = defaultdict(int)
        self.promotions_data = {}
        self.load_promotions_data()
        self.check_up_status.start()

    def cog_unload(self):
        self.check_up_status.cancel()

    def load_promotions_data(self):
        if os.path.exists(PROMOTIONS_FILE):
            try:
                with open(PROMOTIONS_FILE, "r", encoding="utf-8") as f:
                    self.promotions_data = json.load(f)
            except:
                self.promotions_data = {}
        else:
            self.promotions_data = {}

    def save_promotions_data(self):
        with open(PROMOTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.promotions_data, f, indent=4, ensure_ascii=False)

    def get_promotion_status(self, user_id: int):
        return self.promotions_data.get(str(user_id), {}).get("status")

    def set_promotion_status(self, user_id: int, status: str):
        user_id_str = str(user_id)
        if user_id_str not in self.promotions_data:
            self.promotions_data[user_id_str] = {}
        self.promotions_data[user_id_str]["status"] = status
        self.save_promotions_data()

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
                status = self.get_promotion_status(member.id)

                # On ignore ceux qui sont déjà promus ou refusés
                if status in ["promoted", "refused", "voting"]:
                    continue

                # S'il avait été reporté (postponed), on retente le vote cette semaine
                if status not in ["postponed", None]:
                    continue

                if (
                    join_days >= JOINED_THRESHOLD_DAYS
                    and has_valid_role
                    and not has_invite_role
                    and msg_count >= MESSAGE_THRESHOLD
                    and not any(r.name == VETERAN_ROLE_NAME for r in member.roles)
                ):
                    await self.lancer_vote(staff_channel, member)

    async def lancer_vote(self, staff_channel: discord.TextChannel, member: discord.Member):
        mention_staff_role = discord.utils.get(member.guild.roles, name=STAFF_ROLE_NAME)
        mention_text = mention_staff_role.mention if mention_staff_role else "@Staff"

        embed = discord.Embed(
            title="Vote Promotion",
            description=(
                f"{mention_text} — Promotion de {member.mention} en **{VETERAN_ROLE_NAME}** ?\n"
                "Réagissez ✅ ou ❌ (5 minutes)."
            ),
            color=discord.Color.blue()
        )

        vote_message = await staff_channel.send(embed=embed)
        await vote_message.add_reaction("✅")
        await vote_message.add_reaction("❌")
        self.set_promotion_status(member.id, "voting")

        await asyncio.sleep(VOTE_DURATION_SECONDS)

        try:
            vote_message = await vote_message.channel.fetch_message(vote_message.id)
        except discord.NotFound:
            # Si le message a été supprimé, on reporte également
            await staff_channel.send(f"Le message de vote pour {member.mention} a disparu, vote reporté.")
            self.set_promotion_status(member.id, "postponed")
            return

        yes_count = 0
        no_count = 0
        for reaction in vote_message.reactions:
            if str(reaction.emoji) == "✅":
                yes_count = reaction.count - 1
            elif str(reaction.emoji) == "❌":
                no_count = reaction.count - 1

        total_votes = yes_count + no_count

        # Personne n'a voté => report à la semaine prochaine
        if total_votes == 0:
            await staff_channel.send(f"Aucun vote exprimé pour {member.mention}, proposition reportée à la semaine prochaine.")
            self.set_promotion_status(member.id, "postponed")
            return

        # Si au moins une personne refuse => refus définitif
        if no_count >= 1:
            await staff_channel.send(f"Promotion refusée pour {member.mention}. (Un ❌ suffit à annuler la promotion)")
            self.set_promotion_status(member.id, "refused")
            return

        # Sinon (yes_count >= 1, no_count = 0) => promotion
        await self.promouvoir_veteran(staff_channel, member)

    async def promouvoir_veteran(self, staff_channel: discord.TextChannel, member: discord.Member):
        veteran_role = discord.utils.get(member.guild.roles, name=VETERAN_ROLE_NAME)
        if not veteran_role:
            await staff_channel.send("Rôle 'Vétéran' introuvable, impossible de promouvoir.")
            self.set_promotion_status(member.id, "refused")  # On ne repropose pas sans rôle
            return

        try:
            await member.add_roles(veteran_role)
            await staff_channel.send(f"{member.mention} promu(e) **{VETERAN_ROLE_NAME}**.")
            self.set_promotion_status(member.id, "promoted")
        except discord.Forbidden:
            await staff_channel.send(f"Permissions insuffisantes pour promouvoir {member.display_name}.")
            self.set_promotion_status(member.id, "refused")
        except discord.HTTPException as e:
            await staff_channel.send(f"Erreur promotion {member.display_name} : {e}")
            self.set_promotion_status(member.id, "refused")

async def setup(bot: commands.Bot):
    await bot.add_cog(UpCog(bot))
