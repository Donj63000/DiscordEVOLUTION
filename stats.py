import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime

DATA_FILE = "stats_data.json"

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

class StatsCog(commands.Cog):
    """
    Cog de statistiques exhaustives : messages, éditions, suppressions, réactions,
    salons vocaux, présence, etc.

    Enregistre :
      - Des compteurs agrégés (messages par canal, par rôle, par heure, par user…)
      - Un log détaillé de tous les événements, avec timestamp et métadonnées

    Rajout :
      - Commande !stats reset (Staff) pour remettre à zéro les compteurs
      - Commandes !stats on / !stats off (Staff) pour activer/désactiver la collecte
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stats_enabled = True
        self.stats_data = {
            "messages": {
                "channel_count": {},
                "role_count": {},
                "hour_count": {},
                "user_count": {},
                "total": 0
            },
            "edits": {
                "channel_count": {},
                "hour_count": {},
                "total": 0
            },
            "deletions": {
                "channel_count": {},
                "hour_count": {},
                "total": 0
            },
            "reactions_added": {
                "emoji_count": {},
                "hour_count": {},
                "total": 0
            },
            "reactions_removed": {
                "emoji_count": {},
                "hour_count": {},
                "total": 0
            },
            "voice": {
                "join_count": 0,
                "leave_count": 0,
                "channel_joins": {},
                "channel_leaves": {}
            },
            "presence": {
                "status_changes": {}
            },
            "logs": {
                "messages_created": [],
                "messages_edited": [],
                "messages_deleted": [],
                "reactions": [],
                "voice": [],
                "presence": []
            }
        }
        self.load_stats_data()
        self.save_loop.start()

    def cog_unload(self):
        self.save_loop.cancel()

    def load_stats_data(self):
        if os.path.isfile(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for top_key in self.stats_data.keys():
                    if top_key in loaded:
                        if isinstance(self.stats_data[top_key], dict) and isinstance(loaded[top_key], dict):
                            self.stats_data[top_key].update(loaded[top_key])
                        else:
                            self.stats_data[top_key] = loaded[top_key]
            except Exception as e:
                print(f"[Stats] Erreur lors du chargement de {DATA_FILE} : {e}")

    def save_stats_data(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.stats_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Stats] Erreur lors de la sauvegarde : {e}")

    @tasks.loop(seconds=60.0)
    async def save_loop(self):
        self.save_stats_data()

    def reset_stats_data(self):
        self.stats_data = {
            "messages": {
                "channel_count": {},
                "role_count": {},
                "hour_count": {},
                "user_count": {},
                "total": 0
            },
            "edits": {
                "channel_count": {},
                "hour_count": {},
                "total": 0
            },
            "deletions": {
                "channel_count": {},
                "hour_count": {},
                "total": 0
            },
            "reactions_added": {
                "emoji_count": {},
                "hour_count": {},
                "total": 0
            },
            "reactions_removed": {
                "emoji_count": {},
                "hour_count": {},
                "total": 0
            },
            "voice": {
                "join_count": 0,
                "leave_count": 0,
                "channel_joins": {},
                "channel_leaves": {}
            },
            "presence": {
                "status_changes": {}
            },
            "logs": {
                "messages_created": [],
                "messages_edited": [],
                "messages_deleted": [],
                "reactions": [],
                "voice": [],
                "presence": []
            }
        }

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
        for role in message.author.roles:
            if role.is_default():
                continue
            r_id = str(role.id)
            msg_stats["role_count"].setdefault(r_id, 0)
            msg_stats["role_count"][r_id] += 1
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
            "roles": [str(r.id) for r in message.author.roles if not r.is_default()],
            "content": message.content
        }
        self.stats_data["logs"]["messages_created"].append(msg_log)

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
        self.stats_data["logs"]["messages_edited"].append(edit_log)

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
        self.stats_data["logs"]["messages_deleted"].append(del_log)

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
        self.stats_data["logs"]["reactions"].append(react_log)

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
        self.stats_data["logs"]["reactions"].append(react_log)

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
        self.stats_data["logs"]["voice"].append(voice_log_entry)

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
            self.stats_data["logs"]["presence"].append(presence_log)

    @commands.group(name="stats", invoke_without_command=True)
    async def stats_main(self, ctx: commands.Context):
        usage_text = (
            "**Commandes stats :**\n"
            "`!stats all` => Vue d'ensemble\n"
            "`!stats messages` => Statistiques messages\n"
            "`!stats users` => Top utilisateurs\n"
            "`!stats edits` => Éditions\n"
            "`!stats deletions` => Suppressions\n"
            "`!stats reactions` => Réactions\n"
            "`!stats voice` => Activité vocale\n"
            "`!stats presence` => Statuts (présence)\n"
            "`!stats reset` => (Staff) Reset complet\n"
            "`!stats on/off` => (Staff) Activer / Désactiver la collecte\n"
        )
        await ctx.send(usage_text)

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

    @stats_main.command(name="reset")
    @commands.has_role("Staff")
    async def stats_reset(self, ctx: commands.Context):
        self.reset_stats_data()
        self.save_stats_data()
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
