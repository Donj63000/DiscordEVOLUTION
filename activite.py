"""Guild activities: one durable roster shared by commands, cards and the calendar."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from calendrier import MonthlyRenderer
from utils.activity_data import (
    ActivityError, ActiviteData, DEFAULT_CAPACITY, activity_end, apply_draft, change_roster,
    migrate_snapshot, utc, validate_draft,
)
from utils.activity_store import ActivitySnapshotStore
from utils.activity_roles import ActivityRoleManager
from utils.activity_media import announcement_image, close_artwork, IMAGE_FILENAME
from utils.activity_views import (
    ActivityCardView, ActivityListView, ActivityManageView, ActivityModal,
    activity_embed, roster_file, safe,
)
from utils.calendar_data import CalendarState, GROUP_CAPACITY, parse_anchor, one_line, shorten
from utils.calendar_view import CalendrierView as AgendaView
from utils.channel_resolver import resolve_text_channel
from utils.datetime_utils import PARIS
from utils.discord_history import fetch_channel_history

logger = logging.getLogger(__name__)
ORGANISATION_CHANNEL_FALLBACK = os.getenv("ORGANISATION_CHANNEL_NAME", "organisation")
CONSOLE_CHANNEL_FALLBACK = os.getenv("CHANNEL_CONSOLE", "console")
VALIDATED_ROLE_NAME = "Membre validé d'Evolution"
DATA_FILE = "activities_data.json"
MARKER_TEXT = "===BOTACTIVITES==="
MAX_GROUP_SIZE = GROUP_CAPACITY
SNAPSHOT_TIMEOUT = 15.0
DATE_TIME_REGEX = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})\s*(?:;|\s+)\s*(?P<time>\d{2}:\d{2})(?P<desc>.*)$",
    re.DOTALL,
)


def _activity_datetime_utc(value: datetime) -> datetime:
    return utc(value)


def parse_date_time(date_str, time_str):
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def parse_date_time_via_regex(line):
    match = DATE_TIME_REGEX.search(line or "")
    if not match:
        return None, None, None
    starts = parse_date_time(match["date"], match["time"])
    return line[:match.start()].strip() or "Sans titre", starts, match["desc"].strip()


class CalendrierView(AgendaView):
    """Keep the historical import and the activity module's Paris clock."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("clock", lambda: datetime.now(PARIS))
        super().__init__(*args, **kwargs)


class ActiviteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.activities_data = {"next_id": 1, "events": {}}
        self.initialized = False
        self.calendar_renderer = MonthlyRenderer()
        self.snapshot_store = ActivitySnapshotStore(bot)
        self._mutation_lock = asyncio.Lock()
        self._initialization_lock = asyncio.Lock()
        self._source_guild_id = None
        self._persistent_views = {}
        self._dirty_cards = set()
        self._sent_reminders = set()
        self._card_locks = {}
        self.role_manager = ActivityRoleManager(self)
        self._calendar_views = weakref.WeakSet()
        self._calendar_refresh_task = None
        self._calendar_dirty = False
        self._remote_uncertain = False

    def now(self):
        return datetime.now(timezone.utc)

    def _resolve_console_channel(self, guild):
        return resolve_text_channel(
            guild, id_env="CHANNEL_CONSOLE_ID", name_env="CHANNEL_CONSOLE",
            default_name=CONSOLE_CHANNEL_FALLBACK,
        )

    def _resolve_organisation_channel(self, guild):
        return resolve_text_channel(
            guild, id_env="ORGANISATION_CHANNEL_ID", name_env="ORGANISATION_CHANNEL_NAME",
            default_name=ORGANISATION_CHANNEL_FALLBACK,
        )

    def _source_guild(self):
        for guild in self.bot.guilds:
            if self._source_guild_id is not None:
                if guild.id == self._source_guild_id:
                    return guild
            elif self._resolve_console_channel(guild) is not None:
                return guild
        return None

    def _guard(self, ctx):
        if not self.initialized:
            raise ActivityError(
                "Les activités ne sont pas encore chargées. Le Staff doit vérifier l'accès à #console."
            )
        guild = getattr(ctx, "guild", None)
        if guild is None:
            raise ActivityError("Utilise les activités dans un salon du serveur.")
        source = self._source_guild()
        if source is not None and source.id != guild.id:
            raise ActivityError("Les activités appartiennent au serveur de la console, pas à ce serveur.")
        if source is None and len(self.bot.guilds) > 1:
            raise ActivityError("Le serveur des activités n'a pas pu être identifié.")

    def events_for_guild(self, guild_id):
        source = self._source_guild()
        if source is not None and source.id != guild_id:
            return {}
        if source is None and len(self.bot.guilds) > 1:
            return {}
        return {
            key: record for key, record in self.activities_data.get("events", {}).items()
            if record.get("guild_id") in (None, guild_id)
        }

    def _record(self, ctx, identifier):
        self._guard(ctx)
        key = str(identifier or "").strip()
        record = self.events_for_guild(ctx.guild.id).get(key)
        if record is None:
            raise ActivityError("Activité introuvable sur ce serveur. Ouvre `/activite liste`.")
        return record

    @staticmethod
    def is_staff(member):
        permissions = getattr(member, "guild_permissions", None)
        return bool(
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
            or any(role.name == "Staff" for role in getattr(member, "roles", ()))
        )

    def has_validated_role(self, member):
        configured = os.getenv("ACTIVITE_VALIDATED_ROLE_ID", "").strip()
        roles = getattr(member, "roles", ())
        if configured:
            return any(str(role.id) == configured for role in roles) or self.is_staff(member)
        return any(role.name == VALIDATED_ROLE_NAME for role in roles) or self.is_staff(member)

    def can_modify(self, ctx, event):
        creator = event.creator_id if isinstance(event, ActiviteData) else event["creator_id"]
        return ctx.author.id == creator or self.is_staff(ctx.author)

    def _require_validated(self, ctx):
        if not self.has_validated_role(ctx.author):
            raise ActivityError("Rôle invalide : cette action est réservée aux membres validés de la guilde.")

    async def cog_load(self):
        if not self.check_events_loop.is_running():
            self.check_events_loop.start()

    async def initialize_data(self):
        async with self._initialization_lock:
            if self.initialized:
                return
            guild = self._source_guild()
            if guild is None:
                logger.warning("Activities: console missing; initialization postponed")
                return
            channel = self._resolve_console_channel(guild)
            try:
                payload = await asyncio.wait_for(self.snapshot_store.load(channel), timeout=45)
                if payload is None and self._remote_uncertain:
                    raise ActivityError("Sauvegarde introuvable après une écriture incertaine : vérifier #console.")
                if payload is None and Path(DATA_FILE).exists():
                    payload = json.loads(Path(DATA_FILE).read_text(encoding="utf-8"))
                    logger.warning("Activities: no console snapshot, importing the legacy local cache")
                migrated = migrate_snapshot(
                    {"next_id": 1, "events": {}} if payload is None else payload, guild.id,
                )
                self._source_guild_id = guild.id
                if payload is not None and migrated != payload:
                    await asyncio.wait_for(self.snapshot_store.persist(channel, migrated), timeout=SNAPSHOT_TIMEOUT)
                self.activities_data = migrated
                self.initialized = True
                self._remote_uncertain = False
                await self.save_data_local()
                self._register_persistent_views()
                logger.info("Activities initialized: %s records, %s quarantined",
                            len(migrated["events"]), len(migrated.get("quarantine", {})))
            except Exception:
                self.initialized = False
                logger.exception("Activities restoration failed; writes remain disabled")
                return
        try:
            await asyncio.wait_for(self._recover_legacy_cards(guild), timeout=25)
        except Exception:
            logger.exception("Activity legacy card recovery deferred")

    async def save_data_local(self):
        """The local file is a disposable cache, never the authority over a console snapshot."""
        target = Path(DATA_FILE)
        temporary = target.with_suffix(target.suffix + ".temp")
        try:
            temporary.write_text(
                json.dumps(self.activities_data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            logger.warning("Activities local cache unavailable; console remains authoritative",
                           exc_info=True)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.debug("Activities temporary cache could not be removed", exc_info=True)

    async def dump_data_to_console(self, ctx, *, payload=None):
        return await self.dump_data_to_console_no_ctx(ctx.guild, payload=payload)

    async def dump_data_to_console_no_ctx(self, guild, *, payload=None):
        channel = self._resolve_console_channel(guild)
        if channel is None:
            raise ActivityError("Salon #console introuvable : aucune modification confirmée.")
        return await self.snapshot_store.persist(
            channel, self.activities_data if payload is None else payload,
        )

    async def _commit(self, candidate, *, ctx=None, guild=None):
        """Publish memory only after durable success; ambiguous writes require a reload."""
        if not self.initialized:
            raise ActivityError("Le registre est en cours de restauration. Vérifie /activite liste avant de réessayer.")
        try:
            operation = (
                self.dump_data_to_console(ctx, payload=candidate) if ctx is not None
                else self.dump_data_to_console_no_ctx(guild, payload=candidate)
            )
            await asyncio.wait_for(operation, timeout=SNAPSHOT_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            self.initialized = False
            self._remote_uncertain = True
            logger.error("Activity snapshot result uncertain; writes paused until console reload")
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ActivityError(
                "Discord met trop de temps à sauvegarder. Le registre va être relu ; "
                "vérifie /activite liste avant de refaire la demande."
            ) from exc
        self.activities_data = candidate
        await self.save_data_local()
        self._schedule_calendar_refresh()
        logger.debug("Activities snapshot committed events=%s", len(candidate["events"]))

    async def _update_event_fields(self, guild, identifier, **fields):
        """Merge side-effect metadata without overwriting concurrent roster changes."""
        async with self._mutation_lock:
            record = self.events_for_guild(guild.id).get(str(identifier))
            if record is None or all(record.get(k) == v for k, v in fields.items()):
                return
            candidate = copy.deepcopy(self.activities_data)
            candidate["events"][str(identifier)].update(fields)
            changed_card = self._card_signature(record) != self._card_signature(candidate["events"][str(identifier)])
            await self._commit(candidate, guild=guild)
            if changed_card:
                self._dirty_cards.add(str(identifier))

    def _schedule_calendar_refresh(self):
        self._calendar_dirty = True
        if self._calendar_views and (
            self._calendar_refresh_task is None or self._calendar_refresh_task.done()
        ):
            self._calendar_refresh_task = asyncio.create_task(self._refresh_calendars())

    async def _refresh_calendars(self):
        """Coalesce edits and never keep the datastore lock during image rendering or uploads."""
        try:
            while self._calendar_dirty:
                self._calendar_dirty = False
                await asyncio.sleep(0.5)
                for view in list(self._calendar_views):
                    if view.is_finished() or view.message is None:
                        self._calendar_views.discard(view)
                        continue
                    try:
                        await asyncio.wait_for(view.refresh_from_source(), timeout=20)
                    except discord.NotFound:
                        self._calendar_views.discard(view)
                    except Exception:
                        logger.warning("Activity calendar refresh deferred", exc_info=True)
        finally:
            self._calendar_refresh_task = None

    def _register_persistent_views(self):
        for record in self.activities_data.get("events", {}).values():
            if record.get("message_id"):
                self._register_view(record)
                self._dirty_cards.add(record["id"])

    def _register_view(self, record):
        old = self._persistent_views.pop(record["id"], None)
        if old:
            old.stop()
        view = ActivityCardView(self, record)
        self.bot.add_view(view, message_id=int(record["message_id"]))
        self._persistent_views[record["id"]] = view

    @staticmethod
    def _message_event_id(message):
        for row in getattr(message, "components", []):
            for component in getattr(row, "children", []):
                match = re.fullmatch(r"evo:activity:([0-9]+):[a-z]+",
                                     getattr(component, "custom_id", "") or "")
                if match:
                    return match[1]
        for embed in getattr(message, "embeds", []):
            footer = getattr(getattr(embed, "footer", None), "text", "") or ""
            match = re.search(r"Activité #([0-9]+)\b", footer)
            if not match and (embed.title or "").startswith("Nouvelle proposition"):
                match = re.search(r"\bID\s*=\s*([0-9]+)\b", embed.description or "")
            if match:
                return match[1]
        return None

    async def _recover_legacy_cards(self, guild):
        """Recover identifiable old cards without relying on the gateway message cache."""
        channel = self._resolve_organisation_channel(guild)
        if channel is None:
            return
        missing = {
            key for key, data in self.events_for_guild(guild.id).items()
            if not data.get("message_id") and not data.get("cancelled")
        }
        if not missing:
            return
        messages = await fetch_channel_history(channel, limit=200, reason="activities.cards.migrate")
        async with self._mutation_lock:
            candidate = copy.deepcopy(self.activities_data)
            changed = []
            for message in messages:
                key = self._message_event_id(message)
                if message.author == self.bot.user and key in missing:
                    candidate["events"][key].update(
                        channel_id=channel.id, message_id=message.id, publication_pending=False,
                    )
                    changed.append(key)
                    missing.remove(key)
            if changed:
                try:
                    await self._commit(candidate, guild=guild)
                    for key in changed:
                        self._register_view(candidate["events"][key])
                        self._dirty_cards.add(key)
                except Exception:
                    logger.exception("Activities: old card migration postponed")

    async def sync_card(self, identifier, guild, *, publish=False):
        """Bound all Discord work; a saved roster remains authoritative on presentation failure."""
        key = str(identifier)
        try:
            return await asyncio.wait_for(self._sync_card(key, guild, publish=publish), timeout=25)
        except Exception:
            self._dirty_cards.add(key)
            logger.exception("Activities card sync deferred event_id=%s", key)
            return False

    @staticmethod
    def _card_signature(record):
        return tuple(repr(record.get(name)) for name in (
            "titre", "description", "lieu", "starts_at", "date_str", "ends_at",
            "participants", "waitlist", "capacity", "creator_id", "cancelled",
            "role_id", "role_error", "announcement_url",
        ))

    async def _sync_card(self, key, guild, *, publish=False):
        async with self._card_locks.setdefault(key, asyncio.Lock()):
            original = self.events_for_guild(guild.id).get(key)
            if original is None:
                return False
            record = copy.deepcopy(original)
            channel = self._resolve_organisation_channel(guild)
            if record.get("channel_id"):
                channel = guild.get_channel(int(record["channel_id"])) or channel
            if channel is None:
                self._dirty_cards.add(key)
                return False
            me = getattr(guild, "me", None)
            if me is not None:
                permissions = channel.permissions_for(me)
                if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                    raise ActivityError("Autoriser Voir le salon, Envoyer des messages et Intégrer des liens.")
            message = None
            if record.get("message_id"):
                try:
                    message = await channel.fetch_message(int(record["message_id"]))
                except discord.NotFound:
                    publish = publish or (not record.get("cancelled") and activity_end(record) > self.now())
                    await self._update_event_fields(
                        guild, key, message_id=None, publication_pending=publish,
                    )
                    record = copy.deepcopy(self.activities_data["events"][key])
            publish = publish or record.get("publication_pending", False)
            if message is None and not publish:
                self._dirty_cards.discard(key)
                return False
            if message is None:
                recent = await fetch_channel_history(
                    channel, limit=200, reason="activities.card.recover", raise_errors=True,
                )
                message = next((
                    msg for msg in recent
                    if msg.author == self.bot.user and self._message_event_id(msg) == key
                ), None)
            if message is not None and message.author != self.bot.user:
                raise ActivityError("La fiche enregistrée n'appartient pas au bot.")
            record = copy.deepcopy(self.activities_data["events"][key])
            signature = self._card_signature(record)
            view = ActivityCardView(self, record)
            artwork, warning = announcement_image(
                channel, retained=getattr(message, "attachments", ()),
            )
            embed = activity_embed(
                record, now=self.now(), image_url=f"attachment://{IMAGE_FILENAME}" if artwork else None,
            )
            if warning:
                embed.add_field(name="Illustration", value=warning, inline=False)
            created = message is None
            try:
                if created:
                    kwargs = {"file": artwork} if isinstance(artwork, discord.File) else {}
                    message = await channel.send(
                        embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none(), **kwargs,
                    )
                else:
                    await message.edit(
                        content=None, embed=embed, view=view, attachments=[artwork] if artwork else [],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            finally:
                close_artwork(artwork)
            try:
                await self._update_event_fields(
                    guild, key, channel_id=channel.id, message_id=message.id, publication_pending=False,
                )
            except Exception:
                self._dirty_cards.add(key)
                logger.warning("Activity card sent but link not committed; recovery will scan event_id=%s", key)
                raise
            self._register_view(self.activities_data["events"][key])
            if self._card_signature(self.activities_data["events"][key]) == signature:
                self._dirty_cards.discard(key)
            else:
                self._dirty_cards.add(key)
            logger.debug("Activity announcement synced event_id=%s created=%s", key, created)
            return True

    def _card_link(self, record):
        if record.get("message_id") and record.get("channel_id") and record.get("guild_id"):
            return (
                f"https://discord.com/channels/{record['guild_id']}/"
                f"{record['channel_id']}/{record['message_id']}"
            )
        return None

    async def _notify_members(self, guild, record, text, member_ids):
        channel = self._resolve_organisation_channel(guild)
        if channel is None or not member_ids:
            return
        unique = list(dict.fromkeys(member_ids))
        link = self._card_link(record)
        for offset in range(0, len(unique), 40):
            users = [discord.Object(id=uid) for uid in unique[offset:offset + 40]]
            mentions = " ".join(f"<@{user.id}>" for user in users)
            try:
                await channel.send(
                    f"{mentions}\n{text}" + (f"\n{link}" if link else ""),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=users, replied_user=False,
                    ),
                )
            except discord.HTTPException:
                logger.warning("Activities notification failed event_id=%s", record["id"], exc_info=True)

    async def _sync_legacy_roles(self, guild, identifier, user_ids=()):
        """Keep the historical entry point; reconcile the entire team, not only one click."""
        try:
            return await asyncio.wait_for(self.role_manager.sync(guild, identifier), timeout=25)
        except Exception:
            logger.exception("Activity role synchronization deferred event_id=%s", identifier)
            return False

    @commands.command(name="activite")
    @commands.guild_only()
    async def activite_main(self, ctx, action=None, *, args=None):
        aliases = {"créer": "creer", "rejoindre": "join", "quitter": "leave", "aide": "guide"}
        action = aliases.get((action or "liste").lower(), (action or "liste").lower())
        handlers = {
            "creer": self.command_creer, "modifier": self.command_modifier,
            "join": self.command_join, "leave": self.command_leave,
            "annuler": self.command_annuler, "info": self.command_info,
            "liste": self.command_liste, "guide": self.command_guide,
            "gerer": self.command_gerer, "publier": self.command_publier,
            "participants": self.command_participants, "depuis": self.command_depuis,
        }
        try:
            self._guard(ctx)
            if action not in handlers:
                raise ActivityError("Action inconnue. Ouvre `/activite aide`.")
            logger.debug("Activity action=%s guild_id=%s user_id=%s", action, ctx.guild.id, ctx.author.id)
            await handlers[action](ctx, args)
        except ActivityError as exc:
            await ctx.send(str(exc), allowed_mentions=discord.AllowedMentions.none())

    async def command_guide(self, ctx, args=None):
        await ctx.send(
            "**Sorties de la guilde**\n"
            "`/activite creer` ouvre un formulaire : quoi, quand, où, places et précisions.\n"
            "Exemples de date : `demain 21h`, `vendredi 20h30`, `18/09/2026 21:00` (Paris).\n"
            "Tu vérifies l'aperçu avant de créer. Tu es inscrit automatiquement et comptes dans les places.\n"
            "L'annonce avec image et boutons est publiée automatiquement dans #organisation, sans ping général.\n"
            "La durée prévue est de 180 minutes par défaut (réglable par le Staff) ; "
            "utilise l'option `duree` de `/activite creer` ou `/activite modifier` pour la changer.\n\n"
            "`/activite rejoindre` sans argument : choisis une sortie dans la liste pour t'inscrire.\n"
            "`/activite quitter` sans argument : choisis l'inscription à retirer.\n"
            "`/activite liste` : à venir, tes inscriptions (attente incluse), tes sorties, historique.\n"
            "Les boutons **S'inscrire / Liste d'attente** et **Se désinscrire** gèrent le groupe. "
            "Une place libérée revient au premier membre en attente.\n"
            "**Gérer** : modifier, annuler avec confirmation, réparer une fiche supprimée.\n"
            "Les inscrits reçoivent le rôle d'équipe, retiré au départ de la liste et supprimé à la fin prévue.\n"
            "`/calendrier` utilise exactement les mêmes activités et inscriptions ; les sessions ouvertes sont actualisées.\n"
            "Les anciennes commandes `!activite` restent compatibles.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def command_depuis(self, ctx, args=None):
        self._require_validated(ctx)
        message = getattr(ctx, "slash_values", {}).get("_source_message")
        channel = self._resolve_organisation_channel(ctx.guild)
        if message is None or channel is None or message.channel.id != channel.id:
            raise ActivityError("Utilise ce menu sur une annonce du salon d'organisation.")
        if message.guild.id != ctx.guild.id or (
            message.author.id != ctx.author.id and not self.is_staff(ctx.author)
        ):
            raise ActivityError("Tu peux convertir tes propres annonces ; le Staff peut aider les autres membres.")
        if not message.content.strip():
            raise ActivityError("Cette annonce ne contient pas de texte. Utilise `/activite creer`.")
        await self._open_form(
            ctx, initial={"titre": one_line(message.content.splitlines()[0], 85),
                          "description": shorten(message.content, 1500)},
            announcement_url=message.jump_url, creation_key=f"announcement:{message.id}",
        )

    async def _open_form(self, ctx, record=None, *, initial=None, announcement_url=None, creation_key=None):
        if record is None:
            self._require_validated(ctx)
        elif not self.can_modify(ctx, record):
            raise ActivityError("Seuls l'organisateur et le Staff peuvent modifier cette sortie.")
        if record and record.get("cancelled"):
            raise ActivityError("Cette sortie est annulée. Crée une nouvelle sortie.")
        if getattr(ctx, "interaction", None) is None:
            await ctx.send(
                "Ouvre `/activite creer` ou `/activite modifier` pour le formulaire. "
                "L'ancien format reste accepté : `!activite creer Titre JJ/MM/AAAA HH:MM Description`."
            )
            return
        values = dict(initial or {})
        if record:
            values = {
                "titre": record["titre"],
                "date": utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
                        .astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
                "lieu": record.get("lieu", ""), "description": record.get("description", ""),
                "capacite": record.get("capacity", DEFAULT_CAPACITY),
                "duree": record.get("duration_minutes", 180),
            }
        supplied_duration = getattr(ctx, "slash_values", {}).get("duree")
        if supplied_duration is not None:
            values["duree"] = supplied_duration
        await ctx.interaction.response.send_modal(ActivityModal(
            self, ctx.author.id, ctx.guild.id, values=values,
            event_id=record["id"] if record else None,
            revision=record.get("revision", 0) if record else None,
            announcement_url=record.get("announcement_url") if record else announcement_url,
            creation_key=creation_key,
        ))
        ctx.response_count += 1

    async def command_creer(self, ctx, line=None):
        self._guard(ctx)
        self._require_validated(ctx)
        supplied = getattr(ctx, "slash_values", {})
        if not line and not supplied.get("_draft") and not supplied.get("date"):
            return await self._open_form(ctx)
        values = dict(supplied)
        if not supplied.get("date"):
            title, starts, description = parse_date_time_via_regex(line)
            if starts is None:
                raise ActivityError("Utilise `/activite creer` ou Titre JJ/MM/AAAA HH:MM Description.")
            values.update(titre=title, date=starts.strftime("%d/%m/%Y %H:%M"), description=description)
        draft = validate_draft(values, now=self.now())
        source_url = supplied.get("_announcement_url")
        if source_url:
            if not re.fullmatch(rf"https://discord\.com/channels/{ctx.guild.id}/[0-9]+/[0-9]+", source_url):
                raise ActivityError("Le lien d'annonce doit appartenir à ce serveur.")
            draft["announcement_url"] = source_url
        creation_key = supplied.get("_creation_key") or (
            f"message:{ctx.guild.id}:{ctx.message.id}" if getattr(ctx, "message", None) else uuid.uuid4().hex
        )
        async with self._mutation_lock:
            existing = next((
                data for data in self.events_for_guild(ctx.guild.id).values()
                if data.get("creation_key") == creation_key and data["creator_id"] == ctx.author.id
            ), None)
            if existing:
                key = existing["id"]
            else:
                channel = self._resolve_organisation_channel(ctx.guild)
                if channel is None:
                    raise ActivityError("Le salon d'organisation est introuvable. Demande au Staff de le configurer.")
                candidate = copy.deepcopy(self.activities_data)
                key = str(candidate.get("next_id", 1))
                while key in candidate["events"]:
                    key = str(int(key) + 1)
                candidate["next_id"] = int(key) + 1
                candidate["events"][key] = {
                    **draft, "id": key, "guild_id": ctx.guild.id,
                    "creator_id": ctx.author.id, "participants": [ctx.author.id], "waitlist": [],
                    "role_id": None, "cancelled": False, "revision": 0,
                    "reminder_24_sent": False, "reminder_1_sent": False,
                    "creation_key": creation_key, "publication_pending": True,
                    "role_sync_pending": True, "role_member_ids": [],
                }
                await self._commit(candidate, ctx=ctx)
        ctx.activity_committed = True
        record = self.activities_data["events"][key]
        receipt = await ctx.send(
            f"✅ Sortie **{safe(record['titre'], 120)}** enregistrée (#{key}). **Tu es inscrit.**\n"
            "Publication de l'annonce et préparation du rôle d'équipe…",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        role_ok, synced = await asyncio.gather(
            self._sync_legacy_roles(ctx.guild, key), self.sync_card(key, ctx.guild, publish=True),
        )
        if synced and key in self._dirty_cards:
            synced = await self.sync_card(key, ctx.guild)
        record = self.activities_data["events"][key]
        link = self._card_link(record)
        content = (
            f"✅ Sortie **{safe(record['titre'], 120)}** enregistrée (#{key}). **Tu es inscrit.**\n"
            + (f"Annonce dans #organisation : {link}" if synced and link else
               "L'annonce est en attente ; le bot réessaiera automatiquement. "
               "Vérifier les permissions de #organisation. Ne recrée pas la sortie.")
        )
        if not role_ok:
            content += "\n⚠️ Rôle d'équipe en attente : vérifier « Gérer les rôles » et la hiérarchie du bot."
        if receipt is not None:
            try:
                await receipt.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.warning("Activity receipt update unavailable event_id=%s", key)

    async def command_liste(self, ctx, args=None, *, action="info"):
        self._guard(ctx)
        view = ActivityListView(self, ctx.author.id, ctx.guild.id, action=action)
        view.message = await ctx.send(
            embed=view.build_embed(), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def command_info(self, ctx, args):
        record = self._record(ctx, args)
        artwork, _ = announcement_image(getattr(ctx, "channel", None))
        try:
            kwargs = {"file": artwork} if isinstance(artwork, discord.File) else {}
            await ctx.send(
                embed=activity_embed(
                    record, now=self.now(),
                    image_url=f"attachment://{IMAGE_FILENAME}" if artwork else None,
                ), view=ActivityCardView(self, record, persistent=False),
                allowed_mentions=discord.AllowedMentions.none(), **kwargs,
            )
        finally:
            close_artwork(artwork)

    async def command_participants(self, ctx, args):
        record = self._record(ctx, args)
        file = roster_file(record, ctx.guild)
        embed = discord.Embed(title=f"Participants · {safe(record['titre'], 180)}", colour=discord.Colour.blue())
        for label, ids in (
            ("Inscrits", record.get("participants", [])), ("Liste d'attente", record.get("waitlist", [])),
        ):
            lines = [
                f"{i}. <@{uid}> — {safe(getattr(ctx.guild.get_member(uid), 'display_name', 'Membre'), 50)}"
                for i, uid in enumerate(ids, 1)
            ]
            embed.add_field(name=f"{label} · {len(ids)}", value=shorten("\n".join(lines), 1000) or "Personne.",
                            inline=False)
        embed.set_footer(text="La pièce jointe contient la liste intégrale si elle est abrégée ci-dessus.")
        try:
            await ctx.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
        finally:
            file.close()

    async def command_gerer(self, ctx, args):
        record = self._record(ctx, args)
        if not self.can_modify(ctx, record):
            raise ActivityError("Seuls l'organisateur et le Staff peuvent gérer cette sortie.")
        view = ActivityManageView(self, ctx.author.id, ctx.guild.id, record["id"])
        view.message = await ctx.send(
            embed=activity_embed(record, now=self.now()), view=view, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def command_publier(self, ctx, args):
        record = self._record(ctx, args)
        if not self.can_modify(ctx, record):
            raise ActivityError("Seuls l'organisateur et le Staff peuvent republier cette fiche.")
        await self._sync_legacy_roles(ctx.guild, record["id"])
        result = await self.sync_card(record["id"], ctx.guild, publish=True)
        await ctx.send("Fiche publiée et actualisée." if result else
                       "Impossible de publier la fiche. Vérifie le salon et les permissions du bot.")

    async def _change_membership(self, ctx, args, action):
        self._record(ctx, args)
        if action == "join":
            self._require_validated(ctx)
        async with self._mutation_lock:
            record = self._record(ctx, args)
            candidate = copy.deepcopy(self.activities_data)
            changed = candidate["events"][record["id"]]
            changed["waitlist"] = [
                uid for uid in changed.get("waitlist", [])
                if (member := ctx.guild.get_member(uid)) is None or self.has_validated_role(member)
            ]
            changed["role_member_ids"] = list(dict.fromkeys(
                changed.get("role_member_ids", []) + changed.get("participants", [])
            ))
            text, promoted = change_roster(changed, ctx.author.id, action, now=self.now())
            changed["role_sync_pending"] = True
            await self._commit(candidate, ctx=ctx)
            self._dirty_cards.add(record["id"])
        ctx.activity_committed = True
        current = self.activities_data["events"][record["id"]]
        link = self._card_link(current)
        await ctx.send(
            f"✅ {ctx.author.mention} {text}\n**{safe(current['titre'], 100)}** · "
            f"{len(current['participants'])}/{current.get('capacity', DEFAULT_CAPACITY)} inscrits"
            + (f"\nAnnonce : {link}" if link else ""),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._sync_legacy_roles(ctx.guild, record["id"], [ctx.author.id, *promoted])
        await self.sync_card(record["id"], ctx.guild)
        current = self.activities_data["events"][record["id"]]
        if promoted:
            await self._notify_members(
                ctx.guild, current, f"Une place s'est libérée pour **{safe(current['titre'], 100)}** : "
                "tu passes de la liste d'attente aux inscrits.", promoted,
            )

    async def command_join(self, ctx, args=None):
        if not str(args or "").strip():
            return await self.command_liste(ctx, action="join")
        await self._change_membership(ctx, args, "join")

    async def command_leave(self, ctx, args=None):
        if not str(args or "").strip():
            return await self.command_liste(ctx, action="leave")
        await self._change_membership(ctx, args, "leave")

    async def command_annuler(self, ctx, args):
        async with self._mutation_lock:
            record = self._record(ctx, args)
            if not self.can_modify(ctx, record):
                raise ActivityError("Seuls l'organisateur et le Staff peuvent annuler cette sortie.")
            if record.get("cancelled"):
                raise ActivityError("Cette activité est déjà annulée.")
            candidate = copy.deepcopy(self.activities_data)
            candidate["events"][record["id"]]["cancelled"] = True
            candidate["events"][record["id"]]["role_sync_pending"] = True
            candidate["events"][record["id"]]["revision"] = int(record.get("revision", 0)) + 1
            await self._commit(candidate, ctx=ctx)
            self._dirty_cards.add(record["id"])
        ctx.activity_committed = True
        await ctx.send(f"Sortie #{record['id']} annulée. L'historique et les listes sont conservés.")
        await self._sync_legacy_roles(ctx.guild, record["id"])
        await self.sync_card(record["id"], ctx.guild)
        await self._notify_members(
            ctx.guild, self.activities_data["events"][record["id"]],
            f"Sortie annulée : **{safe(record['titre'], 100)}**.",
            record.get("participants", []) + record.get("waitlist", []),
        )

    async def command_modifier(self, ctx, args):
        parts = str(args or "").split(" ", 1)
        record = self._record(ctx, parts[0])
        supplied = getattr(ctx, "slash_values", {})
        if len(parts) == 1 and not supplied.get("_draft") and not supplied.get("date"):
            return await self._open_form(ctx, record)
        if not self.can_modify(ctx, record):
            raise ActivityError("Seuls l'organisateur et le Staff peuvent modifier cette sortie.")
        values = {
            "titre": record["titre"], "lieu": record.get("lieu", ""),
            "capacite": record.get("capacity", DEFAULT_CAPACITY),
            "description": record.get("description", ""),
            "duree": record.get("duration_minutes", 180),
        }
        values.update({k: v for k, v in supplied.items() if v is not None})
        if not supplied.get("date"):
            _, starts, description = parse_date_time_via_regex(parts[1] if len(parts) > 1 else "")
            if starts is None:
                raise ActivityError("Date invalide. Ouvre `/activite modifier` pour le formulaire.")
            values.update(date=starts.strftime("%d/%m/%Y %H:%M"), description=description)
        draft = validate_draft(values, now=self.now())
        async with self._mutation_lock:
            record = self._record(ctx, parts[0])
            if not self.can_modify(ctx, record):
                raise ActivityError("Tu n'es plus autorisé à modifier cette sortie.")
            candidate = copy.deepcopy(self.activities_data)
            changed = candidate["events"][record["id"]]
            apply_draft(changed, draft, revision=supplied.get("_revision"))
            changed["role_sync_pending"] = True
            promoted = []
            while changed.get("waitlist") and len(changed["participants"]) < changed["capacity"]:
                uid = changed["waitlist"].pop(0)
                member = ctx.guild.get_member(uid)
                if member is None or self.has_validated_role(member):
                    changed["participants"].append(uid)
                    promoted.append(uid)
            await self._commit(candidate, ctx=ctx)
            self._dirty_cards.add(record["id"])
        ctx.activity_committed = True
        await ctx.send(f"Sortie #{record['id']} modifiée ; calendrier et inscriptions à jour.")
        await self._sync_legacy_roles(ctx.guild, record["id"], promoted)
        await self.sync_card(record["id"], ctx.guild)
        current = self.activities_data["events"][record["id"]]
        await self._notify_members(
            ctx.guild, current,
            f"Sortie **{safe(current['titre'], 100)}** modifiée : consulte la fiche actualisée.",
            current.get("participants", []) + current.get("waitlist", []),
        )
        if promoted:
            await self._notify_members(
                ctx.guild, current, "La capacité augmente : tu es maintenant inscrit à la sortie.",
                promoted,
            )

    @tasks.loop(minutes=1)
    async def check_events_loop(self):
        if not self.bot.is_ready():
            return
        if not self.initialized:
            await self.initialize_data()
            return
        guild = self._source_guild()
        if guild is None:
            return
        for key in list(self.events_for_guild(guild.id)):
            try:
                await asyncio.wait_for(self._maintain_event(guild, key), timeout=65)
            except Exception:
                logger.exception("Activity maintenance failed event_id=%s; next event continues", key)
        self._schedule_calendar_refresh()

    async def _maintain_event(self, guild, key):
        """Never keep the roster lock during reminders, role requests or announcement edits."""
        async with self._mutation_lock:
            original = self.activities_data["events"].get(key)
            if original is None:
                return
            candidate = copy.deepcopy(self.activities_data)
            record = candidate["events"][key]
            event = ActiviteData.from_dict(record)
            remaining = (utc(event.date_obj) - self.now()).total_seconds()
            changed = False
            if record.get("cancelled") or remaining <= 0:
                if not record.get("closed"):
                    record["closed"] = True
                    changed = True
            if record.get("cancelled") or activity_end(record) <= self.now():
                if not record.get("completed"):
                    record["completed"] = True
                    changed = True
                if not record.get("message_id") and record.get("publication_pending"):
                    record["publication_pending"] = False
                    changed = True
            if changed:
                await self._commit(candidate, guild=guild)
                self._dirty_cards.add(key)
            kind = None
            if not record.get("cancelled") and remaining > 0:
                kind = (
                    "1h" if remaining <= 3600 and not event.reminder_1_sent else
                    "24h" if 3600 < remaining <= 86400 and not event.reminder_24_sent else None
                )
        channel = self._resolve_organisation_channel(guild)
        if kind and channel is not None:
            reminder_key = (key, record.get("revision", 0), kind)
            logger.debug(
                "Activite rappel selectionne event_id=%s echeance=%s time_left_seconds=%.0f",
                key, kind, remaining,
            )
            sent = reminder_key in self._sent_reminders
            if not sent:
                sent = await self.envoyer_rappel(channel, event, kind)
            if sent:
                self._sent_reminders.add(reminder_key)
                async with self._mutation_lock:
                    latest = self.activities_data["events"].get(key)
                    if latest is not None and latest.get("revision", 0) == record.get("revision", 0):
                        candidate = copy.deepcopy(self.activities_data)
                        candidate["events"][key]["reminder_24_sent"] = True
                        if kind == "1h":
                            candidate["events"][key]["reminder_1_sent"] = True
                        await self._commit(candidate, guild=guild)
                    self._sent_reminders.discard(reminder_key)
        current = self.activities_data["events"].get(key, {})
        if (not current.get("completed") or current.get("role_id")
                or current.get("role_staging_name") or current.get("role_sync_pending")):
            await self._sync_legacy_roles(guild, key)
        current = self.activities_data["events"].get(key, {})
        if key in self._dirty_cards or current.get("publication_pending"):
            await self.sync_card(key, guild, publish=bool(current.get("publication_pending")))

    async def envoyer_rappel(self, channel, event: ActiviteData, kind: str) -> bool:
        start = utc(event.date_obj)
        record = event.to_dict()
        text = (
            f"⏰ **Rappel** : {safe(event.titre, 100)}\n"
            f"Début le {start.astimezone(PARIS):%d/%m/%Y à %H:%M} (heure de Paris) "
            f"• <t:{int(start.timestamp())}:R>"
        )
        link = self._card_link(record)
        chunks = [event.participants[i:i + 40] for i in range(0, len(event.participants), 40)] or [[]]
        try:
            for chunk in chunks:
                users = [discord.Object(id=uid) for uid in chunk]
                mentions = " ".join(f"<@{user.id}>" for user in users)
                await asyncio.wait_for(channel.send(
                    text + (f"\n{mentions}" if mentions else "") + (f"\n{link}" if link else ""),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=users, replied_user=False,
                    ),
                ), timeout=12)
        except Exception:
            logger.warning("Activite rappel echoue event_id=%s echeance=%s", event.id, kind,
                           exc_info=True)
            return False
        logger.debug("Activite rappel envoye event_id=%s echeance=%s", event.id, kind)
        return True

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if not self.initialized or member.guild.id != getattr(self._source_guild(), "id", None):
            return
        notifications = []
        try:
            async with self._mutation_lock:
                candidate = copy.deepcopy(self.activities_data)
                changed_keys = []
                for key, record in candidate["events"].items():
                    if record.get("cancelled") or utc(datetime.fromisoformat(
                        record.get("starts_at") or record["date_str"])) <= self.now():
                        continue
                    if member.id not in record.get("participants", []) + record.get("waitlist", []):
                        continue
                    record["role_member_ids"] = list(dict.fromkeys(
                        record.get("role_member_ids", []) + record.get("participants", [])
                    ))
                    record["role_sync_pending"] = True
                    _, promoted = change_roster(record, member.id, "leave", now=self.now())
                    notifications.append((key, promoted))
                    changed_keys.append(key)
                if changed_keys:
                    await self._commit(candidate, guild=member.guild)
                    self._dirty_cards.update(changed_keys)
            for key, promoted in notifications:
                await self._sync_legacy_roles(member.guild, key, promoted)
                await self.sync_card(key, member.guild)
                await self._notify_members(
                    member.guild, self.activities_data["events"][key],
                    "Un membre a quitté le serveur : une place t'est attribuée.", promoted,
                )
        except Exception:
            logger.exception("Activities: departed member cleanup failed user_id=%s", member.id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if not self.initialized or payload.guild_id is None:
            return
        for key, record in self.events_for_guild(payload.guild_id).items():
            if record.get("message_id") == payload.message_id:
                self._dirty_cards.add(key)
                logger.warning("Activity announcement deleted; repair queued event_id=%s", key)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        if not self.initialized or payload.guild_id is None:
            return
        for key, record in self.events_for_guild(payload.guild_id).items():
            if record.get("message_id") in payload.message_ids:
                self._dirty_cards.add(key)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.initialize_data()

    def cog_unload(self):
        self.check_events_loop.cancel()
        if self._calendar_refresh_task is not None:
            self._calendar_refresh_task.cancel()
        for calendar in list(self._calendar_views):
            calendar.stop()
        self._calendar_views.clear()
        for view in self._persistent_views.values():
            view.stop()
        self._persistent_views.clear()

    @commands.command(name="calendrier")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def afficher_calendrier(
        self, ctx, vue: str = "mois", date: str = "",
        filtre: str = "toutes", prive: bool = False,
    ):
        """J'ouvre le mois courant ; les arguments restent compatibles avec l'ancien préfixe."""
        if not self.initialized:
            return await ctx.send("Données en cours de chargement. Réessaie dans quelques secondes.")
        if prive and getattr(ctx, "interaction", None) is None:
            return await ctx.send(
                "Ouvre `/calendrier`, puis **Filtres et options → Ouvrir en privé**."
            )
        try:
            today = datetime.now(PARIS).date()
            state = CalendarState(parse_anchor(date, today), vue.lower(), filtre.lower())
        except ValueError as exc:
            return await ctx.send(str(exc))

        guild = getattr(ctx, "guild", None)
        guilds = getattr(self.bot, "guilds", ())
        if guild is not None and len(guilds) > 1:
            source_guild = next(
                (candidate for candidate in guilds
                 if self._resolve_console_channel(candidate) is not None), None,
            )
            if source_guild is None or source_guild.id != guild.id:
                return await ctx.send(
                    "Les activités de ce bot sont rattachées au serveur de sa console. "
                    "Le calendrier n’est pas disponible ici."
                )

        can_attach = True
        channel = getattr(ctx, "channel", None)
        me = getattr(guild, "me", None)
        if channel is not None and me is not None:
            permissions = channel.permissions_for(me)
            if not permissions.embed_links:
                return await ctx.send(
                    "Il me manque la permission « Intégrer des liens » pour afficher l’agenda."
                )
            can_attach = permissions.attach_files

        view = CalendrierView(
            ctx.author, {}, source=lambda: self.events_for_guild(ctx.guild.id),
            state=state, guild=guild, renderer=self.calendar_renderer,
            action=self._calendar_activity_action, attach_files=can_attach,
            registry=self._calendar_views,
            attachment_limit=min(getattr(guild, "filesize_limit", 8 * 1024 * 1024), 8 * 1024 * 1024),
        )
        await view.send_initial(ctx.send)
        logger.debug("Calendar: opened user_id=%s mode=%s anchor=%s",
                     ctx.author.id, state.mode, state.anchor)

    async def _calendar_activity_action(self, interaction, event_id: str, action: str) -> None:
        """Run the existing command pipeline, including checks, roles and console persistence."""
        from utils.slash_support import invoke_from_component

        if action not in {"join", "leave"}:
            raise ValueError("Unsupported calendar action")
        await invoke_from_component(
            self.bot, interaction, "activite", f"{action} {event_id}",
        )



async def setup(bot: commands.Bot):
    await bot.add_cog(ActiviteCog(bot))
