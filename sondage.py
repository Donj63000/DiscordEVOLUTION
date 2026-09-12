import asyncio
import logging
import os
import random
from datetime import timedelta

import discord
from discord.ext import commands, tasks
from utils.slash_support import delete_invocation_message
from utils.calendar_data import shorten

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
        self._close_lock = asyncio.Lock()
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
            return True
        log.warning("Sondages : sauvegarde #console indisponible.")
        return False

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
            try:
                end_time_ts = int(poll_data.get("end_time_ts", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                log.warning("Sondages : échéance invalide ignorée pour %s.", message_id)
                continue
            if end_time_ts and now_ts >= end_time_ts:
                ended_polls.append(message_id)
        for msg_id in ended_polls:
            await self._finish_poll(msg_id)

    async def _finish_poll(self, message_id: int) -> bool:
        # La clôture manuelle et le minuteur ne doivent pas se concurrencer.
        async with self._close_lock:
            if message_id not in POLL_STORAGE:
                return True
            if not await self.close_poll(message_id):
                return False
            POLL_STORAGE.pop(message_id, None)
            await self._save_polls_to_console()
            return True

    @commands.command(name="sondage")
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.member)
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
        if len(parts) < 3:
            await ctx.send("Veuillez spécifier au moins un titre et deux choix.\nEx: `!sondage Titre ; Choix1 ; Choix2`")
            return
        title = parts[0]
        choices = parts[1:]
        if len(choices) > len(ALPHABET_EMOJIS):
            await ctx.send(f"Nombre de choix trop élevé (max = {len(ALPHABET_EMOJIS)}).")
            return

        if (
            not title or len(title.encode("utf-16-le")) // 2 > 180
            or any(not choice or len(choice.encode("utf-16-le")) // 2 > 100 for choice in choices)
            or len({choice.casefold() for choice in choices}) != len(choices)
        ):
            await ctx.send("Le titre est limité à 180 caractères ; chaque choix doit être distinct et compter de 1 à 100 caractères.")
            return
        if delay_seconds is not None and not 0 < delay_seconds <= 30 * 86400:
            await ctx.send("La durée du sondage doit être positive et ne pas dépasser 30 jours.")
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
            end_time_msg = f"Fin prévue : <t:{end_time_ts}:F> · <t:{end_time_ts}:R>"
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

        sondage_message = await annonce_channel.send(
            "Nouveau sondage :", embed=embed, allowed_mentions=discord.AllowedMentions.none(),
        )
        POLL_STORAGE[sondage_message.id] = {
            "title": title,
            "choices": choices,
            "channel_id": annonce_channel.id,
            "guild_id": ctx.guild.id,
            "author_id": ctx.author.id,
            "end_time_ts": end_time_ts,
        }
        persisted = await self._save_polls_to_console()
        embed.set_footer(text=f"Clôture manuelle : /close_sondage · ID {sondage_message.id}")
        try:
            await sondage_message.edit(embed=embed)
            for i in range(len(choices)):
                await sondage_message.add_reaction(ALPHABET_EMOJIS[i])
        except discord.HTTPException:
            log.warning("Sondages : réactions incomplètes pour %s.", sondage_message.id, exc_info=True)
            await ctx.send(
                "Le sondage est publié, mais Discord a refusé certaines réactions. "
                "Le Staff doit vérifier « Ajouter des réactions » et « Voir les anciens messages »."
            )
        try:
            await delete_invocation_message(ctx)
        except discord.HTTPException:
            pass
        link = getattr(sondage_message, "jump_url", None)
        receipt = f"Sondage publié : {link}" if link else f"Sondage publié (ID `{sondage_message.id}`)."
        if not persisted:
            receipt += "\n⚠️ La sauvegarde #console a échoué : le suivi pourrait être perdu au redémarrage."
        await ctx.send(receipt)

    @commands.command(name="close_sondage")
    @commands.guild_only()
    async def manual_close_poll(self, ctx: commands.Context, message_id: int = None):
        if not message_id:
            await ctx.send("Veuillez préciser l'ID du message. Ex: `!close_sondage 1234567890`")
            return
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            await ctx.send("Aucun sondage trouvé pour cet ID.")
            return
        channel = self.bot.get_channel(poll_data.get("channel_id"))
        source_guild = poll_data.get("guild_id") or getattr(getattr(channel, "guild", None), "id", None)
        if source_guild != ctx.guild.id:
            await ctx.send("Ce sondage n'appartient pas à ce serveur ou son salon est inaccessible.")
            return
        if ctx.author.id != poll_data.get("author_id") and not self._is_staff(ctx.author):
            await ctx.send("Vous n'avez pas l'autorisation de fermer ce sondage.")
            return
        if await self._finish_poll(message_id):
            await ctx.send("Sondage clôturé. Les résultats sont affichés sur son message.")
        else:
            await ctx.send(
                "La clôture a échoué. Le sondage reste suivi ; le Staff doit vérifier "
                "l'accès au salon et les permissions du bot."
            )

    async def close_poll(self, message_id: int) -> bool:
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            return True
        channel_id = poll_data.get("channel_id")
        get_channel = getattr(self.bot, "get_channel", None)
        channel = get_channel(channel_id) if callable(get_channel) else None
        if not channel:
            return False
        try:
            msg_sondage = await channel.fetch_message(message_id)
        except discord.NotFound:
            # Un message supprimé n'a plus de scrutin à surveiller.
            return True
        except discord.HTTPException:
            log.warning("Sondages : lecture impossible pour %s.", message_id, exc_info=True)
            return False
        choices = poll_data.get("choices", [])
        if not isinstance(choices, list) or not 2 <= len(choices) <= len(ALPHABET_EMOJIS):
            log.warning("Sondages : choix invalides pour %s ; suivi conservé.", message_id)
            return False
        title = poll_data.get("title", "Sondage")
        vote_counts = [0] * len(choices)
        for reaction in getattr(msg_sondage, "reactions", []) or []:
            if reaction.emoji in ALPHABET_EMOJIS:
                idx = ALPHABET_EMOJIS.index(reaction.emoji)
                if idx < len(choices):
                    # Soustraire uniquement la réaction effectivement posée par le bot.
                    vote_counts[idx] = max(reaction.count - int(bool(reaction.me)), 0)
        max_votes = max(vote_counts) if vote_counts else 1
        results_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            count = vote_counts[i]
            bar = make_progress_bar(count, max_votes)
            results_lines.append(f"{emoji} **{choice}** : {count} vote(s)\n    `{bar}`")
        embed = discord.Embed(
            title=shorten(f"Résultats : {title} [Clôturé]", 250),
            description=shorten("\n".join(results_lines), 4000),
            color=0xFEE75C,
        )
        embed.set_footer(text="Sondage clôturé · Les réactions ajoutées ensuite ne changent pas ces résultats.")
        try:
            # Une édition est idempotente : une reprise ne publie pas deux récapitulatifs.
            await msg_sondage.edit(embed=embed)
        except discord.NotFound:
            return True
        except discord.HTTPException:
            log.warning("Sondages : édition impossible pour %s.", message_id, exc_info=True)
            return False
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(SondageCog(bot))
