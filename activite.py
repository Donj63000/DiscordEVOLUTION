#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands, tasks
import json
import os
import re
import asyncio
import io
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from datetime import datetime
from calendrier import gen_cal

# -- Paramètres et constantes
ORGA_CHANNEL_NAME = "organisation"
CONSOLE_CHANNEL_NAME = "console"  # <-- Salon où l'on stocke et lit le JSON
PINNED_JSON_FILENAME = "activities_data.json"

VALIDATED_ROLE_NAME = "Membre validé d'Evolution"
DATA_FILE = "activities_data.json"  # <-- N'est plus utilisé localement, conservé si besoin fallback
DATE_TIME_REGEX = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})\s*(?:;|\s+)\s*(?P<time>\d{2}:\d{2})(?P<desc>.*)$")

LETTER_EMOJIS = [
    "🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭",
    "🇮", "🇯", "🇰", "🇱", "🇲", "🇳", "🇴", "🇵",
    "🇶", "🇷", "🇸", "🇹", "🇺", "🇻", "🇼", "🇽",
    "🇾", "🇿"
]
SINGLE_EVENT_EMOJI = "✅"
MAX_GROUP_SIZE = 8


def parse_date_time(date_str, time_str):
    try:
        d, m, y = date_str.split("/")
        h, mi = time_str.split(":")
        return datetime(int(y), int(m), int(d), int(h), int(mi))
    except:
        return None


def parse_date_time_via_regex(line):
    """
    Extrait <titre> (optionnel), date, heure et description d'une ligne
    via le pattern : <...> 15/03/2025 20:30 <description éventuelle...>
    Retourne (titre, datetime, reste)
    """
    mat = DATE_TIME_REGEX.search(line)
    if not mat:
        return None, None, None
    ds = mat.group("date").strip()
    ts = mat.group("time").strip()
    leftover = mat.group("desc").strip()
    # Tout ce qui est avant la date dans la ligne peut être considéré comme titre
    title_part = line[:mat.start()].strip()
    dt = parse_date_time(ds, ts)
    if not dt:
        return None, None, None
    if not title_part:
        title_part = "SansTitre"
    return title_part, dt, leftover


class ActiviteData:
    """
    Représente une activité planifiée.
    """
    def __init__(self, i, t, dt, desc, cid, rid=None):
        self.id = i
        self.titre = t
        self.date_obj = dt
        self.description = desc
        self.creator_id = cid
        self.role_id = rid
        self.participants = []
        self.cancelled = False

    def to_dict(self):
        return {
            "id": self.id,
            "titre": self.titre,
            "date_str": self.date_obj.strftime("%Y-%m-%d %H:%M:%S"),
            "description": self.description,
            "creator_id": self.creator_id,
            "role_id": self.role_id,
            "participants": self.participants,
            "cancelled": self.cancelled
        }

    @staticmethod
    def from_dict(d):
        dt = datetime.strptime(d["date_str"], "%Y-%m-%d %H:%M:%S")
        o = ActiviteData(
            d["id"], d["titre"], dt, d["description"],
            d["creator_id"], d["role_id"]
        )
        o.participants = d["participants"]
        o.cancelled = d["cancelled"]
        return o


class ActiviteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Structure interne : { "next_id": <int>, "events": {id_string: ActiviteData, ...} }
        self.data = {"next_id": 1, "events": {}}

        # Mappings pour gérer les réactions
        self.liste_message_map = {}
        self.single_event_msg_map = {}
        self.unsub_map = {}

        # Au lieu de charger depuis un fichier local, on va lancer
        # un chargement asynchrone depuis le salon "console".
        # On ne peut pas faire 'await' ici dans __init__, donc on programmera un "on_ready".
        self.data_is_loaded = False

        # Tâche de vérification périodique
        self.check_events_loop.start()

    # ----------------------------------------------------------
    #  Méthodes de chargement / sauvegarde sur Discord
    # ----------------------------------------------------------

    async def load_data_from_discord(self):
        """
        Récupère le message épinglé dans le salon `console` qui contient
        le fichier `activities_data.json`. Le télécharge, parse, et remplit self.data.
        S'il n'existe pas, laisse self.data avec des valeurs par défaut.
        """
        if self.data_is_loaded:  # Évite de double-charger
            return

        console_chan = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if not console_chan:
            # Pas de salon => on garde data vide
            print(f"[ActiviteCog] Impossible de trouver le salon '{CONSOLE_CHANNEL_NAME}'.")
            return

        pinned = await console_chan.pins()
        pinned_json_message = None

        # On recherche un message épinglé du bot, avec une PJ nommée "activities_data.json"
        for msg in pinned:
            if msg.author == self.bot.user and msg.attachments:
                for att in msg.attachments:
                    if att.filename == PINNED_JSON_FILENAME:
                        pinned_json_message = msg
                        break
            if pinned_json_message:
                break

        if not pinned_json_message:
            # Aucune donnée dans le channel => data vide
            print("[ActiviteCog] Aucune donnée JSON épinglée trouvée dans #console. Démarrage avec data vide.")
            self.data_is_loaded = True
            return

        # Téléchargement du fichier
        attachment = pinned_json_message.attachments[0]  # On sait que c'est 'activities_data.json'
        data_bytes = await attachment.read()
        try:
            raw_json = json.loads(data_bytes)
            self.data["next_id"] = raw_json.get("next_id", 1)
            self.data["events"] = {}
            for k, v in raw_json.get("events", {}).items():
                # On essaie de recréer ActiviteData
                e = ActiviteData.from_dict(v)
                self.data["events"][k] = e
            print("[ActiviteCog] Données d'activités restaurées depuis #console.")
        except Exception as e:
            print(f"[ActiviteCog] Erreur lors du parse du JSON : {e}")
            # On garde la data vide
        self.data_is_loaded = True

    async def save_data_to_discord(self):
        """
        Convertit self.data en JSON, puis l'envoie en PJ dans le salon `console`,
        épingle ce message, et supprime l'ancien message épinglé pour éviter les doublons.
        """
        # Au cas où on ne veut pas écraser avant d'avoir chargé
        if not self.data_is_loaded:
            return

        console_chan = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if not console_chan:
            print(f"[ActiviteCog] Impossible de trouver le salon '{CONSOLE_CHANNEL_NAME}' pour sauvegarder.")
            return

        # On convertit la structure self.data en JSON
        es = {}
        for k, v in self.data["events"].items():
            es[k] = v.to_dict()
        payload = {
            "next_id": self.data["next_id"],
            "events": es
        }
        json_str = json.dumps(payload, indent=2, ensure_ascii=False)
        data_bytes = json_str.encode("utf-8")

        # On supprime l'ancien message épinglé du bot
        pinned = await console_chan.pins()
        for msg in pinned:
            if msg.author == self.bot.user and msg.attachments:
                for att in msg.attachments:
                    if att.filename == PINNED_JSON_FILENAME:
                        # On retire l'épinglage puis on supprime le message
                        await msg.unpin(reason="Nouveau snapshot d'activités.")
                        try:
                            await msg.delete()
                        except:
                            pass
                        break

        # On envoie un nouveau message avec la PJ
        file_to_send = discord.File(io.BytesIO(data_bytes), filename=PINNED_JSON_FILENAME)
        new_msg = await console_chan.send(
            content="**Snapshot des activités** (sauvegarde automatique)",
            file=file_to_send
        )
        await new_msg.pin(reason="Sauvegarde activités")

        print("[ActiviteCog] Données d'activités sauvegardées dans #console.")

    # ----------------------------------------------------------
    #  Méthodes standard du Cog
    # ----------------------------------------------------------

    @tasks.loop(minutes=5)
    async def check_events_loop(self):
        """
        Toutes les 5 minutes, on vérifie si une activité est dépassée,
        ou si on doit envoyer des rappels.
        """
        # On attend que le bot soit prêt et qu'on ait chargé les données
        if not self.bot.is_ready():
            return
        if not self.data_is_loaded:
            return

        now = datetime.now()
        c = discord.utils.get(self.bot.get_all_channels(), name=ORGA_CHANNEL_NAME)
        if not c:
            return

        for k, e in list(self.data["events"].items()):
            if e.cancelled:
                continue
            d = (e.date_obj - now).total_seconds()

            # Si c'est dans le passé => on supprime l'activité
            if d < 0:
                # Supprimer le rôle associé si nécessaire
                if e.role_id:
                    g = c.guild
                    rr = g.get_role(e.role_id)
                    if rr:
                        try:
                            await rr.delete()
                        except:
                            pass
                self.data["events"].pop(k, None)
                await self.save_data_to_discord()
                continue

            # Système de rappels (24h & 1h avant)
            h = d / 3600.0
            if not hasattr(e, "reminder_24_sent"):
                e.reminder_24_sent = False
            if not hasattr(e, "reminder_1_sent"):
                e.reminder_1_sent = False

            if 23.9 < h < 24.1 and not e.reminder_24_sent:
                await self.envoyer_rappel(c, e, "24h")
                e.reminder_24_sent = True

            if 0.9 < h < 1.1 and not e.reminder_1_sent:
                await self.envoyer_rappel(c, e, "1h")
                e.reminder_1_sent = True

        # Après la boucle, on sauvegarde si jamais on a modifié des champs
        await self.save_data_to_discord()

    async def envoyer_rappel(self, channel, e, t):
        mention = f"<@&{e.role_id}>" if e.role_id else ""
        ds = e.date_obj.strftime("%d/%m/%Y à %H:%M")
        if t == "24h":
            message = (
                f"⏰ **Rappel 24h** : {e.titre} démarre dans 24h.\n"
                f"{mention}\nDébut le {ds}."
            )
        else:
            message = (
                f"⏰ **Rappel 1h** : {e.titre} démarre dans 1h.\n"
                f"{mention}\nDébut le {ds}."
            )
        await channel.send(message)

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Quand le bot est prêt, on charge pour la première fois les données depuis #console,
        si ce n'est pas déjà fait.
        """
        if not self.data_is_loaded:
            print("[ActiviteCog] Chargement des données depuis le salon console...")
            await self.load_data_from_discord()

    def cog_unload(self):
        self.check_events_loop.cancel()

    # ----------------------------------------------------------
    #  Commandes principales
    # ----------------------------------------------------------

    @commands.command(name="activite")
    async def activite_main(self, ctx, action=None, *, args=None):
        if not self.data_is_loaded:
            return await ctx.send("Données en cours de chargement, réessaye dans quelques instants.")
        if not action:
            await ctx.send("Actions disponibles: guide, creer, liste, info, join, leave, annuler, modifier.")
            return
        a = action.lower()

        if a == "guide":
            await self.command_guide(ctx)
        elif a == "creer":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Tu n'as pas le rôle requis pour créer une activité.")
            await self.command_creer(ctx, args)
        elif a == "liste":
            await self.command_liste(ctx)
        elif a == "info":
            await self.command_info(ctx, args)
        elif a == "join":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Tu n'as pas le rôle requis pour rejoindre une activité.")
            await self.command_join(ctx, args)
        elif a == "leave":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Tu n'as pas le rôle requis pour quitter une activité.")
            await self.command_leave(ctx, args)
        elif a == "annuler":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Tu n'as pas le rôle requis pour annuler une activité.")
            await self.command_annuler(ctx, args)
        elif a == "modifier":
            if not self.has_validated_role(ctx.author):
                return await ctx.send("Tu n'as pas le rôle requis pour modifier une activité.")
            await self.command_modifier(ctx, args)
        else:
            await ctx.send("Action inconnue. Utilise `!activite guide` pour plus d’informations.")

    async def command_guide(self, ctx):
        em = discord.Embed(
            title="Guide d’utilisation de la commande !activite",
            description=(
                "Voici un récapitulatif complet des actions possibles avec la commande "
                "`!activite` et leur syntaxe. Assure-toi de bien avoir le rôle "
                f"**{VALIDATED_ROLE_NAME}** si tu souhaites créer, rejoindre, quitter, "
                "annuler ou modifier une activité."
            ),
            color=0x00AAFF
        )
        em.add_field(
            name="1. !activite creer <titre> <JJ/MM/AAAA HH:MM> <description>",
            value=(
                "**Crée une nouvelle activité**.\n\n"
                "Exemple : `!activite creer SortieDonjon 15/03/2025 20:30 Préparez vos potions !`\n\n"
                "- `<titre>` : Le nom ou thème de l’activité.\n"
                "- `<JJ/MM/AAAA HH:MM>` : Date et heure de début.\n"
                "- `<description>` : Informations facultatives.\n\n"
                "Un nouveau rôle sera automatiquement créé, et l'auteur se verra attribuer ce rôle."
            ),
            inline=False
        )
        em.add_field(
            name="2. !activite liste",
            value=(
                "**Affiche la liste des activités à venir**.\n\n"
                "Le bot envoie un message avec un aperçu de chaque activité.\n"
                "Pour s’inscrire, il faut utiliser la commande join."
            ),
            inline=False
        )
        em.add_field(
            name="3. !activite info <id>",
            value=(
                "**Affiche les détails d’une activité**.\n\n"
                "Exemple : `!activite info 3`\n"
                "- `<id>` : L’identifiant de l’activité."
            ),
            inline=False
        )
        em.add_field(
            name="4. !activite join <id>",
            value=(
                "**Rejoint une activité**.\n\n"
                "Exemple : `!activite join 3`\n"
                "- `<id>` : L’identifiant de l’activité.\n"
                "Ajoute le joueur à la liste des participants et lui assigne le rôle correspondant."
            ),
            inline=False
        )
        em.add_field(
            name="5. !activite leave <id>",
            value=(
                "**Quitte une activité**.\n\n"
                "Exemple : `!activite leave 3`\n"
                "- `<id>` : L’identifiant de l’activité."
            ),
            inline=False
        )
        em.add_field(
            name="6. !activite annuler <id>",
            value=(
                "**Annule une activité**.\n\n"
                "Exemple : `!activite annuler 3`\n"
                "- `<id>` : L’identifiant de l’activité.\n"
                "Seul l’organisateur ou un administrateur peut annuler."
            ),
            inline=False
        )
        em.add_field(
            name="7. !activite modifier <id> <JJ/MM/AAAA HH:MM> <description>",
            value=(
                "**Modifie la date et la description d’une activité**.\n\n"
                "Exemple : `!activite modifier 3 12/05/2025 19:30 Départ devant la porte`\n"
                "- `<id>` : L’identifiant de l’activité.\n"
                "- `<JJ/MM/AAAA HH:MM>` : Nouvelle date.\n"
                "- `<description>` : Nouvelle description."
            ),
            inline=False
        )
        em.add_field(
            name="Règles et rappels",
            value=(
                f"- **Places limitées** : {MAX_GROUP_SIZE}.\n"
                f"- **Rôle requis** : {VALIDATED_ROLE_NAME}.\n"
                "- **Rappels** : 24h et 1h avant l’activité."
            ),
            inline=False
        )
        await ctx.send(embed=em)

    async def command_creer(self, ctx, line):
        if not line or line.strip() == "":
            return await ctx.send("Syntaxe: !activite creer <titre> <JJ/MM/AAAA HH:MM> <description>")

        t, dt, de = parse_date_time_via_regex(line)
        if not dt:
            return await ctx.send("La date/heure est invalide (JJ/MM/AAAA HH:MM)")

        # Vérification qu'il n'y a pas déjà une activité similaire
        for e_id, eobj in self.data["events"].items():
            if eobj.cancelled:
                continue
            if eobj.titre.lower() == t.lower():
                # Vérif si la date est "proche"
                if abs((eobj.date_obj - dt).days) < 3:
                    await ctx.send(f"Une activité similaire '{t}' existe déjà à des dates proches.")
                    break

        g = ctx.guild
        rn = f"Sortie - {t}"
        try:
            ro = await g.create_role(name=rn)
        except Exception as ex:
            return await ctx.send(f"Impossible de créer le rôle : {ex}")

        i = str(self.data["next_id"])
        self.data["next_id"] += 1

        a = ActiviteData(i, t, dt, de, ctx.author.id, ro.id)
        self.data["events"][i] = a
        await self.save_data_to_discord()

        # Assigner le rôle de l'activité au créateur
        try:
            await ctx.author.add_roles(ro)
        except:
            pass

        ds = dt.strftime("%d/%m/%Y à %H:%M")
        em = discord.Embed(
            title=f"Création de l’activité : {t}",
            description=de or "Aucune description spécifiée",
            color=0x00FF00
        )
        em.add_field(name="Date/Heure", value=ds, inline=False)
        em.add_field(name="ID de l’activité", value=i, inline=True)
        await ctx.send(embed=em)

        # Petit message dans #organisation
        c = discord.utils.get(g.text_channels, name=ORGA_CHANNEL_NAME)
        if c:
            val = discord.utils.get(g.roles, name=VALIDATED_ROLE_NAME)
            mention = f"<@&{val.id}>" if val else "@everyone"
            ev = discord.Embed(
                title=f"Nouvelle proposition : {t}",
                description=(
                    f"Date : {ds}\n"
                    f"Description : {de or '(aucune)'}\n"
                    f"Réagissez avec {SINGLE_EVENT_EMOJI}\n"
                    f"ID = {i}"
                ),
                color=0x44DD55
            )
            m = await c.send(
                content=f"{mention} : Nouvelle activité proposée par {ctx.author.mention}",
                embed=ev
            )
            await m.add_reaction(SINGLE_EVENT_EMOJI)
            self.single_event_msg_map[m.id] = i

    async def command_liste(self, ctx):
        now = datetime.now()
        up = []
        for k, e in self.data["events"].items():
            if e.cancelled:
                continue
            if e.date_obj > now:
                up.append(e)

        if not up:
            return await ctx.send("Aucune activité à venir.")

        up.sort(key=lambda x: x.date_obj)
        em = discord.Embed(title="Activités à venir", color=0x3498db)

        mp = {}
        for i, ev in enumerate(up):
            if i >= len(LETTER_EMOJIS):
                break
            emj = LETTER_EMOJIS[i]
            ds = ev.date_obj.strftime("%d/%m %H:%M")
            pc = len(ev.participants)
            org = ctx.guild.get_member(ev.creator_id)
            on = org.display_name if org else "Inconnu"
            ro = f"<@&{ev.role_id}>" if ev.role_id else "Aucun"
            par = []
            for p in ev.participants:
                mem = ctx.guild.get_member(p)
                par.append(mem.display_name if mem else f"<@{p}>")
            pstr = ", ".join(par) if par else "Aucun participant"
            txt = (
                f"**ID** : {ev.id}\n"
                f"**Date** : {ds}\n"
                f"**Organisateur** : {on}\n"
                f"**Participants** ({pc}/{MAX_GROUP_SIZE}) : {pstr}\n"
                f"**Rôle associé** : {ro}\n"
                f"---\n{ev.description or '*Aucune description*'}"
            )
            em.add_field(name=f"{emj} : {ev.titre}", value=txt, inline=False)
            mp[emj] = ev.id

        ms = await ctx.send(embed=em)
        for i in range(len(up)):
            if i >= len(LETTER_EMOJIS):
                break
            await ms.add_reaction(LETTER_EMOJIS[i])

        self.liste_message_map[ms.id] = mp

    async def command_info(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe : !activite info <id>")
        if args not in self.data["events"]:
            return await ctx.send("Activité introuvable.")

        e = self.data["events"][args]
        em = discord.Embed(
            title=f"Informations : {e.titre} (ID={e.id})",
            color=0xFFC107
        )
        em.add_field(name="Date/Heure", value=e.date_obj.strftime("%d/%m/%Y %H:%M"), inline=False)
        em.add_field(name="Annulée ?", value="Oui" if e.cancelled else "Non", inline=True)
        em.add_field(name="Description", value=e.description or "Aucune", inline=False)
        o = ctx.guild.get_member(e.creator_id)
        on = o.display_name if o else "Inconnu"
        em.add_field(name="Organisateur", value=on, inline=False)
        par = []
        for p in e.participants:
            mem = ctx.guild.get_member(p)
            par.append(mem.display_name if mem else f"<@{p}>")
        pc = len(par)
        pstr = ", ".join(par) if par else "Aucun"
        em.add_field(name=f"Participants ({pc}/{MAX_GROUP_SIZE})", value=pstr, inline=False)
        if e.role_id:
            em.add_field(name="Rôle associé", value=f"<@&{e.role_id}>", inline=True)
        await ctx.send(embed=em)

    async def command_join(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite join <id>")
        if args not in self.data["events"]:
            return await ctx.send("Activité introuvable.")
        e = self.data["events"][args]
        if e.cancelled:
            return await ctx.send("Cette activité est annulée.")
        if len(e.participants) >= MAX_GROUP_SIZE:
            return await ctx.send("Le groupe est complet.")
        if ctx.author.id in e.participants:
            return await ctx.send("Tu es déjà inscrit(e) à cette activité.")
        e.participants.append(ctx.author.id)
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.add_roles(r)
                except:
                    pass
        await ctx.send(f"{ctx.author.mention} a rejoint l’activité « {e.titre} » (ID={args}).")

    async def command_leave(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite leave <id>")
        if args not in self.data["events"]:
            return await ctx.send("Activité introuvable.")
        e = self.data["events"][args]
        if ctx.author.id not in e.participants:
            return await ctx.send("Tu n’étais pas inscrit(e) à cette activité.")
        e.participants.remove(ctx.author.id)
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.remove_roles(r)
                except:
                    pass
        await ctx.send(f"{ctx.author.mention} s’est désinscrit(e) de l’activité « {e.titre} » (ID={args}).")

    async def command_annuler(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite annuler <id>")
        if args not in self.data["events"]:
            return await ctx.send("Activité introuvable.")
        e = self.data["events"][args]
        if not self.can_modify(ctx, e):
            return await ctx.send("Tu n’as pas l’autorisation d’annuler cette activité.")
        e.cancelled = True
        await self.save_data_to_discord()
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await r.delete(reason="Annulation de l’activité.")
                except:
                    pass
        await ctx.send(f"Activité « {e.titre} » annulée avec succès.")

    async def command_modifier(self, ctx, args):
        if not args:
            return await ctx.send("Syntaxe: !activite modifier <id> <JJ/MM/AAAA HH:MM> <description>")
        p = args.split(" ", 1)
        if len(p) < 2:
            return await ctx.send("Exemple : !activite modifier 3 12/05/2025 19:30 Desc")
        i = p[0]
        rest = p[1]
        if i not in self.data["events"]:
            return await ctx.send("Activité introuvable.")
        e = self.data["events"][i]
        if not self.can_modify(ctx, e):
            return await ctx.send("Tu n’as pas l’autorisation de modifier cette activité.")
        if e.cancelled:
            return await ctx.send("Cette activité est déjà annulée.")
        m = DATE_TIME_REGEX.search(rest)
        if not m:
            return await ctx.send("Impossible de trouver la date/heure dans ta requête.")
        ds = m.group("date").strip()
        ts = m.group("time").strip()
        nd = m.group("desc").strip()
        dt = parse_date_time(ds, ts)
        if not dt:
            return await ctx.send("Date invalide. Format JJ/MM/AAAA HH:MM")
        e.date_obj = dt
        e.description = nd
        await self.save_data_to_discord()
        await ctx.send(
            f"Activité « {e.titre} » (ID={i}) mise à jour.\n"
            f"Nouvelle date/heure : {dt.strftime('%d/%m/%Y %H:%M')}\n"
            f"Nouvelle description : {nd or '(aucune)'}"
        )

    @commands.command(name="calendrier")
    async def afficher_calendrier(self, ctx):
        # Simple affichage d’un calendrier généré (déjà présent dans votre code initial)
        now = datetime.now()
        an = now.year
        mo = now.month
        try:
            bg = mpimg.imread("calendrier1.png")
        except:
            bg = None

        buf = gen_cal(self.data["events"], bg, an, mo)
        fil = discord.File(fp=buf, filename="calendrier.png")
        msg = await ctx.send(file=fil)
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
                except:
                    pass
                break
            else:
                try:
                    await msg.remove_reaction(reac.emoji, usr)
                except:
                    pass
                if str(reac.emoji) == "➡️":
                    mo += 1
                    if mo > 12:
                        mo = 1
                        an += 1
                else:
                    mo -= 1
                    if mo < 1:
                        mo = 12
                        an -= 1
                try:
                    await msg.delete()
                except:
                    pass
                newb = gen_cal(self.data["events"], bg, an, mo)
                newf = discord.File(fp=newb, filename="calendrier.png")
                msg = await ctx.send(file=newf)
                await msg.add_reaction("⬅️")
                await msg.add_reaction("➡️")

    # ----------------------------------------------------------
    #  Gestion des réactions
    # ----------------------------------------------------------

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
        # À implémenter si besoin : logique de "join" via réactions
        pass

    async def handle_reaction_single_event(self, reaction, user):
        # À implémenter si besoin : logique de "join" via ✅
        pass

    async def handle_unsubscribe_dm(self, reaction, user):
        pass

    # ----------------------------------------------------------
    #  Utilitaires
    # ----------------------------------------------------------

    def can_modify(self, ctx, e):
        if ctx.author.id == e.creator_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        return False

    def has_validated_role(self, member):
        return any(r.name == VALIDATED_ROLE_NAME for r in member.roles)


async def setup(bot: commands.Bot):
    """
    Méthode d'initialisation asynchrone du Cog.
    Discord.py appelle cette fonction lors d'un load_extension.
    """
    cog = ActiviteCog(bot)
    bot.add_cog(cog)
    # Optionnel : on peut forcer un chargement tout de suite,
    # mais on_ready le fera déjà.
    # await cog.load_data_from_discord()
