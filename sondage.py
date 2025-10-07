import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import os
import random

ALPHABET_EMOJIS = [
    "🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮",
    "🇯", "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷",
    "🇸", "🇹", "🇺", "🇻", "🇼", "🇽", "🇾", "🇿"
]

POLL_STORAGE = {}
ANNONCE_CHANNEL_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "annonces")


def random_pastel_color() -> int:
    r = random.randint(128, 255)
    g = random.randint(128, 255)
    b = random.randint(128, 255)
    return (r << 16) + (g << 8) + b

def make_progress_bar(count: int, max_count: int, bar_length: int = 10) -> str:
    if max_count == 0:
        return "░" * bar_length
    fraction = count / max_count
    filled = int(round(fraction * bar_length))
    return "█" * filled + "░" * (bar_length - filled)

class SondageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.poll_watcher.start()

    def cog_unload(self):
        self.poll_watcher.cancel()

    @tasks.loop(seconds=20.0)
    async def poll_watcher(self):
        now = datetime.utcnow()
        ended_polls = []
        for message_id, poll_data in list(POLL_STORAGE.items()):
            end_time = poll_data.get("end_time")
            if end_time and now >= end_time:
                ended_polls.append(message_id)
        for msg_id in ended_polls:
            await self.close_poll(msg_id)
            POLL_STORAGE.pop(msg_id, None)

    @commands.command(name="sondage")
    async def create_sondage(self, ctx: commands.Context, *, args: str = None):
        if not args:
            await ctx.send("Utilisation : `!sondage <Titre> ; <Choix1> ; Choix2 ; ... ; temps=JJ:HH:MM`\nExemple : `!sondage Sortie Donjon ; Bworker ; Ougah ; temps=1:12:30` (1j12h30min)")
            return
        parts = [p.strip() for p in args.split(";")]
        delay_seconds = None
        for i, part in enumerate(parts):
            if part.lower().startswith("temps="):
                try:
                    raw_time = part.split("=")[1].strip()
                    d, h, m = raw_time.split(":")
                    days = int(d)
                    hours = int(h)
                    mins = int(m)
                    delay_seconds = days * 86400 + hours * 3600 + mins * 60
                    parts.pop(i)
                except (ValueError, IndexError):
                    pass
                break
        if len(parts) < 2:
            await ctx.send("Veuillez spécifier au moins un titre et un choix.\nEx: `!sondage Titre ; Choix1 ; Choix2`")
            return
        title = parts[0]
        choices = parts[1:]
        max_choices = len(ALPHABET_EMOJIS)
        if len(choices) > max_choices:
            await ctx.send(f"Nombre de choix trop élevé (max = {max_choices}).")
            return
        description_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            description_lines.append(f"{emoji} **{choice}**")
        description_str = "\n".join(description_lines)
        embed_color = random_pastel_color()
        embed = discord.Embed(title=f"📊 {title}", description=description_str, color=embed_color)
        embed.set_author(name=f"Sondage créé par {ctx.author.display_name}", icon_url=(ctx.author.display_avatar.url if ctx.author.display_avatar else None))
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2550/2550205.png")
        end_time_val = None
        end_time_msg = "Aucune (clôture manuelle)."
        if delay_seconds is not None:
            end_time_val = datetime.utcnow() + timedelta(seconds=delay_seconds)
            end_time_msg = f"Fin prévue : {end_time_val.strftime('%d/%m/%Y %H:%M')}"
        embed.add_field(name="⏳ Fin du sondage", value=end_time_msg, inline=False)
        embed.set_footer(text=f"ID du message (pour !close_sondage) : {ctx.message.id}")
        annonce_channel = discord.utils.get(ctx.guild.text_channels, name=ANNONCE_CHANNEL_NAME)
        if not annonce_channel:
            await ctx.send(f"Le canal #{ANNONCE_CHANNEL_NAME} est introuvable.")
            return
        sondage_message = await annonce_channel.send("@everyone Nouveau sondage :", embed=embed)
        for i in range(len(choices)):
            await sondage_message.add_reaction(ALPHABET_EMOJIS[i])
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
        POLL_STORAGE[sondage_message.id] = {
            "title": title,
            "choices": choices,
            "channel_id": annonce_channel.id,
            "author_id": ctx.author.id,
            "end_time": end_time_val,
        }
        if end_time_val:
            d = delay_seconds // 86400
            rem = delay_seconds % 86400
            h = rem // 3600
            rem = rem % 3600
            m = rem // 60
            await ctx.send(f"Sondage lancé: `{title}`\nFin estimée dans ~{d}j {h}h {m}m.")

    @commands.command(name="close_sondage")
    async def manual_close_poll(self, ctx: commands.Context, message_id: int = None):
        if not message_id:
            await ctx.send("Veuillez préciser l'ID du message. Ex: `!close_sondage 1234567890`")
            return
        if message_id not in POLL_STORAGE:
            await ctx.send("Aucun sondage trouvé pour cet ID.")
            return
        await self.close_poll(message_id)
        POLL_STORAGE.pop(message_id, None)

    async def close_poll(self, message_id: int):
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            return
        channel_id = poll_data["channel_id"]
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            msg_sondage = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        choices = poll_data["choices"]
        title = poll_data["title"]
        vote_counts = [0] * len(choices)
        for reaction in msg_sondage.reactions:
            if reaction.emoji in ALPHABET_EMOJIS:
                idx = ALPHABET_EMOJIS.index(reaction.emoji)
                if idx < len(choices):
                    vote_counts[idx] = reaction.count - 1
        max_votes = max(vote_counts) if vote_counts else 1
        results_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            count = vote_counts[i]
            bar = make_progress_bar(count, max_votes)
            results_lines.append(f"{emoji} **{choice}** : {count} vote(s)\n    `{bar}`")
        results_str = "\n".join(results_lines)
        embed = discord.Embed(title=f"Résultats du sondage : {title}", description=results_str, color=0xFEE75C)
        embed.set_footer(text="Le sondage est maintenant clôturé.")
        await channel.send(f"**Fin du sondage** : `{title}`\nVoici le récapitulatif :", embed=embed)
        if msg_sondage.embeds:
            closed_embed = msg_sondage.embeds[0].copy()
            closed_embed.title += " [Clôturé]"
            closed_embed.color = 0x2C2F33
            await msg_sondage.edit(embed=closed_embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(SondageCog(bot))
