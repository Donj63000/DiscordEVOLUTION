#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import tempfile
import re
import asyncio
import io
import logging
import unicodedata
from typing import Dict, Optional

import discord

from discord.ext import commands, tasks
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from datetime import datetime, timedelta, date
from utils.channel_resolver import resolve_text_channel
from utils.discord_history import fetch_channel_history

# Import d'un module externe "calendrier" contenant la fonction gen_cal
from calendrier import gen_cal, MONTH_NAMES_FR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Noms de canaux / rôles
ORGANISATION_CHANNEL_FALLBACK = os.getenv("ORGANISATION_CHANNEL_NAME", "organisation")
CONSOLE_CHANNEL_FALLBACK = os.getenv("CHANNEL_CONSOLE", "console")
VALIDATED_ROLE_NAME = "Membre validé d'Evolution"

# Fichier de persistance
DATA_FILE = "activities_data.json"
MARKER_TEXT = "===BOTACTIVITES==="

# Expression régulière pour parse la date/heure
DATE_TIME_REGEX = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})\s*(?:;|\s+)\s*(?P<time>\d{2}:\d{2})(?P<desc>.*)$")

# Emojis pour la pagination et l'inscription
LETTER_EMOJIS = [
    "🇦","🇧","🇨","🇩","🇪","🇫","🇬","🇭","🇮","🇯","🇰","🇱","🇲","🇳","🇴","🇵",
    "🇶","🇷","🇸","🇹","🇺","🇻","🇼","🇽","🇾","🇿"
]

# Emojis d'inscription / désinscription
SINGLE_EVENT_EMOJI = "✅"
UNSUB_EMOJI = "❌"

# Taille maximum d'un groupe
MAX_GROUP_SIZE = 8

# Verrou asynchrone pour sécuriser la sauvegarde (écritures concurrentes)
save_lock = asyncio.Lock()

def normalize_string(s: str):
    """Normalise une chaîne en supprimant les accents et en mettant en minuscule."""
    nf = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')

def parse_date_time(date_str, time_str):
    """Convertit une date et une heure (JJ/MM/AAAA, HH:MM) en objet datetime. Retourne None si échec."""
    try:
        d, m, y = date_str.split("/")
        h, mi = time_str.split(":")
        return datetime(int(y), int(m), int(d), int(h), int(mi))
    except ValueError:
        return None

def parse_date_time_via_regex(line):
    """
    Cherche dans une ligne un motif <titre> ... JJ/MM/AAAA HH:MM ... <desc>.
    Retourne (titre, datetime, description) ou (None, None, None) si échec.
    """
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
    """
    Représente une activité (ID, titre, date, description, etc.).
    Gère la sérialisation/désérialisation en dictionnaire JSON.
    """
    def __init__(self, i, t, dt, desc, cid, rid=None,
                 reminder_24_sent=False, reminder_1_sent=False):
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
        """Transforme l'objet en dict JSON."""
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
        """Recrée ActiviteData depuis un dict JSON."""
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


