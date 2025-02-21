#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands, tasks
import json
import re
import asyncio
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from datetime import datetime
from calendrier import gen_cal

ORGA_CHANNEL_NAME = "organisation"
CONSOLE_CHANNEL_NAME = "console"
PINNED_JSON_FILENAME = "activities_data.json"
VALIDATED_ROLE_NAME = "Membre validé d'Evolution"
DATA_FILE = "activities_data.json"
DATE_TIME_REGEX = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})\s*(?:;|\s+)\s*(?P<time>\d{2}:\d{2})(?P<desc>.*)$")
LETTER_EMOJIS = [
    "🇦","🇧","🇨","🇩","🇪","🇫","🇬","🇭","🇮","🇯","🇰","🇱","🇲","🇳","🇴","🇵",
    "🇶","🇷","🇸","🇹","🇺","🇻","🇼","🇽","🇾","🇿"
]
SINGLE_EVENT_EMOJI = "✅"
MAX_GROUP_SIZE = 8

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
            i=d["id"], t=d["titre"], dt=dt, desc=d["description"],
            cid=d["creator_id"], rid=d["role_id"],
            reminder_24_sent=d.get("reminder_24_sent", False),
            reminder_1_sent=d.get("reminder_1_sent", False)
        )
        o.participants = d["participants"]
        o.cancelled = d["cancelled"]
        return o

class ActiviteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = {"next_id": 1, "events": {}}
        self.liste_message_map = {}
        self.single_event_msg_map = {}
        self.unsub_map = {}
        self.data_is_loaded = False
        self.check_events_loop.start()
    async def load_data_from_discord(self):
        if self.data_is_loaded:
            return
        console_chan = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if not console_chan:
            self.data_is_loaded = True
            return
        try:
            pinned = await console_chan.pins()
        except discord.Forbidden:
            pinned = []
        pinned_json_message = None
        for msg in pinned:
            if msg.author == self.bot.user and msg.attachments:
                for att in msg.attachments:
                    if att.filename == PINNED_JSON_FILENAME:
                        pinned_json_message = msg
                        break
            if pinned_json_message:
                break
        if not pinned_json_message:
            self.data_is_loaded = True
            return
        attachment = pinned_json_message.attachments[0]
        data_bytes = await attachment.read()
        try:
            raw_json = json.loads(data_bytes)
            self.data["next_id"] = raw_json.get("next_id", 1)
            self.data["events"] = {}
            for k, v in raw_json.get("events", {}).items():
                e = ActiviteData.from_dict(v)
                self.data["events"][k] = e
        except:
            pass
        self.data_is_loaded = True
    async def save_data_to_discord(self):
        if not self.data_is_loaded:
            return
        console_chan = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if not console_chan:
            return
        es = {}
        for k, v in self.data["events"].items():
            es[k] = v.to_dict()
        payload = {"next_id": self.data["next_id"], "events": es}
        json_str = json.dumps(payload, indent=2, ensure_ascii=False)
        data_bytes = json_str.encode("utf-8")
        try:
            pinned = await console_chan.pins()
        except discord.Forbidden:
            pinned = []
        for msg in pinned:
            if msg.author == self.bot.user and msg.attachments:
                for att in msg.attachments:
                    if att.filename == PINNED_JSON_FILENAME:
                        try:
                            await msg.unpin(reason="Nouveau snapshot d'activités.")
                        except:
                            pass
                        try:
                            await msg.delete()
                        except:
                            pass
                        break
        file_to_send = discord.File(io.BytesIO(data_bytes), filename=PINNED_JSON_FILENAME)
        try:
            new_msg = await console_chan.send(content="**Snapshot des activités** (sauvegarde automatique)", file=file_to_send)
            await new_msg.pin(reason="Sauvegarde activités")
        except:
            pass
    @tasks.loop(minutes=5)
    async def check_events_loop(self):
        if not self.bot.is_ready():
            return
        if not self.data_is_loaded:
            return
        now = datetime.now()
        org_channel = discord.utils.get(self.bot.get_all_channels(), name=ORGA_CHANNEL_NAME)
        if not org_channel:
            return
        for k, e in list(self.data["events"].items()):
            if e.cancelled:
                continue
            delta_seconds = (e.date_obj - now).total_seconds()
            if delta_seconds < 0:
                if e.role_id:
                    g = org_channel.guild
                    rr = g.get_role(e.role_id)
                    if rr:
                        try:
                            await rr.delete(reason="Activité terminée")
                        except:
                            pass
                self.data["events"].pop(k, None)
                await self.save_data_to_discord()
                continue
            hours_left = delta_seconds / 3600.0
            if 23.9 < hours_left < 24.1 and not e.reminder_24_sent:
                await self.envoyer_rappel(org_channel, e, "24h")
                e.reminder_24_sent = True
            if 0.9 < hours_left < 1.1 and not e.reminder_1_sent:
                await self.envoyer_rappel(org_channel, e, "1h")
                e.reminder_1_sent = True
        await self.save_data_to_discord()
    async def envoyer_rappel(self, channel, e, t):
        mention = f"<@&{e.role_id}>" if e.role_id else ""
        ds = e.date_obj.strftime("%d/%m/%Y à %H:%M")
        if t == "24h":
            message = f"⏰ **Rappel 24h** : {e.titre} démarre dans 24h.\n{mention}\nDébut le {ds}."
        else:
            message = f"⏰ **Rappel 1h** : {e.titre} démarre dans 1h.\n{mention}\nDébut le {ds}."
        try:
            await channel.send(message)
        except:
            pass
    @commands.Cog.listener()
    async def on_ready(self):
        if not self.data_is_loaded:
            await self.load_data_from_discord()
    def cog_unload(self):
        self.check_events_loop.cancel()
    @commands.command(name="activite")
    async def activite_main(self, ctx, action=None, *, args=None):
        if not self.data_is_loaded:
            return await ctx.send("Données en cours de chargement.")
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
        em = discord.Embed(
            title="Guide !activite",
            description="Actions: creer, liste, info, join, leave, annuler, modifier.",
            color=0x00AAFF
        )
        await ctx.send(embed=em)
    async def command_creer(self, ctx, line):
        if not line or line.strip() == "":
            return await ctx.send("Syntaxe: !activite creer <titre> <JJ/MM/AAAA HH:MM> <desc>")
        titre, dt, description = parse_date_time_via_regex(line)
        if not dt:
            return await ctx.send("Date/heure invalide.")
        for e_id, eobj in self.data["events"].items():
            if eobj.cancelled:
                continue
            if eobj.titre.lower() == titre.lower():
                pass
        guild = ctx.guild
        role_name = f"Sortie - {titre}"
        try:
            new_role = await guild.create_role(name=role_name)
        except Exception as ex:
            return await ctx.send(f"Impossible de créer le rôle : {ex}")
        event_id = str(self.data["next_id"])
        self.data["next_id"] += 1
        a = ActiviteData(event_id, titre, dt, description, ctx.author.id, new_role.id)
        self.data["events"][event_id] = a
        await self.save_data_to_discord()
        try:
            await ctx.author.add_roles(new_role)
        except:
            pass
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
                description=f"Date : {ds}\nDesc : {description or '(aucune)'}\nRéagissez avec {SINGLE_EVENT_EMOJI}\nID = {event_id}",
                color=0x44DD55
            )
            msg = await org_chan.send(
                content=f"{mention} Activité proposée par {ctx.author.mention}",
                embed=ev_embed
            )
            await msg.add_reaction(SINGLE_EVENT_EMOJI)
            self.single_event_msg_map[msg.id] = event_id
    async def command_liste(self, ctx):
        now = datetime.now()
        upcoming = []
        for k, e in self.data["events"].items():
            if e.cancelled:
                continue
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
            org_name = org.display_name if org else "Inconnu"
            ro = f"<@&{ev.role_id}>" if ev.role_id else "Aucun"
            participant_names = []
            for p_id in ev.participants:
                mem = ctx.guild.get_member(p_id)
                participant_names.append(mem.display_name if mem else f"<@{p_id}>")
            pstr = ", ".join(participant_names) if participant_names else "Aucun"
            txt = (
                f"ID : {ev.id}\n"
                f"Date : {ds}\n"
                f"Organisateur : {org_name}\n"
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
        if args not in self.data["events"]:
            return await ctx.send("Introuvable.")
        e = self.data["events"][args]
        em = discord.Embed(title=f"Infos : {e.titre} (ID={e.id})", color=0xFFC107)
        em.add_field(name="Date/Heure", value=e.date_obj.strftime("%d/%m/%Y %H:%M"), inline=False)
        em.add_field(name="Annulée", value="Oui" if e.cancelled else "Non", inline=True)
        em.add_field(name="Description", value=e.description or "Aucune", inline=False)
        org = ctx.guild.get_member(e.creator_id)
        org_name = org.display_name if org else "Inconnu"
        em.add_field(name="Organisateur", value=org_name, inline=False)
        participant_names = []
        for p_id in e.participants:
            mem = ctx.guild.get_member(p_id)
            participant_names.append(mem.display_name if mem else f"<@{p_id}>")
        pstr = ", ".join(participant_names) if participant_names else "Aucun"
        pc = len(participant_names)
        em.add_field(name=f"Participants ({pc}/{MAX_GROUP_SIZE})", value=pstr, inline=False)
        if e.role_id:
            em.add_field(name="Rôle", value=f"<@&{e.role_id}>", inline=True)
        await ctx.send(embed=em)
    async def command_join(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite join <id>")
        if args not in self.data["events"]:
            return await ctx.send("Introuvable.")
        e = self.data["events"][args]
        if e.cancelled:
            return await ctx.send("Annulée.")
        if len(e.participants) >= MAX_GROUP_SIZE:
            return await ctx.send("Groupe complet.")
        if ctx.author.id in e.participants:
            return await ctx.send("Déjà inscrit.")
        e.participants.append(ctx.author.id)
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.add_roles(r)
                except:
                    pass
        await ctx.send(f"{ctx.author.mention} rejoint {e.titre} (ID={args}).")
    async def command_leave(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite leave <id>")
        if args not in self.data["events"]:
            return await ctx.send("Introuvable.")
        e = self.data["events"][args]
        if ctx.author.id not in e.participants:
            return await ctx.send("Pas inscrit.")
        e.participants.remove(ctx.author.id)
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.remove_roles(r)
                except:
                    pass
        await ctx.send(f"{ctx.author.mention} se retire de {e.titre} (ID={args}).")
    async def command_annuler(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite annuler <id>")
        if args not in self.data["events"]:
            return await ctx.send("Introuvable.")
        e = self.data["events"][args]
        if not self.can_modify(ctx, e):
            return await ctx.send("Non autorisé.")
        e.cancelled = True
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await r.delete(reason="Annulation.")
                except:
                    pass
        await ctx.send(f"{e.titre} annulée.")
    async def command_modifier(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite modifier <id> <JJ/MM/AAAA HH:MM> <desc>")
        parts = args.split(" ", 1)
        if len(parts) < 2:
            return await ctx.send("Exemple: !activite modifier 3 12/05/2025 19:30 Desc")
        event_id = parts[0]
        rest = parts[1]
        if event_id not in self.data["events"]:
            return await ctx.send("Introuvable.")
        e = self.data["events"][event_id]
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
        await self.save_data_to_discord()
        await ctx.send(f"{e.titre} (ID={event_id}) modifiée. Nouvelle date: {dt.strftime('%d/%m/%Y %H:%M')}")
    @commands.command(name="calendrier")
    async def afficher_calendrier(self, ctx):
        now = datetime.now()
        annee = now.year
        mois = now.month
        try:
            bg = mpimg.imread("calendrier1.png")
        except:
            bg = None
        buf = gen_cal(self.data["events"], bg, annee, mois)
        file_cal = discord.File(fp=buf, filename="calendrier.png")
        msg = await ctx.send(file=file_cal)
        await msg.add_reaction("⬅️")
        await msg.add_reaction("➡️")
        def check(reaction, user):
            return user == ctx.author and reaction.message.id == msg.id and str(reaction.emoji) in ["⬅️","➡️"]
        while True:
            try:
                reac, usr = await self.bot.wait_for("reaction_add", timeout=60, check=check)
            except asyncio.TimeoutError:
                try:
                    await msg.clear_reactions()
                except:
                    pass
                break
            else:
                try:
                    await msg.remove_reaction(reac.emoji, usr)
                except:
                    pass
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
                except:
                    pass
                new_buf = gen_cal(self.data["events"], bg, annee, mois)
                new_file_cal = discord.File(fp=new_buf, filename="calendrier.png")
                msg = await ctx.send(file=new_file_cal)
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
        event_id = mapping[emj]
        e = self.data["events"].get(event_id)
        if not e or e.cancelled:
            await reaction.message.channel.send("Annulée ou introuvable.")
            return
        if len(e.participants) >= MAX_GROUP_SIZE:
            await reaction.message.channel.send("Complet.")
            return
        if user.id in e.participants:
            await reaction.message.channel.send("Déjà inscrit.")
            return
        e.participants.append(user.id)
        await self.save_data_to_discord()
        if e.role_id:
            role = reaction.message.guild.get_role(e.role_id)
            if role:
                try:
                    await user.add_roles(role)
                except:
                    pass
        await reaction.message.channel.send(f"{user.mention} rejoint {e.titre} (ID={e.id}).")
    async def handle_reaction_single_event(self, reaction, user):
        if str(reaction.emoji) != SINGLE_EVENT_EMOJI:
            return
        event_id = self.single_event_msg_map[reaction.message.id]
        e = self.data["events"].get(event_id)
        if not e or e.cancelled:
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
        await self.save_data_to_discord()
        if e.role_id:
            role = reaction.message.guild.get_role(e.role_id)
            if role:
                try:
                    await user.add_roles(role)
                except:
                    pass
        await reaction.message.channel.send(f"{user.mention} rejoint {e.titre} (ID={e.id}).")
    async def handle_unsubscribe_dm(self, reaction, user):
        pass
    def can_modify(self, ctx, e):
        if ctx.author.id == e.creator_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        return False
    def has_validated_role(self, member):
        return any(r.name == VALIDATED_ROLE_NAME for r in member.roles)

async def setup(bot: commands.Bot):
    cog = ActiviteCog(bot)
    bot.add_cog(cog)
    try:
        await bot.wait_until_ready()
        await cog.load_data_from_discord()
    except:
        pass
