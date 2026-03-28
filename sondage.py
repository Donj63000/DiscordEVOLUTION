import asyncio
import logging
import os
import random
from datetime import timedelta

import discord
from discord.ext import commands, tasks

from utils.channel_resolver import resolve_text_channel
from utils.console_json_store import ConsoleJSONSnapshotStore

ALPHABET_EMOJIS = [
    "🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮",
    "🇯", "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷",
    "🇸", "🇹", "🇺", "🇻", "🇼", "🇽", "🇾", "🇿",
]

POLL_STORAGE: dict[int, dict] = {}
ANNONCE_CHANNEL_FALLBACK = os.getenv("ANNONCE_CHANNEL_NAME") or "annonces"
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
POLL_MARKER = "===SONDAGES==="
POLL_FILENAME = "polls_data.json"
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
log = logging.getLogger(__name__)


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
        self.console_message_id: int | None = None
        self._init_task: asyncio.Task | None = None
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=POLL_MARKER,
            filename=POLL_FILENAME,
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="SONDAGE_HISTORY_LIMIT",
        )
        self.poll_watcher.start()

    async def cog_load(self):
        wait_until_ready = getattr(self.bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            if self._init_task is None or self._init_task.done():
                import asyncio
                self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        self.poll_watcher.cancel()

    async def _post_ready_init(self):
        await self.bot.wait_until_ready()
        await self._load_polls_from_console()

    def _serialize_polls(self) -> dict:
        return {
            "polls": {
                str(message_id): dict(data)
                for message_id, data in POLL_STORAGE.items()
            }
        }

    async def _save_polls_to_console(self):
        message = await self.store.save(self._serialize_polls(), current_message_id=self.console_message_id)
        if message is not None:
            self.console_message_id = message.id

    async def _load_polls_from_console(self):
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)
        if not isinstance(payload, dict):
            return
        polls = payload.get("polls") or {}
        if not isinstance(polls, dict):
            return
        POLL_STORAGE.clear()
        for message_id_str, data in polls.items():
            try:
                message_id = int(message_id_str)
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                POLL_STORAGE[message_id] = data
        self.console_message_id = getattr(message, "id", None)
        log.info("SondageCog: %s polls restored from console.", len(POLL_STORAGE))

    def _is_staff(self, member: discord.Member) -> bool:
        perms = getattr(member, "guild_permissions", None)
        if perms and (perms.manage_messages or perms.administrator):
            return True
        roles = getattr(member, "roles", [])
        return any(getattr(role, "name", None) == STAFF_ROLE_NAME for role in roles)

    @tasks.loop(seconds=20.0)
    async def poll_watcher(self):
        now_ts = int(discord.utils.utcnow().timestamp())
        ended_polls = []
        for message_id, poll_data in list(POLL_STORAGE.items()):
            end_time_ts = int(poll_data.get("end_time_ts", 0) or 0)
            if end_time_ts and now_ts >= end_time_ts:
                ended_polls.append(message_id)
        for msg_id in ended_polls:
            await self.close_poll(msg_id)
            POLL_STORAGE.pop(msg_id, None)
        if ended_polls:
            await self._save_polls_to_console()

    @commands.command(name="sondage")
    async def create_sondage(self, ctx: commands.Context, *, args: str = None):
        if not args:
            await ctx.send(
                "Utilisation : `!sondage <Titre> ; <Choix1> ; Choix2 ; ... ; temps=JJ:HH:MM`\n"
                "Exemple : `!sondage Sortie Donjon ; Bworker ; Ougah ; temps=1:12:30` (1j12h30min)"
            )
            return
        parts = [p.strip() for p in args.split(";")]
        delay_seconds = None
        for i, part in enumerate(parts):
            if part.lower().startswith("temps="):
                try:
                    raw_time = part.split("=", 1)[1].strip()
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
        if len(choices) > len(ALPHABET_EMOJIS):
            await ctx.send(f"Nombre de choix trop élevé (max = {len(ALPHABET_EMOJIS)}).")
            return

        description_lines = [f"{ALPHABET_EMOJIS[i]} **{choice}**" for i, choice in enumerate(choices)]
        embed = discord.Embed(
            title=f"📊 {title}",
            description="\n".join(description_lines),
            color=random_pastel_color(),
        )
        embed.set_author(
            name=f"Sondage créé par {ctx.author.display_name}",
            icon_url=(ctx.author.display_avatar.url if getattr(ctx.author, "display_avatar", None) else None),
        )
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2550/2550205.png")

        end_time_ts = 0
        end_time_msg = "Aucune (clôture manuelle)."
        if delay_seconds is not None:
            end_dt = discord.utils.utcnow() + timedelta(seconds=delay_seconds)
            end_time_ts = int(end_dt.timestamp())
            end_time_msg = f"Fin prévue : {end_dt.strftime('%d/%m/%Y %H:%M')}"
        embed.add_field(name="⏳ Fin du sondage", value=end_time_msg, inline=False)

        annonce_channel = resolve_text_channel(
            ctx.guild,
            id_env="ANNONCE_CHANNEL_ID",
            name_env="ANNONCE_CHANNEL_NAME",
            default_name=ANNONCE_CHANNEL_FALLBACK,
        )
        if not annonce_channel:
            await ctx.send("Le canal d'annonces est introuvable. Vérifie ANNONCE_CHANNEL_ID ou ANNONCE_CHANNEL_NAME.")
            return

        sondage_message = await annonce_channel.send("@everyone Nouveau sondage :", embed=embed)
        embed.set_footer(text=f"ID du message (pour !close_sondage) : {sondage_message.id}")
        await sondage_message.edit(embed=embed)
        for i in range(len(choices)):
            await sondage_message.add_reaction(ALPHABET_EMOJIS[i])
        try:
            await ctx.message.delete()
        except Exception:
            pass

        POLL_STORAGE[sondage_message.id] = {
            "title": title,
            "choices": choices,
            "channel_id": annonce_channel.id,
            "author_id": ctx.author.id,
            "end_time_ts": end_time_ts,
        }
        await self._save_polls_to_console()
        if end_time_ts:
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
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            await ctx.send("Aucun sondage trouvé pour cet ID.")
            return
        if ctx.author.id != poll_data.get("author_id") and not self._is_staff(ctx.author):
            await ctx.send("Vous n'avez pas l'autorisation de fermer ce sondage.")
            return
        await self.close_poll(message_id)
        POLL_STORAGE.pop(message_id, None)
        await self._save_polls_to_console()

    async def close_poll(self, message_id: int):
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            return
        channel_id = poll_data.get("channel_id")
        get_channel = getattr(self.bot, "get_channel", None)
        channel = get_channel(channel_id) if callable(get_channel) else None
        if not channel:
            return
        try:
            msg_sondage = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        choices = poll_data.get("choices", [])
        title = poll_data.get("title", "Sondage")
        vote_counts = [0] * len(choices)
        for reaction in getattr(msg_sondage, "reactions", []) or []:
            if reaction.emoji in ALPHABET_EMOJIS:
                idx = ALPHABET_EMOJIS.index(reaction.emoji)
                if idx < len(choices):
                    vote_counts[idx] = max(reaction.count - 1, 0)
        max_votes = max(vote_counts) if vote_counts else 1
        results_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            count = vote_counts[i]
            bar = make_progress_bar(count, max_votes)
            results_lines.append(f"{emoji} **{choice}** : {count} vote(s)\n    `{bar}`")
        embed = discord.Embed(
            title=f"Résultats du sondage : {title}",
            description="\n".join(results_lines),
            color=0xFEE75C,
        )
        embed.set_footer(text="Le sondage est maintenant clôturé.")
        await channel.send(f"**Fin du sondage** : `{title}`\nVoici le récapitulatif :", embed=embed)
        if msg_sondage.embeds:
            closed_embed = msg_sondage.embeds[0].copy()
            closed_embed.title += " [Clôturé]"
            closed_embed.color = 0x2C2F33
            await msg_sondage.edit(embed=closed_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SondageCog(bot))