class CalendrierView(discord.ui.View):
    """Vue interactive pour naviguer dans le calendrier des activités."""

    def __init__(
        self,
        author: discord.abc.User,
        events: Dict[str, ActiviteData],
        bg_image,
        highlight: Optional[date] = None,
    ) -> None:
        super().__init__(timeout=180)
        self.author_id = author.id
        self.events = events
        self.bg_image = bg_image
        self.highlight_date = highlight or datetime.now().date()
        self.year = self.highlight_date.year
        self.month = self.highlight_date.month
        self.message: Optional[discord.Message] = None
        self._sync_button_states()

    def _sync_button_states(self) -> None:
        same_month = (
            self.highlight_date is not None
            and self.year == self.highlight_date.year
            and self.month == self.highlight_date.month
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "Aujourd'hui":
                child.disabled = same_month

    def _events_count_current_month(self) -> int:
        return sum(
            1
            for evt in self.events.values()
            if not evt.cancelled
            and evt.date_obj.year == self.year
            and evt.date_obj.month == self.month
        )

    def build_content(self) -> str:
        count = self._events_count_current_month()
        if count == 0:
            info = "Aucun événement prévu pour ce mois."
        elif count == 1:
            info = "1 événement prévu pour ce mois."
        else:
            info = f"{count} événements prévus pour ce mois."
        return (
            f"Calendrier des activités – {MONTH_NAMES_FR[self.month]} {self.year}\n"
            f"{info}\n"
            "Utilise les boutons ci-dessous pour naviguer."
        )

    def build_file(self) -> discord.File:
        buffer = gen_cal(
            self.events,
            self.bg_image,
            self.year,
            self.month,
            highlight_date=self.highlight_date,
        )
        return discord.File(fp=buffer, filename="calendrier.png")

    def _step_month(self, delta: int) -> None:
        self.month += delta
        while self.month < 1:
            self.month += 12
            self.year -= 1
        while self.month > 12:
            self.month -= 12
            self.year += 1
        self._sync_button_states()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seule la personne qui a demandé le calendrier peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        if self.message is None:
            self.message = interaction.message
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._sync_button_states()
        file = self.build_file()
        content = self.build_content()
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    content=content,
                    attachments=[file],
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=content,
                    attachments=[file],
                    view=self,
                )
        except discord.HTTPException:
            pass

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_month(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self._step_month(-1)
        await self._refresh(interaction)

    @discord.ui.button(label="Aujourd'hui", style=discord.ButtonStyle.primary)
    async def go_today(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if (
            self.highlight_date
            and self.year == self.highlight_date.year
            and self.month == self.highlight_date.month
        ):
            await interaction.response.send_message(
                "Nous sommes déjà sur le mois en cours.",
                ephemeral=True,
            )
            return
        self.year = self.highlight_date.year
        self.month = self.highlight_date.month
        self._sync_button_states()
        await self._refresh(interaction)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_month(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self._step_month(1)
        await self._refresh(interaction)

    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.danger)
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass
        self.stop()

class ActiviteCog(commands.Cog):
    """
    Cog principal pour la gestion des activités (création, inscriptions, rappels, etc.).
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.activities_data = {
            "next_id": 1,
            "events": {}
        }
        self.initialized = False

        # Suivi des messages (IDs) pour la liste paginée et les events uniques
        self.liste_message_map = {}
        self.single_event_msg_map = {}

    def _resolve_console_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return resolve_text_channel(
            guild,
            id_env="CHANNEL_CONSOLE_ID",
            name_env="CHANNEL_CONSOLE",
            default_name=CONSOLE_CHANNEL_FALLBACK,
        )

    def _resolve_organisation_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return resolve_text_channel(
            guild,
            id_env="ORGANISATION_CHANNEL_ID",
            name_env="ORGANISATION_CHANNEL_NAME",
            default_name=ORGANISATION_CHANNEL_FALLBACK,
        )

    async def cog_load(self):
        """Chargement asynchrone du Cog."""
        if not self.check_events_loop.is_running():
            self.check_events_loop.start()

    def _mark_initialized(self) -> None:
        self.initialized = True
        logger.info("ActiviteCog: données initialisées.")

    async def initialize_data(self):
        """
        1) On tente de charger le fichier local d’abord (source de vérité).
        2) Puis, si on trouve un bloc JSON plus récent dans le channel console, on peut surdéfinir.
        3) On gère aussi la possibilité d’un fichier joint .json dans le channel console.
        """
        if self.initialized:
            return
        # 1) Charger d'abord depuis le fichier local, s'il existe
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.activities_data = json.load(f)
                logger.info("Données chargées depuis le fichier local.")
            except Exception as e:
                logger.warning(f"Impossible de charger {DATA_FILE} : {e}")
        else:
            logger.info("Pas de fichier local trouvé, on part sur des données vierges.")

        # 2) Chercher éventuellement dans le channel console pour un message plus récent
        console_channel = None
        for guild in self.bot.guilds:
            candidate = self._resolve_console_channel(guild)
            if candidate:
                console_channel = candidate
                break
        if console_channel:
            # On va scroller l'historique en commençant par le plus récent
            limit = max(
                int(os.getenv("ACTIVITE_HISTORY_LIMIT", os.getenv("CONSOLE_HISTORY_LIMIT", "200"))),
                0,
            )
            messages = await fetch_channel_history(
                console_channel,
                limit=limit,
                reason="activite.console",
            )
            for msg in messages:
                if msg.author == self.bot.user and MARKER_TEXT in msg.content:
                    # Priorité 1 : s'il y a un attachement .json
                    if msg.attachments:
                        for att in msg.attachments:
                            if att.filename.endswith(".json"):
                                try:
                                    file_bytes = await att.read()
                                    data_loaded = json.loads(file_bytes.decode("utf-8"))
                                    self.activities_data = data_loaded
                                    logger.info(
                                        "Données surchargées depuis un fichier joint JSON dans %s.",
                                        console_channel.name,
                                    )
                                    logger.debug("ActiviteCog: snapshot console charge via piece jointe.")
                                    self._mark_initialized()
                                    return
                                except Exception as ex:
                                    logger.warning(f"Impossible de parser le fichier JSON joint : {ex}")
                        # Si on n’a pas pu lire d’attachement JSON valide, on check le bloc inline
                    # Priorité 2 : bloc ```json ... ```
                    if "```json\n" in msg.content:
                        try:
                            start_idx = msg.content.index("```json\n") + len("```json\n")
                            end_idx = msg.content.rindex("\n```")
                            raw_json = msg.content[start_idx:end_idx]
                            data_loaded = json.loads(raw_json)
                            self.activities_data = data_loaded
                            logger.info(
                                "Données surchargées depuis %s (bloc texte JSON).",
                                console_channel.name,
                            )
                            logger.debug("ActiviteCog: snapshot console charge via bloc json.")
                            self._mark_initialized()
                            return
                        except Exception as e:
                            logger.warning(
                                "Impossible de parser le JSON %s inline: %s",
                                console_channel.name,
                                e,
                            )
        else:
            logger.info(
                "Channel %s introuvable, on reste sur le fichier local.",
                CONSOLE_CHANNEL_FALLBACK,
            )

        self._mark_initialized()

    async def save_data_local(self):
        """
        Sauvegarde des données dans le fichier JSON.
        On utilise un verrou asynchrone + un fichier temporaire pour éviter la corruption.
        """
        async with save_lock:
            temp_file = DATA_FILE + ".temp"
            try:
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.activities_data, f, indent=4, ensure_ascii=False)
                # Rename atomique
                os.replace(temp_file, DATA_FILE)
                logger.info("Sauvegarde OK (fichier local).")
            except Exception as e:
                logger.warning(f"Erreur lors de la sauvegarde : {e}")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass

    async def dump_data_to_console(self, ctx):
        """
        Envoie les données dans le channel console, après une opération critique.
        Les données peuvent être trop volumineuses => on envoie un fichier joint.
        """
        console_channel = self._resolve_console_channel(ctx.guild)
        if not console_channel:
            return
        await self._dump_data(console_channel)

    async def dump_data_to_console_no_ctx(self, guild: discord.Guild):
        """Variante sans ctx, ex. depuis la boucle asynchrone."""
        console_channel = self._resolve_console_channel(guild)
        if not console_channel:
            return
        await self._dump_data(console_channel)

    async def _dump_data(self, console_channel: discord.TextChannel):
        data_str = json.dumps(self.activities_data, indent=4, ensure_ascii=False)
        content_prefix = f"{MARKER_TEXT}"
        if len(data_str) < 1900:
            # On peut poster directement en code-block
            await console_channel.send(f"{content_prefix}\n```json\n{data_str}\n```")
        else:
            # Fichier trop gros, on envoie en pièce jointe
            tmp = tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8", suffix=".json")
            temp_file_path = tmp.name
            try:
                tmp.write(data_str)
                tmp.flush()
                tmp.close()
            except Exception as ex:
                logger.warning(f"Erreur création du fichier temp {CONSOLE_CHANNEL_FALLBACK}: {ex}")
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass
                return

            try:
                await console_channel.send(
                    f"{content_prefix} (fichier)",
                    file=discord.File(fp=temp_file_path, filename="activities_data.json")
                )
            finally:
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass

    @tasks.loop(minutes=5)
    async def check_events_loop(self):
        """Boucle d'auto-nettoyage (passe toutes les 5 minutes)."""
        if not self.bot.is_ready():
            return
        if not self.initialized:
            return

        now = datetime.now()
        org_channel = None
        for guild in self.bot.guilds:
            candidate = self._resolve_organisation_channel(guild)
            if candidate:
                org_channel = candidate
                break
        if not org_channel:
            return

        if "events" not in self.activities_data:
            return

        to_delete = []
        modified = False

        for k, e_data in list(self.activities_data["events"].items()):
            if e_data["cancelled"]:
                continue
            evt = ActiviteData.from_dict(e_data)
            time_left = (evt.date_obj - now).total_seconds()

            # Si l'activité est passée
            if time_left < 0:
                if evt.role_id:
                    rr = org_channel.guild.get_role(evt.role_id)
                    if rr:
                        try:
                            await rr.delete(reason="Activité terminée")
                        except Exception as ex:
                            logger.warning(f"Erreur suppression rôle {rr}: {ex}")
                to_delete.append(k)
                continue

            # Rappel 24h
            if not evt.reminder_24_sent and 0 < time_left <= 24*3600:
                await self.envoyer_rappel(org_channel, evt, "24h")
                e_data["reminder_24_sent"] = True
                modified = True

            # Rappel 1h
            if not evt.reminder_1_sent and 0 < time_left <= 3600:
                await self.envoyer_rappel(org_channel, evt, "1h")
                e_data["reminder_1_sent"] = True
                modified = True

        # Suppression des events passés
        for kdel in to_delete:
            del self.activities_data["events"][kdel]
            modified = True

        if modified:
            await self.save_data_local()
            if org_channel and org_channel.guild:
                await self.dump_data_to_console_no_ctx(org_channel.guild)

    async def envoyer_rappel(self, channel, e: ActiviteData, t: str):
        mention = f"<@&{e.role_id}>" if e.role_id else ""
        ds = e.date_obj.strftime("%d/%m/%Y à %H:%M")
        if t == "24h":
            msg = f"⏰ **Rappel 24h** : {e.titre} démarre dans 24h.\n{mention}\nDébut le {ds}."
        else:
            msg = f"⏰ **Rappel 1h** : {e.titre} démarre dans 1h.\n{mention}\nDébut le {ds}."
        try:
            await channel.send(msg)
        except Exception as ex:
            logger.warning(f"Impossible d'envoyer rappel : {ex}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Quand le bot est prêt."""
        if not self.initialized:
            await self.initialize_data()

    def cog_unload(self):
        """À la désinstallation du Cog, on arrête la loop."""
        self.check_events_loop.cancel()

    @commands.command(name="activite")
    async def activite_main(self, ctx, action=None, *, args=None):
        """
        Commande principale : !activite <action> <arguments...>
        - creer / liste / info / join / leave / annuler / modifier / guide
        """
        if not self.initialized:
            return await ctx.send("Données en cours de chargement.")
        if not action:
            return await ctx.send("Actions: guide, creer, liste, info, join, leave, annuler, modifier.")

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
            await ctx.send("Action inconnue. Tapez !activite guide pour l'aide.")

    async def command_guide(self, ctx):
        """Affiche le guide rapide pour la commande !activite."""
        txt = (
            "**Guide !activite**\n\n"
            "`!activite creer <titre> <JJ/MM/AAAA HH:MM> <desc>`\n"
            "`!activite liste`\n"
            "`!activite info <id>`\n"
            "`!activite join <id>` / `!activite leave <id>`\n"
            "`!activite annuler <id>`\n"
            "`!activite modifier <id> <JJ/MM/AAAA HH:MM> <desc>`\n"
        )
        em = discord.Embed(title="Guide Complet : !activite", description=txt, color=0x00AAFF)
        await ctx.send(embed=em)

    async def command_creer(self, ctx, line):
        """Crée une nouvelle activité."""
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

        # Création de l'activité
        a = ActiviteData(event_id, titre, dt, description, ctx.author.id, new_role.id)
        # On inscrit directement le créateur
        a.participants.append(ctx.author.id)

        if "events" not in self.activities_data:
            self.activities_data["events"] = {}
        self.activities_data["events"][event_id] = a.to_dict()

        # Sauvegarde + dump console
        await self.save_data_local()
        await self.dump_data_to_console(ctx)

        # Ajout du rôle au créateur (logique redondante,
        # mais permet de donner les perms ou l'identifiant visuel)
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

        org_chan = self._resolve_organisation_channel(guild)
        if org_chan:
            val_role = discord.utils.get(guild.roles, name=VALIDATED_ROLE_NAME)
            mention = f"<@&{val_role.id}>" if val_role else "@everyone"
            ev_embed = discord.Embed(
                title=f"Nouvelle proposition : {titre}",
                description=(
                    f"Date : {ds}\n"
                    f"Desc : {description or '(aucune)'}\n"
                    f"Réagissez avec {SINGLE_EVENT_EMOJI} pour participer, "
                    f"{UNSUB_EMOJI} pour vous retirer.\n"
                    f"ID = {event_id}"
                ),
                color=0x44DD55
            )
            msg = await org_chan.send(
                content=f"{mention} Activité proposée par {ctx.author.mention}",
                embed=ev_embed
            )
            await msg.add_reaction(SINGLE_EVENT_EMOJI)
            await msg.add_reaction(UNSUB_EMOJI)
            self.single_event_msg_map[msg.id] = event_id

    async def command_liste(self, ctx):
        """Affiche la liste paginée des activités à venir."""
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

        # Tri chronologique
        upcoming.sort(key=lambda x: x.date_obj)

        events_per_page = 10
        pages = []
        for i in range(0, len(upcoming), events_per_page):
            chunk = upcoming[i:i+events_per_page]
            pages.append(chunk)

        total_pages = len(pages)
        current_page = 0

        def make_embed(page_idx):
            page_events = pages[page_idx]
            em = discord.Embed(
                title=f"Activités à venir (page {page_idx+1}/{total_pages})",
                color=0x3498db
            )
            for ev in page_events:
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
                em.add_field(name=f"• {ev.titre}", value=txt, inline=False)

            return em

        embed_page = make_embed(current_page)
        msg_sent = await ctx.send(embed=embed_page)

        if total_pages == 1:
            return

        await msg_sent.add_reaction("⬅️")
        await msg_sent.add_reaction("➡️")

        self.liste_message_map[msg_sent.id] = {
            "pages": pages,
            "current_page": current_page,
            "total_pages": total_pages
        }

    async def command_info(self, ctx, args):
        """Affiche les détails d'une activité (ID)."""
        if not args:
            return await ctx.send("Syntaxe : !activite info <id>")

        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Activité introuvable.")

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
        """Permet de rejoindre un événement (ID)."""
        if not args:
            return await ctx.send("Syntaxe: !activite join <id>")

        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Activité introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        if e.cancelled:
            return await ctx.send("Activité annulée.")
        if len(e.participants) >= MAX_GROUP_SIZE:
            return await ctx.send("Groupe complet.")
        if ctx.author.id in e.participants:
            return await ctx.send("Déjà inscrit.")

        e.participants.append(ctx.author.id)
        self.activities_data["events"][args] = e.to_dict()

        await self.save_data_local()
        await self.dump_data_to_console(ctx)

        # Donne le rôle
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.add_roles(r)
                except Exception as ex:
                    logger.warning(f"Impossible d'ajouter le rôle: {ex}")

        await ctx.send(f"{ctx.author.mention} rejoint {e.titre} (ID={args}).")

    async def command_leave(self, ctx, args):
        """Permet de quitter un événement (ID)."""
        if not args:
            return await ctx.send("Syntaxe: !activite leave <id>")

        if "events" not in self.activities_data or args not in self.activities_data["events"]:
            return await ctx.send("Introuvable.")

        e_dict = self.activities_data["events"][args]
        e = ActiviteData.from_dict(e_dict)
        if ctx.author.id not in e.participants:
            return await ctx.send("Pas inscrit sur cet événement.")

        e.participants.remove(ctx.author.id)
        self.activities_data["events"][args] = e.to_dict()

        await self.save_data_local()
        await self.dump_data_to_console(ctx)

        # Retire le rôle
        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await ctx.author.remove_roles(r)
                except Exception as ex:
                    logger.warning(f"Impossible de retirer le rôle: {ex}")

        await ctx.send(f"{ctx.author.mention} se retire de {e.titre} (ID={args}).")

    async def command_annuler(self, ctx, args):
        """Annule un événement si on est créateur ou admin."""
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

        await self.save_data_local()
        await self.dump_data_to_console(ctx)

        if e.role_id:
            r = ctx.guild.get_role(e.role_id)
            if r:
                try:
                    await r.delete(reason="Annulation.")
                except Exception as ex:
                    logger.warning(f"Impossible de supprimer le rôle: {ex}")

        await ctx.send(f"{e.titre} (ID={args}) annulée.")

    async def command_modifier(self, ctx, args):
        """Modifie la date/heure + description d'un événement (ID)."""
        if not args:
            return await ctx.send("Syntaxe: !activite modifier <id> <JJ/MM/AAAA HH:MM> <desc>")
        parts = args.split(" ", 1)
        if len(parts) < 2:
            return await ctx.send("Exemple: !activite modifier 3 12/05/2025 19:30 Nouvelle desc")

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
        # On réinitialise éventuellement les rappels (si on veut)
        e.reminder_24_sent = False
        e.reminder_1_sent = False

        self.activities_data["events"][event_id] = e.to_dict()

        await self.save_data_local()
        await self.dump_data_to_console(ctx)

        await ctx.send(
            f"{e.titre} (ID={event_id}) modifiée.\n"
            f"Nouvelle date: {dt.strftime('%d/%m/%Y %H:%M')}\n"
            f"Description: {nd}"
        )

    @commands.command(name="calendrier")
    async def afficher_calendrier(self, ctx):
        """Affiche le calendrier mensuel via une vue interactive (boutons)."""
        if not self.initialized:
            return await ctx.send("Données en cours de chargement.")

        try:
            bg = mpimg.imread("calendrier1.png")
        except Exception as e:
            logger.info(f"Impossible de charger 'calendrier1.png': {e}")
            bg = None

        events: Dict[str, ActiviteData] = {}
        if "events" in self.activities_data:
            events = {
                key: ActiviteData.from_dict(value)
                for key, value in self.activities_data["events"].items()
            }

        highlight = datetime.now().date()
        view = CalendrierView(ctx.author, events, bg, highlight)
        file_cal = view.build_file()
        message = await ctx.send(
            content=view.build_content(),
            file=file_cal,
            view=view,
        )
        view.message = message

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """
        Gère les réactions sur :
        - la liste paginée (pour naviguer / s'inscrire / se désinscrire),
        - l'événement unique (✅ / ❌).
        """
        if user.bot:
            return

        # 1) Si c'est un message de liste paginée
        if reaction.message.id in self.liste_message_map:
            await self.handle_reaction_list_pagination(reaction, user)
            return

        # 2) Si c'est un message unique (créé par !activite creer)
        if reaction.message.id in self.single_event_msg_map:
            await self.handle_reaction_single_event(reaction, user)
            return

    async def handle_reaction_list_pagination(self, reaction, user):
        """
        Possibilité de réagir avec ✅ / ❌ pour s'inscrire ou se désinscrire
        depuis la liste paginée, puis re-dump dans la console.
        Mais seulement si la page contient un seul événement.
        """
        data = self.liste_message_map[reaction.message.id]
        pages = data["pages"]
        current_page = data["current_page"]
        total_pages = data["total_pages"]

        emj = str(reaction.emoji)

        # Retirer la réaction tout de suite
        try:
            await reaction.message.remove_reaction(emj, user)
        except Exception as ex:
            logger.warning(f"Impossible de retirer la réaction pagination: {ex}")

        # Navigation pages
        if emj in ["⬅️", "➡️"]:
            if emj == "➡️":
                current_page += 1
                if current_page >= total_pages:
                    current_page = 0
            else:  # "⬅️"
                current_page -= 1
                if current_page < 0:
                    current_page = total_pages - 1

            data["current_page"] = current_page
            self.liste_message_map[reaction.message.id] = data

            # Reconstruit l'embed
            page_events = pages[current_page]
            embed = discord.Embed(
                title=f"Activités à venir (page {current_page+1}/{total_pages})",
                color=0x3498db
            )
            for ev in page_events:
                ds = ev.date_obj.strftime("%d/%m %H:%M")
                pc = len(ev.participants)
                org = reaction.message.guild.get_member(ev.creator_id)
                on = org.display_name if org else "Inconnu"
                ro = f"<@&{ev.role_id}>" if ev.role_id else "Aucun"

                plist = []
                for pid in ev.participants:
                    mem = reaction.message.guild.get_member(pid)
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
                embed.add_field(name=f"• {ev.titre}", value=txt, inline=False)

            await reaction.message.edit(embed=embed)
            return

        # Inscription/désinscription sur la page ?
        if emj not in [SINGLE_EVENT_EMOJI, UNSUB_EMOJI]:
            return

        page_events = pages[current_page]
        if len(page_events) != 1:
            # On ne peut pas savoir quel event viser si plusieurs
            await reaction.message.channel.send(
                f"{user.mention} : Cette page contient plusieurs événements. "
                f"Utilise plutôt `!activite join <id>` ou `!activite leave <id>`."
            )
            return

        ev = page_events[0]
        if not self.has_validated_role(user):
            await reaction.message.channel.send(f"{user.mention} : rôle invalide.")
            return
        if ev.cancelled:
            await reaction.message.channel.send("Activité annulée.")
            return

        # On récupère l'ActiviteData à jour
        e_dict = self.activities_data["events"].get(ev.id)
        if not e_dict:
            return
        e_data = ActiviteData.from_dict(e_dict)

        if emj == SINGLE_EVENT_EMOJI:
            # S'inscrire
            if len(e_data.participants) >= MAX_GROUP_SIZE:
                await reaction.message.channel.send("Groupe complet.")
                return
            if user.id in e_data.participants:
                await reaction.message.channel.send("Déjà inscrit.")
                return

            e_data.participants.append(user.id)
            self.activities_data["events"][e_data.id] = e_data.to_dict()
            await self.save_data_local()
            await self.dump_data_to_console_no_ctx(reaction.message.guild)

            # Ajout du rôle
            if e_data.role_id:
                role = reaction.message.guild.get_role(e_data.role_id)
                if role:
                    try:
                        await user.add_roles(role)
                    except Exception as ex:
                        logger.warning(f"Impossible d'ajouter le rôle à {user}: {ex}")

            await reaction.message.channel.send(f"{user.mention} rejoint {e_data.titre} (ID={e_data.id}).")

        else:
            # emj == UNSUB_EMOJI => se désinscrire
            if user.id not in e_data.participants:
                await reaction.message.channel.send("Pas inscrit.")
                return

            e_data.participants.remove(user.id)
            self.activities_data["events"][e_data.id] = e_data.to_dict()
            await self.save_data_local()
            await self.dump_data_to_console_no_ctx(reaction.message.guild)

            # Retirer le rôle
            if e_data.role_id:
                role = reaction.message.guild.get_role(e_data.role_id)
                if role:
                    try:
                        await user.remove_roles(role)
                    except Exception as ex:
                        logger.warning(f"Impossible de retirer le rôle: {ex}")

            await reaction.message.channel.send(f"{user.mention} se retire de {e_data.titre} (ID={e_data.id}).")

    async def handle_reaction_single_event(self, reaction, user):
        """Inscription / désinscription sur un seul event (message unique créé par !activite creer)."""
        emj = str(reaction.emoji)
        event_id = self.single_event_msg_map[reaction.message.id]
        guild = reaction.message.guild

        if "events" not in self.activities_data or event_id not in self.activities_data["events"]:
            await reaction.message.channel.send("Événement introuvable ou annulé.")
            return

        e_dict = self.activities_data["events"][event_id]
        e = ActiviteData.from_dict(e_dict)
        if e.cancelled:
            await reaction.message.channel.send("Activité annulée.")
            return

        if not self.has_validated_role(user):
            await reaction.message.channel.send(f"{user.mention} rôle invalide.")
            return

        # On retire la réaction pour éviter qu'elle reste
        try:
            await reaction.message.remove_reaction(emj, user)
        except Exception as ex:
            logger.warning(f"Impossible de retirer la réaction {emj}: {ex}")

        if emj == SINGLE_EVENT_EMOJI:
            # Join
            if len(e.participants) >= MAX_GROUP_SIZE:
                await reaction.message.channel.send("Groupe complet.")
                return
            if user.id in e.participants:
                await reaction.message.channel.send("Déjà inscrit.")
                return

            e.participants.append(user.id)
            self.activities_data["events"][event_id] = e.to_dict()
            await self.save_data_local()
            await self.dump_data_to_console_no_ctx(guild)

            if e.role_id:
                role = guild.get_role(e.role_id)
                if role:
                    try:
                        await user.add_roles(role)
                    except Exception as ex:
                        logger.warning(f"Impossible d'ajouter le rôle: {ex}")

            await reaction.message.channel.send(f"{user.mention} rejoint {e.titre} (ID={e.id}).")

        elif emj == UNSUB_EMOJI:
            # Leave
            if user.id not in e.participants:
                await reaction.message.channel.send("Vous n'êtes pas inscrit.")
                return

            e.participants.remove(user.id)
            self.activities_data["events"][event_id] = e.to_dict()
            await self.save_data_local()
            await self.dump_data_to_console_no_ctx(guild)

            if e.role_id:
                role = guild.get_role(e.role_id)
                if role:
                    try:
                        await user.remove_roles(role)
                    except Exception as ex:
                        logger.warning(f"Impossible de retirer le rôle: {ex}")

            await reaction.message.channel.send(f"{user.mention} se retire de {e.titre} (ID={e.id}).")

    def can_modify(self, ctx, e: ActiviteData):
        """Autorise l'annulation / modification si créateur ou admin."""
        if ctx.author.id == e.creator_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        return False

    def has_validated_role(self, member: discord.Member):
        """Vérifie la présence du rôle validé."""
        return any(r.name == VALIDATED_ROLE_NAME for r in member.roles)


async def setup(bot: commands.Bot):
    """Routine d'installation du Cog."""
    await bot.add_cog(ActiviteCog(bot))
