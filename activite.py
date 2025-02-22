#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import asyncio
import io
import logging
import unicodedata
import discord
from discord.ext import commands, tasks
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from datetime import datetime
from calendrier import gen_cal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ORGA_CHANNEL_NAME = "organisation"
CONSOLE_CHANNEL_NAME = "console"
DATA_FILE = "activities_data.json"
MARKER_TEXT = "===BOTACTIVITES==="
VALIDATED_ROLE_NAME = "Membre validé d'Evolution"
DATE_TIME_REGEX = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})\s*(?:;|\s+)\s*(?P<time>\d{2}:\d{2})(?P<desc>.*)$")
LETTER_EMOJIS = [
    "🇦","🇧","🇨","🇩","🇪","🇫","🇬","🇭","🇮","🇯","🇰","🇱","🇲","🇳","🇴","🇵",
    "🇶","🇷","🇸","🇹","🇺","🇻","🇼","🇽","🇾","🇿"
]
SINGLE_EVENT_EMOJI = "✅"
MAX_GROUP_SIZE = 8

def normalize_string(s: str):
    nf = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')

def parse_date_time(date_str, time_str):
    try:
        d, m, y = date_str.split("/")
        h, mi = time_str.split(":")
        return datetime(int(y), int(m), int(d), int(h), int(mi))
    except ValueError:
        return None

def parse_date_time_via_regex(line):
    mat = DATE_TIME_REGEX.search(line)
    if not mat:
        return None, None, None
    ds = mat.group("date").strip()
    ts = mat.group("time").strip()
    leftover = mat.group("desc").strip()
    title_part = line[:mat.start()].strip()
    dt = parse_date_time(ds, ts)
    if not dt:
        return None, None, None
    if not title_part:
        title_part = "SansTitre"
    return title_part, dt, leftover

class ActiviteData:
    def __init__(self, i, t, dt, desc, cid, rid=None, reminder_24_sent=False, reminder_1_sent=False):
        self.id = i
        self.titre = t
        self.date_obj = dt
        self.description = desc
        self.creator_id = cid
        self.role_id = rid
        self.participants = []
        self.cancelled = False
        self.reminder_24_sent = reminder_24_sent
        self.reminder_1_sent = reminder_1_sent

    def to_dict(self):
        return {
            "id": self.id,
            "titre": self.titre,
            "date_str": self.date_obj.strftime("%Y-%m-%d %H:%M:%S"),
            "description": self.description,
            "creator_id": self.creator_id,
            "role_id": self.role_id,
            "participants": self.participants,
            "cancelled": self.cancelled,
            "reminder_24_sent": self.reminder_24_sent,
            "reminder_1_sent": self.reminder_1_sent
        }

    @staticmethod
    def from_dict(d):
        dt = datetime.strptime(d["date_str"], "%Y-%m-%d %H:%M:%S")
        o = ActiviteData(
            i=d["id"],
            t=d["titre"],
            dt=dt,
            desc=d["description"],
            cid=d["creator_id"],
            rid=d["role_id"],
            reminder_24_sent=d.get("reminder_24_sent", False),
            reminder_1_sent=d.get("reminder_1_sent", False)
        )
        o.participants = d["participants"]
        o.cancelled = d["cancelled"]
        return o

class ActiviteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.activities_data = {"next_id": 1, "events": {}}
        self.initialized = False
        self.liste_message_map = {}
        self.single_event_msg_map = {}
        self.unsub_map = {}

    async def cog_load(self):
        await self.initialize_data()
        self.check_events_loop.start()

    async def initialize_data(self):
        console_channel = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if console_channel:
            async for msg in console_channel.history(limit=1000, oldest_first=False):
                if msg.author == self.bot.user and MARKER_TEXT in msg.content:
                    try:
                        start_idx = msg.content.index("```json\n") + len("```json\n")
                        end_idx = msg.content.rindex("\n```")
                        raw_json = msg.content[start_idx:end_idx]
                        data_loaded = json.loads(raw_json)
                        self.activities_data = data_loaded
                        break
                    except Exception as e:
                        logger.warning(f"Impossible de parser le JSON dans un message console: {e}")

        if (self.activities_data.get("events") is None or len(self.activities_data["events"]) == 0) and os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.activities_data = json.load(f)
            except Exception as e:
                logger.warning(f"Impossible de charger le fichier local {DATA_FILE}: {e}")

        self.initialized = True
        logger.info("ActiviteCog : données initialisées.")

    def save_data_local(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.activities_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Erreur lors de la sauvegarde locale : {e}")

    async def dump_data_to_console(self, ctx):
        console_channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not console_channel:
            return
        await self._dump_data_to_console_channel(console_channel)

    async def dump_data_to_console_no_ctx(self, guild: discord.Guild):
        console_channel = discord.utils.get(guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not console_channel:
            return
        await self._dump_data_to_console_channel(console_channel)

    async def _dump_data_to_console_channel(self, console_channel: discord.TextChannel):
        data_str = json.dumps(self.activities_data, indent=4, ensure_ascii=False)
        marker = MARKER_TEXT
        if len(data_str) < 1900:
            await console_channel.send(f"{marker}\n```json\n{data_str}\n```")
        else:
            temp_file_path = self._as_temp_file(data_str)
            await console_channel.send(
                f"{marker} (fichier)",
                file=discord.File(fp=temp_file_path, filename="activities_data.json")
            )

    def _as_temp_file(self, data_str):
        temp_path = "temp_activities_data.json"
        try:
            with open(temp_path, "w", encoding="utf-8") as tmp:
                tmp.write(data_str)
        except:
            pass
        return temp_path

    @tasks.loop(minutes=5)
    async def check_events_loop(self):
        if not self.bot.is_ready():
            return
        if not self.initialized:
            return
        now = datetime.now()
        org_channel = discord.utils.get(self.bot.get_all_channels(), name=ORGA_CHANNEL_NAME)
        if not org_channel:
            return

        if "events" not in self.activities_data:
            return

        to_delete = []
        modified = False
        for k, e_data in self.activities_data["events"].items():
            if e_data["cancelled"]:
                continue
            evt = ActiviteData.from_dict(e_data)
            delta_seconds = (evt.date_obj - now).total_seconds()
            if delta_seconds < 0:
                if evt.role_id:
                    rr = org_channel.guild.get_role(evt.role_id)
                    if rr:
                        try:
                            await rr.delete(reason="Activité terminée")
                        except Exception as ex:
                            logger.warning(f"Erreur lors de la suppression du rôle {rr.name}: {ex}")
                to_delete.append(k)
                continue

            hours_left = delta_seconds / 3600.0
            if 23.9 < hours_left < 24.1 and not evt.reminder_24_sent:
                await self.envoyer_rappel(org_channel, evt, "24h")
                e_data["reminder_24_sent"] = True
                modified = True
            if 0.9 < hours_left < 1.1 and not evt.reminder_1_sent:
                await self.envoyer_rappel(org_channel, evt, "1h")
                e_data["reminder_1_sent"] = True
                modified = True

        for kdel in to_delete:
            del self.activities_data["events"][kdel]
            modified = True

        if modified:
            self.save_data_local()
            # Pour le cas où pas de ctx ici, on dump dans console en "no_ctx"
            if org_channel:
                await self.dump_data_to_console_no_ctx(org_channel.guild)

    async def envoyer_rappel(self, channel, e: ActiviteData, t: str):
        mention = f"<@&{e.role_id}>" if e.role_id else ""
        ds = e.date_obj.strftime("%d/%m/%Y à %H:%M")
        if t == "24h":
            message = f"⏰ **Rappel 24h** : {e.titre} démarre dans 24h.\n{mention}\nDébut le {ds}."
        else:
            message = f"⏰ **Rappel 1h** : {e.titre} démarre dans 1h.\n{mention}\nDébut le {ds}."
        try:
            await channel.send(message)
        except Exception as ex:
            logger.warning(f"Impossible d'envoyer le rappel dans {channel}: {ex}")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.initialized:
            await self.initialize_data()

    def cog_unload(self):
        self.check_events_loop.cancel()

    @commands.command(name="activite")
    async def activite_main(self, ctx, action=None, *, args=None):
        if not self.initialized:
            await ctx.send("Données en cours de chargement, réessayez dans un instant.")
            return
        if not action:
            await ctx.send("Actions: guide, creer, liste, info, join, leave, annuler, modifier.")
            return

        a = action.lower()
        if a == "guide":
            await self.command_guide(ctx)
        elif a == "creer":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Rôle invalide.")
            await self.command_creer(ctx, args)
        elif a == "liste":
            await self.command_liste(ctx)
        elif a == "info":
            await self.command_info(ctx, args)
        elif a == "join":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Rôle invalide.")
            await self.command_join(ctx, args)
        elif a == "leave":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Rôle invalide.")
            await self.command_leave(ctx, args)
        elif a == "annuler":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Rôle invalide.")
            await self.command_annuler(ctx, args)
        elif a == "modifier":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Rôle invalide.")
            await self.command_modifier(ctx, args)
        else:
            await ctx.send("Action inconnue.")

    async def command_guide(self, ctx):
        txt = (
            "**Guide d’utilisation de la commande !activite**\n\n"
            "**1) !activite creer <titre> <JJ/MM/AAAA HH:MM> <description>**\n"
            "→ Crée une nouvelle activité. Exemple : `!activite creer Donjon 01/04/2025 20:30 Préparez vos potions!`\n"
            "\n"
            "**2) !activite liste**\n"
            "→ Liste toutes les activités à venir.\n"
            "\n"
            "**3) !activite info <id>**\n"
            "→ Affiche les détails d’une activité. Exemple : `!activite info 3`\n"
            "\n"
            "**4) !activite join <id>** / **!activite leave <id>**\n"
            "→ Rejoindre ou quitter une activité donnée. Exemple : `!activite join 3`\n"
            "\n"
            "**5) !activite annuler <id>**\n"
            "→ Annuler une activité (si vous êtes le créateur ou un administrateur).\n"
            "\n"
            "**6) !activite modifier <id> <JJ/MM/AAAA HH:MM> <description>**\n"
            "→ Modifier la date ou la description d’une activité.\n"
        )
        embed = discord.Embed(title="Guide Complet : !activite", description=txt, color=0x00AAFF)
        await ctx.send(embed=embed)

    async def command_creer(self, ctx, line):
        if not line or line.strip() == "":
            return await ctx.send("Syntaxe: !activite creer <titre> <JJ/MM/AAAA HH:MM> <desc>")
        titre, dt, description = parse_date_time_via_regex(line)
        if not dt:
            return await ctx.send("Date/heure invalide.")
        guild = ctx.guild
        role_name = f"Sortie - {titre}"
        try:
            new_role = await guild.create_role(name=role_name)
        except Exception as ex:
            return await ctx.send(f"Impossible de créer le rôle : {ex}")

        event_id = str(self.activities_data.get("next_id", 1))
        self.activities_data["next_id"] = int(event_id) + 1

        a = ActiviteData(event_id, titre, dt, description, ctx.author.id, new_role.id)
        if "events" not in self.activities_data:
            self.activities_data["events"] = {}
        self.activities_data["events"][event_id] = a.to_dict()

        self.save_data_local()
        await self.dump_data_to_console(ctx)

        try:
            await ctx.author.add_roles(new_role)
        except Exception as ex:
            logger.warning(f"Impossible d'ajouter le rôle au créateur: {ex}")

        ds = dt.strftime("%d/%m/%Y à %H:%M")
        em = discord.Embed(
            title=f"Création: {titre}",
            description=description or "Aucune description",
            color=0x00FF00
        )
        em.add_field(name="Date/Heure", value=ds, inline=False)
        em.add_field(name="ID", value=event_id, inline=True)
        await ctx.send(embed=em)

        org_chan = discord.utils.get(guild.text_channels, name=ORGA_CHANNEL_NAME)
        if org_chan:
            val_role = discord.utils.get(guild.roles, name=VALIDATED_ROLE_NAME)
            mention = f"<@&{val_role.id}>" if val_role else "@everyone"
            ev_embed = discord.Embed(
                title=f"Nouvelle proposition : {titre}",
                description=(
                    f"Date : {ds}\n"
                    f"Desc : {description or '(aucune)'}\n"
                    f"Réagissez avec {SINGLE_EVENT_EMOJI}\n"
                    f"ID = {event_id}"
                ),
                color=0x44DD55
            )
            msg = await org_chan.send(
                content=f"{mention} Activité proposée par {ctx.author.mention}",
                embed=ev_embed
            )
            await msg.add_reaction(SINGLE_EVENT_EMOJI)
            self.single_event_msg_map[msg.id] = event_id

    async def command_liste(self, ctx):
        if "events" not in self.activities_data:
            return await ctx.send("Aucune activité enregistrée.")

        now = datetime.now()
        upcoming = []
        for k, ev_dict in self.activities_data["events"].items():
            if ev_dict["cancelled"]:
                continue
            e = ActiviteData.from_dict(ev_dict)
            if e.date_obj > now:
                upcoming.append(e)
        if not upcoming:
            return await ctx.send("Aucune activité à venir.")

        upcoming.sort(key=lambda x: x.date_obj)
        em = discord.Embed(title="Activités à venir", color=0x3498db)

        emoji_to_event_id = {}
        for i, ev in enumerate(upcoming):
            if i >= len(LETTER_EMOJIS):
                break
            emj = LETTER_EMOJIS[i]
            ds = ev.date_obj.strftime("%d/%m %H:%M")
            pc = len(ev.participants)
            org = ctx.guild.get_member(ev.creator_id)
            on = org.display_name if org else "Inconnu"
            ro = f"<@&{ev.role_id}>" if ev.role_id else "Aucun"

            plist = []
            for pid in ev.participants:
                mem = ctx.guild.get_member(pid)
                plist.append(mem.display_name if mem else f"<@{pid}>")
            pstr = ", ".join(plist) if plist else "Aucun"
            txt = (
                f"ID : {ev.id}\n"
                f"Date : {ds}\n"
                f"Organisateur : {on}\n"
                f"Participants ({pc}/{MAX_GROUP_SIZE}) : {pstr}\n"
                f"Rôle : {ro}\n"
                f"---\n{ev.description or '*Aucune description*'}"
            )
            em.add_field(name=f"{emj} : {ev.titre}", value=txt, inline=False)
            emoji_to_event_id[emj] = ev.id

        msg_sent = await ctx.send(embed=em)
        for i in range(len(upcoming)):
            if i >= len(LETTER_EMOJIS):
                break
            await msg_sent.add_reaction(LETTER_EMOJIS[i])

        self.liste_message_map[msg_sent.id] = emoji_to_event_id

    async def command_info(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe : !activite info <id>")
        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        em = discord.Embed(title=f"Infos : {e.titre} (ID={e.id})", color=0xFFC107)
        em.add_field(name="Date/Heure", value=e.date_obj.strftime("%d/%m/%Y %H:%M"), inline=False)
        em.add_field(name="Annulée", value="Oui" if e.cancelled else "Non", inline=True)
        em.add_field(name="Description", value=e.description or "Aucune", inline=False)
        org = ctx.guild.get_member(e.creator_id)
        on = org.display_name if org else "Inconnu"

        em.add_field(name="Organisateur", value=on, inline=False)
        plist = []
        for pid in e.participants:
            mem = ctx.guild.get_member(pid)
            plist.append(mem.display_name if mem else f"<@{pid}>")
        pc = len(plist)
        pstr = ", ".join(plist) if plist else "Aucun"
        em.add_field(name=f"Participants ({pc}/{MAX_GROUP_SIZE})", value=pstr, inline=False)
        if e.role_id:
            em.add_field(name="Rôle", value=f"<@&{e.role_id}>", inline=True)
        await ctx.send(embed=em)

    async def command_join(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite join <id>")
        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        if e.cancelled:
            return await ctx.send("Annulée.")
        if len(e.participants) >= MAX_GROUP_SIZE:
            return await ctx.send("Groupe complet.")
        if ctx.author.id in e.participants:
            return await ctx.send("Déjà inscrit.")

        e.participants.append(ctx.author.id)
        self.activities_data["events"][args] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console(ctx)

        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.add_roles(r)
                except Exception as ex:
                    logger.warning(f"Impossible d'ajouter le rôle à {ctx.author}: {ex}")

        await ctx.send(f"{ctx.author.mention} rejoint {e.titre} (ID={args}).")

    async def command_leave(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite leave <id>")
        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        if ctx.author.id not in e.participants:
            return await ctx.send("Pas inscrit.")

        e.participants.remove(ctx.author.id)
        self.activities_data["events"][args] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console(ctx)

        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.remove_roles(r)
                except Exception as ex:
                    logger.warning(f"Impossible de retirer le rôle à {ctx.author}: {ex}")

        await ctx.send(f"{ctx.author.mention} se retire de {e.titre} (ID={args}).")

    async def command_annuler(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite annuler <id>")
        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        if not self.can_modify(ctx, e):
            return await ctx.send("Non autorisé.")

        e.cancelled = True
        self.activities_data["events"][args] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console(ctx)

        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await r.delete(reason="Annulation.")
                except Exception as ex:
                    logger.warning(f"Impossible de supprimer le rôle pour annulation: {ex}")

        await ctx.send(f"{e.titre} annulée.")

    async def command_modifier(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite modifier <id> <JJ/MM/AAAA HH:MM> <desc>")
        parts = args.split(" ", 1)
        if len(parts) < 2:
            return await ctx.send("Exemple: !activite modifier 3 12/05/2025 19:30 Desc")

        event_id = parts[0]
        rest = parts[1]
        if "events" not in self.activities_data or event_id not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][event_id]
        e = ActiviteData.from_dict(e_dict)
        if not self.can_modify(ctx, e):
            return await ctx.send("Non autorisé.")
        if e.cancelled:
            return await ctx.send("Déjà annulée.")

        mat = DATE_TIME_REGEX.search(rest)
        if not mat:
            return await ctx.send("Date/heure non trouvée.")

        ds = mat.group("date").strip()
        ts = mat.group("time").strip()
        nd = mat.group("desc").strip()
        dt = parse_date_time(ds, ts)
        if not dt:
            return await ctx.send("Date invalide.")

        e.date_obj = dt
        e.description = nd
        self.activities_data["events"][event_id] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console(ctx)

        await ctx.send(f"{e.titre} (ID={event_id}) modifiée. Nouvelle date: {dt.strftime('%d/%m/%Y %H:%M')}")

    @commands.command(name="calendrier")
    async def afficher_calendrier(self, ctx):
        if not self.initialized:
            return await ctx.send("Données en cours de chargement.")

        now = datetime.now()
        annee = now.year
        mois = now.month

        try:
            bg = mpimg.imread("calendrier1.png")
        except Exception as e:
            logger.info(f"Impossible de charger 'calendrier1.png': {e}")
            bg = None

        all_events = {}
        if "events" in self.activities_data:
            for k, v in self.activities_data["events"].items():
                all_events[k] = ActiviteData.from_dict(v)

        buf = gen_cal(all_events, bg, annee, mois)
        file_cal = discord.File(fp=buf, filename="calendrier.png")
        msg = await ctx.send(file=file_cal)
        await msg.add_reaction("⬅️")
        await msg.add_reaction("➡️")

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == msg.id
                and str(reaction.emoji) in ["⬅️", "➡️"]
            )

        while True:
            try:
                reac, usr = await self.bot.wait_for("reaction_add", timeout=60, check=check)
            except asyncio.TimeoutError:
                try:
                    await msg.clear_reactions()
                except Exception as ex:
                    logger.warning(f"Impossible de clear_reactions: {ex}")
                break
            else:
                try:
                    await msg.remove_reaction(reac.emoji, usr)
                except Exception as ex:
                    logger.warning(f"Impossible de remove_reaction: {ex}")
                if str(reac.emoji) == "➡️":
                    mois += 1
                    if mois > 12:
                        mois = 1
                        annee += 1
                else:
                    mois -= 1
                    if mois < 1:
                        mois = 12
                        annee -= 1
                try:
                    await msg.delete()
                except Exception as ex:
                    logger.warning(f"Impossible de supprimer l'ancien message de calendrier: {ex}")
                buf = gen_cal(all_events, bg, annee, mois)
                file_cal = discord.File(fp=buf, filename="calendrier.png")
                msg = await ctx.send(file=file_cal)
                await msg.add_reaction("⬅️")
                await msg.add_reaction("➡️")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        if reaction.message.id in self.liste_message_map:
            await self.handle_reaction_list(reaction, user)
        elif reaction.message.id in self.single_event_msg_map:
            await self.handle_reaction_single_event(reaction, user)
        elif reaction.message.id in self.unsub_map:
            await self.handle_unsubscribe_dm(reaction, user)

    async def handle_reaction_list(self, reaction, user):
        mapping = self.liste_message_map[reaction.message.id]
        emj = str(reaction.emoji)
        if emj not in mapping:
            return
        if not self.has_validated_role(user):
            await reaction.message.channel.send(f"{user.mention} : Rôle invalide.")
            return
        event_id = mapping[emj]
        guild = reaction.message.guild
        if "events" not in self.activities_data or event_id not in self.activities_data["events"]:
            await reaction.message.channel.send("Annulée ou introuvable.")
            return

        e_dict = self.activities_data["events"][event_id]
        e = ActiviteData.from_dict(e_dict)
        if e.cancelled:
            await reaction.message.channel.send("Annulée.")
            return
        if len(e.participants) >= MAX_GROUP_SIZE:
            await reaction.message.channel.send("Complet.")
            return
        if user.id in e.participants:
            await reaction.message.channel.send("Déjà inscrit.")
            return

        e.participants.append(user.id)
        self.activities_data["events"][event_id] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console_no_ctx(guild)

        if e.role_id:
            role = guild.get_role(e.role_id)
            if role:
                try:
                    await user.add_roles(role)
                except Exception as ex:
                    logger.warning(f"Impossible d'ajouter le rôle à {user}: {ex}")

        await reaction.message.channel.send(f"{user.mention} rejoint {e.titre} (ID={e.id}).")

    async def handle_reaction_single_event(self, reaction, user):
        if str(reaction.emoji) != SINGLE_EVENT_EMOJI:
            return
        event_id = self.single_event_msg_map[reaction.message.id]
        guild = reaction.message.guild
        if "events" not in self.activities_data or event_id not in self.activities_data["events"]:
            await reaction.message.channel.send("Annulée ou introuvable.")
            return

        e_dict = self.activities_data["events"][event_id]
        e = ActiviteData.from_dict(e_dict)
        if e.cancelled:
            await reaction.message.channel.send("Annulée ou introuvable.")
            return
        if len(e.participants) >= MAX_GROUP_SIZE:
            await reaction.message.channel.send("Complet.")
            return
        if user.id in e.participants:
            await reaction.message.channel.send("Déjà inscrit.")
            return
        if not self.has_validated_role(user):
            await reaction.message.channel.send(f"{user.mention} rôle invalide.")
            return

        e.participants.append(user.id)
        self.activities_data["events"][event_id] = e.to_dict()

        self.save_data_local()
        await self.dump_data_to_console_no_ctx(guild)

        if e.role_id:
            role = guild.get_role(e.role_id)
            if role:
                try:
                    await user.add_roles(role)
                except Exception as ex:
                    logger.warning(f"Impossible d'ajouter le rôle (event unique) à {user}: {ex}")

        await reaction.message.channel.send(f"{user.mention} rejoint {e.titre} (ID={e.id}).")

    async def handle_unsubscribe_dm(self, reaction, user):
        pass

    def can_modify(self, ctx, e: ActiviteData):
        if ctx.author.id == e.creator_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        return False

    def has_validated_role(self, member: discord.Member):
        return any(r.name == VALIDATED_ROLE_NAME for r in member.roles)

async def setup(bot: commands.Bot):
    await bot.add_cog(ActiviteCog(bot))
