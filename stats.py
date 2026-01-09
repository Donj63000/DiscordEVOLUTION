import asyncio
import copy
import json
import logging
import os
from datetime import datetime

import discord
from discord.ext import commands, tasks

from utils.stats_store import StatsStore

DATA_FILE = os.getenv("STATS_LOCAL_PATH", "stats_data.json")
STATS_CHANNEL_NAME = os.getenv("STATS_CHANNEL", "console")
DEFAULT_MAX_LOGS = 2000

log = logging.getLogger(__name__)

DEFAULT_STATS_TEMPLATE = {
    "messages": {
        "channel_count": {},
        "role_count": {},
        "hour_count": {},
        "user_count": {},
        "total": 0,
    },
    "edits": {
        "channel_count": {},
        "hour_count": {},
        "total": 0,
    },
    "deletions": {
        "channel_count": {},
        "hour_count": {},
        "total": 0,
    },
    "reactions_added": {
        "emoji_count": {},
        "hour_count": {},
        "total": 0,
    },
    "reactions_removed": {
        "emoji_count": {},
        "hour_count": {},
        "total": 0,
    },
    "voice": {
        "join_count": 0,
        "leave_count": 0,
        "channel_joins": {},
        "channel_leaves": {},
    },
    "presence": {
        "status_changes": {},
    },
    "logs": {
        "messages_created": [],
        "messages_edited": [],
        "messages_deleted": [],
        "reactions": [],
        "voice": [],
        "presence": [],
    },
}


def build_stats_state(loaded: dict | None = None) -> dict:
    base = copy.deepcopy(DEFAULT_STATS_TEMPLATE)
    if not isinstance(loaded, dict):
        return base
    _merge_dicts(base, loaded)
    return base


def _merge_dicts(base: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if key not in base:
            base[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
            continue
        current = base[key]
        if isinstance(current, dict) and isinstance(value, dict):
            _merge_dicts(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            base[key] = copy.deepcopy(value)
        else:
            base[key] = value

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

class StatsCog(commands.Cog):
    """
    Commandes !stats on / !stats off (Staff) pour activer/désactiver la collecte
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stats_enabled = True
        self.stats_data = build_stats_state()
        self.store: StatsStore | None = None
        self.initialized = False
        self._init_lock = asyncio.Lock()
        self._init_task: asyncio.Task | None = None
        try:
            self.max_logs = max(int(os.getenv("STATS_MAX_LOGS", str(DEFAULT_MAX_LOGS))), 0)
        except ValueError:
            self.max_logs = DEFAULT_MAX_LOGS

    def cog_unload(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        if self.save_loop.is_running():
            self.save_loop.cancel()

    async def cog_load(self) -> None:
        if self._init_task is None or self._init_task.done():
            self._init_task = asyncio.create_task(self._post_ready_init())

    async def _post_ready_init(self) -> None:
        await self.bot.wait_until_ready()
        async with self._init_lock:
            if self.initialized:
                return
            log.debug("StatsCog: init start for channel %s", STATS_CHANNEL_NAME)
            self.store = StatsStore(self.bot, STATS_CHANNEL_NAME)
            loaded = None
            try:
                loaded = await self.store.load()
            except Exception as exc:
                print(f"[Stats] ?%chec du chargement via #{STATS_CHANNEL_NAME} : {exc}")
            if not loaded and os.path.exists(DATA_FILE):
                try:
                    with open(DATA_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                except Exception as exc:
                    print(f"[Stats] ?%chec du chargement local {DATA_FILE} : {exc}")
            if loaded:
                self.stats_data = build_stats_state(loaded)
            self.initialized = True
            log.debug(
                "StatsCog: init complete (messages=%s)",
                self.stats_data["messages"]["total"],
            )
        if not self.save_loop.is_running():
            self.save_loop.start()

    async def save_stats_data(self):
        stored = False
        if self.store:
            try:
                stored = await self.store.save(self.stats_data)
            except Exception as exc:
                print(f"[Stats] Erreur persistance #{STATS_CHANNEL_NAME} : {exc}")
        if stored:
            return
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.stats_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Stats] Erreur lors de la sauvegarde locale : {e}")

    @tasks.loop(seconds=int(os.getenv("STATS_SAVE_INTERVAL", "900")))
    async def save_loop(self):
        await self.save_stats_data()

    def reset_stats_data(self):
        self.stats_data = build_stats_state()

    def _append_log(self, bucket: str, entry: dict) -> None:
        logs = self.stats_data.setdefault("logs", {})
        items = logs.get(bucket)
        if not isinstance(items, list):
            items = []
            logs[bucket] = items
        items.append(entry)
        max_logs = self.max_logs
        if max_logs > 0 and len(items) > max_logs:
            del items[:-max_logs]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.stats_enabled:
            return
        if message.author.bot:
            return
        msg_stats = self.stats_data["messages"]
        msg_stats["total"] += 1
        ch_id = str(message.channel.id)
        msg_stats["channel_count"].setdefault(ch_id, 0)
        msg_stats["channel_count"][ch_id] += 1
        roles = []
        if message.guild is not None:
            for role in message.author.roles:
                if role.is_default():
                    continue
                r_id = str(role.id)
                msg_stats["role_count"].setdefault(r_id, 0)
                msg_stats["role_count"][r_id] += 1
                roles.append(str(role.id))
        hour_str = str(datetime.now().hour)
        msg_stats["hour_count"].setdefault(hour_str, 0)
        msg_stats["hour_count"][hour_str] += 1
        user_id = str(message.author.id)
        msg_stats["user_count"].setdefault(user_id, 0)
        msg_stats["user_count"][user_id] += 1
        msg_log = {
            "timestamp": now_iso(),
            "message_id": str(message.id),
            "channel_id": ch_id,
            "author_id": user_id,
            "author_name": message.author.name,
            "roles": roles,
            "content": message.content
        }
        self._append_log("messages_created", msg_log)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not self.stats_enabled:
            return
        if after.author.bot:
            return
        ed_stats = self.stats_data["edits"]
        ed_stats["total"] += 1
        ch_id = str(after.channel.id)
        ed_stats["channel_count"].setdefault(ch_id, 0)
        ed_stats["channel_count"][ch_id] += 1
        hour_str = str(datetime.now().hour)
        ed_stats["hour_count"].setdefault(hour_str, 0)
        ed_stats["hour_count"][hour_str] += 1
        edit_log = {
            "timestamp": now_iso(),
            "message_id": str(after.id),
            "channel_id": ch_id,
            "author_id": str(after.author.id),
            "author_name": after.author.name,
            "old_content": before.content,
            "new_content": after.content
        }
        self._append_log("messages_edited", edit_log)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self.stats_enabled:
            return
        if message.author and message.author.bot:
            return
        del_stats = self.stats_data["deletions"]
        del_stats["total"] += 1
        ch_id = str(message.channel.id)
        del_stats["channel_count"].setdefault(ch_id, 0)
        del_stats["channel_count"][ch_id] += 1
        hour_str = str(datetime.now().hour)
        del_stats["hour_count"].setdefault(hour_str, 0)
        del_stats["hour_count"][hour_str] += 1
        del_log = {
            "timestamp": now_iso(),
            "message_id": str(message.id),
            "channel_id": ch_id,
            "author_id": str(message.author.id) if message.author else None,
            "author_name": message.author.name if message.author else None,
            "content": message.content if message.content else None
        }
        self._append_log("messages_deleted", del_log)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        if not self.stats_enabled:
            return
        if user.bot:
            return
        add_stats = self.stats_data["reactions_added"]
        add_stats["total"] += 1
        emoji_str = str(reaction.emoji)
        add_stats["emoji_count"].setdefault(emoji_str, 0)
        add_stats["emoji_count"][emoji_str] += 1
        hour_str = str(datetime.now().hour)
        add_stats["hour_count"].setdefault(hour_str, 0)
        add_stats["hour_count"][hour_str] += 1
        react_log = {
            "timestamp": now_iso(),
            "type": "ADD",
            "message_id": str(reaction.message.id),
            "channel_id": str(reaction.message.channel.id),
            "user_id": str(user.id),
            "user_name": user.name,
            "emoji": emoji_str
        }
        self._append_log("reactions", react_log)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member):
        if not self.stats_enabled:
            return
        if user is None or user.bot:
            return
        rem_stats = self.stats_data["reactions_removed"]
        rem_stats["total"] += 1
        emoji_str = str(reaction.emoji)
        rem_stats["emoji_count"].setdefault(emoji_str, 0)
        rem_stats["emoji_count"][emoji_str] += 1
        hour_str = str(datetime.now().hour)
        rem_stats["hour_count"].setdefault(hour_str, 0)
        rem_stats["hour_count"][hour_str] += 1
        react_log = {
            "timestamp": now_iso(),
            "type": "REMOVE",
            "message_id": str(reaction.message.id),
            "channel_id": str(reaction.message.channel.id),
            "user_id": str(user.id),
            "user_name": user.name,
            "emoji": emoji_str
        }
        self._append_log("reactions", react_log)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not self.stats_enabled:
            return
        voice_stats = self.stats_data["voice"]
        voice_log_entry = {
            "timestamp": now_iso(),
            "member_id": str(member.id),
            "member_name": member.name,
            "old_channel_id": str(before.channel.id) if before.channel else None,
            "new_channel_id": str(after.channel.id) if after.channel else None,
        }
        if before.channel is None and after.channel is not None:
            voice_stats["join_count"] += 1
            ch_id = str(after.channel.id)
            voice_stats["channel_joins"].setdefault(ch_id, 0)
            voice_stats["channel_joins"][ch_id] += 1
            voice_log_entry["type"] = "JOIN"
        elif before.channel is not None and after.channel is None:
            voice_stats["leave_count"] += 1
            ch_id = str(before.channel.id)
            voice_stats["channel_leaves"].setdefault(ch_id, 0)
            voice_stats["channel_leaves"][ch_id] += 1
            voice_log_entry["type"] = "LEAVE"
        elif before.channel and after.channel and before.channel.id != after.channel.id:
            voice_stats["leave_count"] += 1
            old_id = str(before.channel.id)
            voice_stats["channel_leaves"].setdefault(old_id, 0)
            voice_stats["channel_leaves"][old_id] += 1
            voice_stats["join_count"] += 1
            new_id = str(after.channel.id)
            voice_stats["channel_joins"].setdefault(new_id, 0)
            voice_stats["channel_joins"][new_id] += 1
            voice_log_entry["type"] = "MOVE"
        else:
            return
        self._append_log("voice", voice_log_entry)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if not self.stats_enabled:
            return
        if before.bot:
            return
        old_status = str(before.status)
        new_status = str(after.status)
        if old_status != new_status:
            pres_stats = self.stats_data["presence"]["status_changes"]
            transition_key = f"{old_status}->{new_status}"
            pres_stats.setdefault(transition_key, 0)
            pres_stats[transition_key] += 1
            presence_log = {
                "timestamp": now_iso(),
                "user_id": str(after.id),
                "user_name": after.name,
                "old_status": old_status,
                "new_status": new_status
            }
            self._append_log("presence", presence_log)

    @commands.group(name="stats", invoke_without_command=True)
    async def stats_main(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Commandes statistiques",
            description=(
                "Toutes les statistiques du serveur sont regroupées ici."
                " Utilise les sous-commandes ci-dessous pour explorer les"
                " différentes sections."
            ),
            color=0x00FF7F,
        )
        embed.add_field(
            name="Aperçu",
            value=(
                "`!stats all` — Vue d'ensemble\n"
                "`!stats messages` — Statistiques messages\n"
                "`!stats users` — Top utilisateurs\n"
                "`!stats ladder [options]` — Classement des profils\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Autres sections",
            value=(
                "`!stats edits` — Éditions\n"
                "`!stats deletions` — Suppressions\n"
                "`!stats reactions` — Réactions\n"
                "`!stats voice` — Activité vocale\n"
                "`!stats presence` — Statuts (présence)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Staff",
            value=(
                "`!stats reset` — Reset complet\n"
                "`!stats on` / `!stats off` — Activer ou désactiver la collecte"
            ),
            inline=False,
        )
        embed.set_footer(text="Astuce : `!stats ladder all` génère un export complet.")
        await ctx.send(embed=embed)

    @stats_main.command(name="all")
    async def stats_all(self, ctx: commands.Context):
        msg_total = self.stats_data["messages"]["total"]
        edits_total = self.stats_data["edits"]["total"]
        del_total = self.stats_data["deletions"]["total"]
        react_add_total = self.stats_data["reactions_added"]["total"]
        react_rem_total = self.stats_data["reactions_removed"]["total"]
        voice_join = self.stats_data["voice"]["join_count"]
        voice_leave = self.stats_data["voice"]["leave_count"]
        desc = (
            f"**Messages envoyés :** {msg_total}\n"
            f"**Éditions :** {edits_total}\n"
            f"**Suppressions :** {del_total}\n"
            f"**Réactions ajoutées :** {react_add_total}\n"
            f"**Réactions retirées :** {react_rem_total}\n"
            f"**Entrées vocales :** {voice_join}\n"
            f"**Sorties vocales :** {voice_leave}\n"
        )
        embed = discord.Embed(title="Statistiques globales", description=desc, color=0x00FF7F)
        embed.add_field(
            name="Explications",
            value=(
                "Données cumulées depuis le dernier reset.\n"
                "- **Messages envoyés** : total hors bots\n"
                "- **Éditions / Suppressions** : nombre de modifications / suppressions\n"
                "- **Réactions** : ajout / retrait\n"
                "- **Vocal** : entrées / sorties des salons vocaux"
            ),
            inline=False
        )
        embed.set_footer(text="Sauvegarde auto toutes les 60s.")
        await ctx.send(embed=embed)

    @stats_main.command(name="messages")
    async def stats_messages(self, ctx: commands.Context):
        msg_stats = self.stats_data["messages"]
        total_msg = msg_stats["total"]
        if total_msg == 0:
            return await ctx.send("Aucun message enregistré.")
        channel_counts = msg_stats["channel_count"]
        sorted_channels = sorted(channel_counts.items(), key=lambda x: x[1], reverse=True)
        top_channels_text = ""
        for i, (ch_id, count) in enumerate(sorted_channels[:5], start=1):
            ch_obj = self.bot.get_channel(int(ch_id))
            ch_name = "#" + ch_obj.name if ch_obj else f"Unknown({ch_id})"
            perc = (count / total_msg) * 100
            top_channels_text += f"{i}) {ch_name}: {count} msg ({perc:.2f}%)\n"
        if not top_channels_text:
            top_channels_text = "Aucune donnée."
        role_counts = msg_stats["role_count"]
        sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
        top_roles_text = ""
        for i, (r_id, count) in enumerate(sorted_roles[:5], start=1):
            r_obj = ctx.guild.get_role(int(r_id))
            r_name = "@" + r_obj.name if r_obj else f"Unknown({r_id})"
            perc = (count / total_msg) * 100
            top_roles_text += f"{i}) {r_name}: {count} msg ({perc:.2f}%)\n"
        if not top_roles_text:
            top_roles_text = "Aucune donnée."
        hour_counts = msg_stats["hour_count"]
        hour_text_list = []
        if hour_counts:
            total_hours = sum(hour_counts.values())
            sorted_hours = sorted(hour_counts.items(), key=lambda x: int(x[0]))
            for hour_str, c in sorted_hours:
                perc = (c / total_hours) * 100
                hour_text_list.append(f"{int(hour_str):02d}h: {c} ({perc:.1f}%)")
        hour_text = "\n".join(hour_text_list) if hour_text_list else "Aucune donnée horaire."
        embed = discord.Embed(title="Statistiques Messages", color=0x3498db)
        embed.add_field(name=f"Total Messages = {total_msg}", value="—", inline=False)
        embed.add_field(name="Top canaux", value=top_channels_text, inline=False)
        embed.add_field(name="Top rôles", value=top_roles_text, inline=False)
        embed.add_field(name="Distribution horaire", value=hour_text, inline=False)
        embed.add_field(
            name="Note",
            value="Les messages envoyés par les bots ne sont pas comptés.",
            inline=False
        )
        await ctx.send(embed=embed)

    @stats_main.command(name="users")
    async def stats_users(self, ctx: commands.Context):
        msg_stats = self.stats_data["messages"]
        total_msg = msg_stats["total"]
        if total_msg == 0:
            return await ctx.send("Aucun message enregistré.")
        user_counts = msg_stats["user_count"]
        if not user_counts:
            return await ctx.send("Aucune donnée utilisateur.")
        sorted_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
        lines = []
        top_n = 10
        for i, (u_id, count) in enumerate(sorted_users[:top_n], start=1):
            user_obj = ctx.guild.get_member(int(u_id))
            display_name = user_obj.display_name if user_obj else f"Unknown({u_id})"
            perc = (count / total_msg) * 100
            lines.append(f"{i}) **{display_name}** : {count} msg ({perc:.2f}%)")
        embed = discord.Embed(
            title="Top Utilisateurs (Messages)",
            description="\n".join(lines) if lines else "Aucune donnée.",
            color=0x1abc9c
        )
        embed.set_footer(text=f"Total messages = {total_msg}")
        await ctx.send(embed=embed)

    @stats_main.command(name="edits")
    async def stats_edits(self, ctx: commands.Context):
        ed_stats = self.stats_data["edits"]
        total_edits = ed_stats["total"]
        if total_edits == 0:
            return await ctx.send("Aucune édition détectée.")
        ch_counts = ed_stats["channel_count"]
        sorted_ch = sorted(ch_counts.items(), key=lambda x: x[1], reverse=True)
        top_ch_text = ""
        for i, (ch_id, count) in enumerate(sorted_ch[:5], start=1):
            ch_obj = self.bot.get_channel(int(ch_id))
            ch_name = "#" + ch_obj.name if ch_obj else f"Unknown({ch_id})"
            perc = (count / total_edits) * 100
            top_ch_text += f"{i}) {ch_name}: {count} ({perc:.2f}%)\n"
        hour_counts = ed_stats["hour_count"]
        hour_text_list = []
        if hour_counts:
            sorted_hours = sorted(hour_counts.items(), key=lambda x: int(x[0]))
            for hour_str, c in sorted_hours:
                perc = (c / total_edits) * 100
                hour_text_list.append(f"{hour_str}h: {c} ({perc:.1f}%)")
        hour_text = "\n".join(hour_text_list) if hour_text_list else "Aucune donnée horaire."
        embed = discord.Embed(title="Statistiques Éditions de Messages", color=0x2ecc71)
        embed.add_field(name="Total d'éditions", value=str(total_edits), inline=False)
        embed.add_field(name="Top canaux", value=top_ch_text or "Aucune donnée", inline=False)
        embed.add_field(name="Distribution horaire", value=hour_text, inline=False)
        await ctx.send(embed=embed)

    @stats_main.command(name="deletions")
    async def stats_deletions(self, ctx: commands.Context):
        del_stats = self.stats_data["deletions"]
        total_del = del_stats["total"]
        if total_del == 0:
            return await ctx.send("Aucune suppression détectée.")
        ch_counts = del_stats["channel_count"]
        sorted_ch = sorted(ch_counts.items(), key=lambda x: x[1], reverse=True)
        top_ch_text = ""
        for i, (ch_id, count) in enumerate(sorted_ch[:5], start=1):
            ch_obj = self.bot.get_channel(int(ch_id))
            ch_name = "#" + (ch_obj.name if ch_obj else f"Unknown({ch_id})")
            perc = (count / total_del) * 100
            top_ch_text += f"{i}) {ch_name}: {count} ({perc:.2f}%)\n"
        hour_counts = del_stats["hour_count"]
        hour_text_list = []
        if hour_counts:
            sorted_hours = sorted(hour_counts.items(), key=lambda x: int(x[0]))
            for hour_str, c in sorted_hours:
                perc = (c / total_del) * 100
                hour_text_list.append(f"{hour_str}h: {c} ({perc:.1f}%)")
        hour_text = "\n".join(hour_text_list) if hour_text_list else "Aucune donnée horaire."
        embed = discord.Embed(title="Statistiques Suppressions de Messages", color=0xe74c3c)
        embed.add_field(name="Total de suppressions", value=str(total_del), inline=False)
        embed.add_field(name="Top canaux", value=top_ch_text or "Aucune donnée", inline=False)
        embed.add_field(name="Distribution horaire", value=hour_text or "Aucune", inline=False)
        await ctx.send(embed=embed)

    @stats_main.command(name="reactions")
    async def stats_reactions(self, ctx: commands.Context):
        add_stats = self.stats_data["reactions_added"]
        rem_stats = self.stats_data["reactions_removed"]
        total_added = add_stats["total"]
        total_removed = rem_stats["total"]
        embed = discord.Embed(title="Statistiques Réactions", color=0xf1c40f)
        add_desc = f"**Total ajoutées** : {total_added}\n"
        if total_added > 0:
            sorted_emojis = sorted(add_stats["emoji_count"].items(), key=lambda x: x[1], reverse=True)
            top_5 = sorted_emojis[:5]
            for e, c in top_5:
                perc = (c / total_added) * 100
                add_desc += f"{e}: {c} ({perc:.2f}%)\n"
        else:
            add_desc += "Aucune réaction ajoutée."
        rem_desc = f"**Total retirées** : {total_removed}\n"
        if total_removed > 0:
            sorted_emojis = sorted(rem_stats["emoji_count"].items(), key=lambda x: x[1], reverse=True)
            top_5 = sorted_emojis[:5]
            for e, c in top_5:
                perc = (c / total_removed) * 100
                rem_desc += f"{e}: {c} ({perc:.2f}%)\n"
        else:
            rem_desc += "Aucune réaction retirée."
        embed.add_field(name="Ajout de réactions", value=add_desc, inline=False)
        embed.add_field(name="Retrait de réactions", value=rem_desc, inline=False)
        await ctx.send(embed=embed)

    @stats_main.command(name="voice")
    async def stats_voice(self, ctx: commands.Context):
        voice_stats = self.stats_data["voice"]
        total_joins = voice_stats["join_count"]
        total_leaves = voice_stats["leave_count"]
        desc = (
            f"**Total Joins**: {total_joins}\n"
            f"**Total Leaves**: {total_leaves}\n\n"
        )
        channel_joins = voice_stats["channel_joins"]
        if channel_joins:
            sorted_joins = sorted(channel_joins.items(), key=lambda x: x[1], reverse=True)[:5]
            desc += "**Top 5 salons (Join)**\n"
            for ch_id, cnt in sorted_joins:
                ch_obj = self.bot.get_channel(int(ch_id))
                ch_name = "#" + ch_obj.name if ch_obj else f"Unknown({ch_id})"
                desc += f"{ch_name} => {cnt} joins\n"
            desc += "\n"
        else:
            desc += "Aucun join par salon.\n"
        channel_leaves = voice_stats["channel_leaves"]
        if channel_leaves:
            sorted_leaves = sorted(channel_leaves.items(), key=lambda x: x[1], reverse=True)[:5]
            desc += "**Top 5 salons (Leave)**\n"
            for ch_id, cnt in sorted_leaves:
                ch_obj = self.bot.get_channel(int(ch_id))
                ch_name = "#" + ch_obj.name if ch_obj else f"Unknown({ch_id})"
                desc += f"{ch_name} => {cnt} leaves\n"
        else:
            desc += "Aucun leave par salon.\n"
        embed = discord.Embed(title="Statistiques Vocales", description=desc, color=0x8e44ad)
        await ctx.send(embed=embed)

    @stats_main.command(name="presence")
    async def stats_presence(self, ctx: commands.Context):
        pres_dict = self.stats_data["presence"]["status_changes"]
        if not pres_dict:
            return await ctx.send("Aucun changement de statut enregistré (ou presence_intent inactif).")
        total_changes = sum(pres_dict.values())
        desc = f"**Total changements de statut** : {total_changes}\n\n"
        sorted_changes = sorted(pres_dict.items(), key=lambda x: x[1], reverse=True)
        for transition, count in sorted_changes:
            perc = (count / total_changes) * 100
            desc += f"{transition} : {count} ({perc:.1f}%)\n"
        embed = discord.Embed(title="Statistiques de Présence (statuts)", description=desc, color=0xbdc3c7)
        await ctx.send(embed=embed)

    @stats_main.command(name="ladder")
    async def stats_ladder(self, ctx: commands.Context, *, arg: str = ""):
        """Proxy vers la commande !ladder pour l'exposer sous !stats."""
        ladder_command = self.bot.get_command("ladder")
        if ladder_command is None:
            await ctx.send(
                "La commande `!ladder` est actuellement indisponible."
                " Réessaie plus tard."
            )
            return
        await ctx.invoke(ladder_command, arg=arg)

    @stats_main.command(name="reset")
    @commands.has_role("Staff")
    async def stats_reset(self, ctx: commands.Context):
        self.reset_stats_data()
        await self.save_stats_data()
        await ctx.send("Les statistiques ont été **réinitialisées** avec succès.")

    @stats_main.command(name="on")
    @commands.has_role("Staff")
    async def stats_on(self, ctx: commands.Context):
        if self.stats_enabled:
            await ctx.send("La collecte de stats est déjà **active**.")
        else:
            self.stats_enabled = True
            await ctx.send("Collecte de stats **activée**.")

    @stats_main.command(name="off")
    @commands.has_role("Staff")
    async def stats_off(self, ctx: commands.Context):
        if not self.stats_enabled:
            await ctx.send("La collecte de stats est déjà **désactivée**.")
        else:
            self.stats_enabled = False
            await ctx.send("Collecte de stats **désactivée**.")

async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
