# coding: utf-8
"""
Cog : EventConversation – version « rôle & salon privés par événement »
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
import copy
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import dateparser
import discord
from discord import app_commands


def _ensure_utils() -> None:
    utils = getattr(discord, "utils", None)
    if utils is None:
        return
    if not hasattr(utils, "is_inside_class"):

        def _is_inside_class(obj: Any) -> bool:
            qualname = getattr(obj, "__qualname__", "")
            return "." in qualname and "<locals>" not in qualname

        try:
            setattr(utils, "is_inside_class", _is_inside_class)
        except Exception:
            return
    if not hasattr(utils, "evaluate_annotation"):

        def _evaluate_annotation(
            annotation: Any,
            globalns: Optional[Dict[str, Any]] = None,
            localns: Optional[Dict[str, Any]] = None,
            cache: Optional[Dict[str, Any]] = None,
        ):
            if isinstance(annotation, str):
                try:
                    return eval(annotation, globalns or {}, localns or {})
                except Exception:
                    return annotation
            return annotation

        try:
            setattr(utils, "evaluate_annotation", _evaluate_annotation)
        except Exception:
            return


_ensure_utils()
from discord.ext import commands, tasks
from utils.slash_support import delete_invocation_message, notify_private_workflow
from zoneinfo import ZoneInfo

from models import EventData
from utils.console_store import ConsoleStore
from utils.storage import EventStore
from utils.datetime_utils import parse_french_datetime
from utils.channel_resolver import resolve_text_channel
from utils.guild_scope import require_guild, is_allowed_guild, allowed_guilds
from utils.event_provenance import (EventSecurityError, provenance_key, sign_event, verify_event, resource_marker, utc_datetime)
from utils.dm_sessions import dm_workflow, dm_check_for, finish_dm_input, cancel_dm_flows

__all__ = ["setup"]

# --------------------------------------------------------------------------- #
# Configuration générale                                                      #
# --------------------------------------------------------------------------- #

LOCAL_TZ = ZoneInfo("Europe/Paris")
DM_TIMEOUT = 15 * 60
MIN_DELTA = timedelta(minutes=5)
MAX_DESC_LEN = 1_000

SYSTEM_PROMPT = (
    "Tu es EvolutionBOT et tu aides à créer un événement Discord. "
    "À partir de la conversation suivante, fournis UNIQUEMENT un JSON strict "
    'avec les clés : name, description, start_time, end_time, location, max_slots, dungeon_name. '
    "Les dates sont au format JJ/MM/AAAA HH:MM. Mets null si information manquante."
)

EMBED_COLOR_PREVIEW = 0x3498DB
EMBED_COLOR_ANNOUNCE = 0x1ABC9C

STAFF_ROLE_NAME = "Staff"               # rôle qui doit voir le salon privé
EVENT_CAT_NAME = "Événements"           # catégorie où créer les salons privés


# --------------------------------------------------------------------------- #
# Utils                                                                       #
# --------------------------------------------------------------------------- #

_slug_re = re.compile(r"[^a-z0-9-]+")


def slugify(txt: str) -> str:
    """event name -> 'tournoi-among-us' (max 90 car)."""
    slug = _slug_re.sub("-", txt.lower()).strip("-")
    return slug[:90] if slug else "event"


# --------------------------------------------------------------------------- #
# Data : EventDraft                                                           #
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class EventDraft:
    name: str
    description: str
    start_time: datetime                 # UTC aware
    end_time: datetime                   # UTC aware
    location: Optional[str] = None
    max_slots: Optional[int] = None
    dungeon_name: str = "Donjon"

    # ---------- helpers date --------------------------------------------- #
    @staticmethod
    def _parse_dt(raw: str | datetime | None) -> Optional[datetime]:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            dt = raw
        else:
            try:
                dt = datetime.strptime(raw, "%d/%m/%Y %H:%M")
            except ValueError:
                dt = dateparser.parse(
                    raw,
                    languages=["fr"],
                    settings={
                        "TIMEZONE": str(LOCAL_TZ),
                        "RETURN_AS_TIMEZONE_AWARE": True,
                        "PREFER_DATES_FROM": "future",
                    },
                )
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(timezone.utc)

    # ---------- from JSON ------------------------------------------------- #
    @classmethod
    def from_json(cls, obj: Dict[str, Any]) -> "EventDraft":
        start = cls._parse_dt(obj.get("start_time"))
        end = cls._parse_dt(obj.get("end_time"))
        if start is None:
            raise ValueError("La date de début est manquante ou mal comprise.")
        if end is None:
            end = start + timedelta(hours=1)
        if end <= start:
            raise ValueError("L’heure de fin doit être après l’heure de début.")
        return cls(
            name=str(obj.get("name") or "Événement")[:100],
            description=str(obj.get("description") or "Aucune description")[:MAX_DESC_LEN],
            start_time=start,
            end_time=end,
            location=str(obj["location"]) if obj.get("location") else None,
            max_slots=int(obj["max_slots"]) if obj.get("max_slots") is not None else None,
            dungeon_name=str(obj.get("dungeon_name") or "Donjon"),
        )

    # ---------- embeds ---------------------------------------------------- #
    def _fmt_dt(self, dt: datetime) -> str:
        return dt.strftime("%d/%m/%Y %H:%M UTC")

    def to_preview_embed(self) -> discord.Embed:
        e = discord.Embed(title=f"📅 {self.name}", description=self.description, colour=EMBED_COLOR_PREVIEW)
        e.add_field(name="Début", value=self._fmt_dt(self.start_time), inline=False)
        e.add_field(name="Fin", value=self._fmt_dt(self.end_time), inline=False)
        if self.location:
            e.add_field(name="Lieu", value=self.location, inline=False)
        e.add_field(name="Donjon", value=self.dungeon_name, inline=False)
        if self.max_slots is not None:
            e.add_field(name="Places dispo", value=str(self.max_slots), inline=False)
        return e

    def to_announce_embed(self) -> discord.Embed:
        e = discord.Embed(title=f"📣 {self.name}", description=self.description, colour=EMBED_COLOR_ANNOUNCE)
        e.add_field(
            name="Quand",
            value=f"{self._fmt_dt(self.start_time)} • <t:{int(self.start_time.timestamp())}:R>",
            inline=False,
        )
        e.add_field(name="Fin", value=self._fmt_dt(self.end_time), inline=False)
        if self.location:
            e.add_field(name="Lieu", value=self.location, inline=False)
        e.add_field(name="Donjon", value=self.dungeon_name, inline=False)
        if self.max_slots is not None:
            e.add_field(name="Places dispo", value=str(self.max_slots), inline=False)
        e.set_footer(text="Clique sur un des boutons pour t’inscrire ⤵️")
        return e


# --------------------------------------------------------------------------- #
# UI : Confirm / RSVP                                                         #
# --------------------------------------------------------------------------- #

class ConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, *, guild, bot) -> None:
        super().__init__(timeout=DM_TIMEOUT)
        self.value: Optional[bool] = None
        self.owner_id = owner_id
        self.guild = guild
        self.bot = bot

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        source_guild_id = getattr(itx, "guild_id", None)
        if source_guild_id is not None and source_guild_id != self.guild.id:
            await itx.response.send_message("Cette confirmation appartient à un autre serveur.", ephemeral=True)
            return False
        if itx.user.id != self.owner_id:
            await itx.response.send_message("Cette confirmation appartient à un autre membre.", ephemeral=True)
            return False
        try:
            require_guild(self.guild, self.bot)
            member = await self.guild.fetch_member(self.owner_id)
            roles = await self.guild.fetch_roles()
            role_ids, _, _ = EventConversationCog._member_state(self.guild, member, roles)
            if not any(role.id in role_ids and role.name == STAFF_ROLE_NAME for role in roles):
                raise EventSecurityError("Le rôle Staff actuel est requis.")
        except (commands.CheckFailure, EventSecurityError, discord.HTTPException):
            await itx.response.send_message("Vos droits sur Evolution ne permettent plus cette confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Valider ✅", style=discord.ButtonStyle.success)
    async def _confirm(self, itx: discord.Interaction, _: discord.ui.Button):
        if not await self.interaction_check(itx):
            return
        self.value = True
        await itx.response.defer()
        self.stop()

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.danger)
    async def _cancel(self, itx: discord.Interaction, _: discord.ui.Button):
        if not await self.interaction_check(itx):
            return
        self.value = False
        await itx.response.defer()
        self.stop()


class RSVPView(discord.ui.View):
    def __init__(self, role: discord.Role, max_slots: Optional[int], *, parent_cog, store_data):
        super().__init__(timeout=None)
        self.role = role
        self.max_slots = max_slots
        self.parent = parent_cog
        self.store_data = store_data
        self._going: set[int] = set(store_data.get("going", []))

    @discord.ui.button(label="Je participe ✅", style=discord.ButtonStyle.success, custom_id="rsvp_yes")
    async def rsvp_yes(self, itx: discord.Interaction, button: discord.ui.Button):
        try:
            self._going = await self.parent.change_rsvp(itx, self.store_data, join=True)
        except (EventSecurityError, commands.CheckFailure) as exc:
            return await itx.response.send_message(str(exc), ephemeral=True)
        except discord.Forbidden:
            return await itx.response.send_message("Je n'ai pas la permission d'ajouter le rôle.", ephemeral=True)
        await itx.response.send_message("✅ Inscription enregistrée !", ephemeral=True)

    @discord.ui.button(label="Me désinscrire ❌", style=discord.ButtonStyle.danger, custom_id="rsvp_no")
    async def rsvp_no(self, itx: discord.Interaction, button: discord.ui.Button):
        try:
            self._going = await self.parent.change_rsvp(itx, self.store_data, join=False)
        except (EventSecurityError, commands.CheckFailure) as exc:
            return await itx.response.send_message(str(exc), ephemeral=True)
        except discord.Forbidden:
            return await itx.response.send_message("Je n'ai pas la permission de retirer le rôle.", ephemeral=True)
        await itx.response.send_message("Désinscription effectuée.", ephemeral=True)


# --------------------------------------------------------------------------- #
# Cog principal                                                               #
# --------------------------------------------------------------------------- #

class SecurityReviewView(discord.ui.View):
    """A concrete, short-lived human approval, never an approval from an AI."""

    def __init__(self, parent, interaction, record, *, cleanup=False):
        super().__init__(timeout=300)
        self.parent = parent
        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.channel_id = interaction.channel_id
        self.event_id = str(record["event_id"])
        self.fingerprint = parent.record_fingerprint(record)
        self.cleanup = cleanup
        self._used = False
        self._expires_at = time.monotonic() + 300
        if cleanup:
            self.children[0].label = "Supprimer ces ressources"

    async def interaction_check(self, interaction):
        if self._used or time.monotonic() >= self._expires_at:
            await interaction.response.send_message("Cette confirmation a expiré ou a déjà été utilisée.", ephemeral=True)
            return False
        if (interaction.user.id != self.user_id or interaction.guild_id != self.guild_id
                or interaction.channel_id != self.channel_id):
            await interaction.response.send_message("Cette confirmation n'appartient pas à ce contexte.", ephemeral=True)
            return False
        require_guild(interaction.guild_id, self.parent.bot)
        return True

    @discord.ui.button(label="Revalider cet événement", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Also check explicitly: callbacks invoked internally do not run View checks.
        if not await self.interaction_check(interaction):
            return
        self._used = True
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.parent.confirm_security_review(
                interaction, self.event_id, self.fingerprint, cleanup=self.cleanup,
            )
        except (EventSecurityError, commands.CheckFailure, discord.HTTPException) as exc:
            result = f"Action refusée : {exc}"
        except Exception:
            self.parent.log.exception("Event security review failed event=%s", self.event_id)
            result = "La sauvegarde a échoué. Les actions de cet événement restent suspendues."
        await interaction.edit_original_response(content=result, view=self, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):
            return
        self._used = True
        self.stop()
        await interaction.response.edit_message(content="Action annulée.", view=None)


class EventConversationCog(commands.Cog):
    event_security = app_commands.Group(
        name="event-securite", description="Staff : contrôler les ressources des événements.",
        guild_only=True, default_permissions=discord.Permissions(manage_roles=True, manage_channels=True),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
    )

    def __init__(self, bot: commands.Bot, *, announce_channel_name="organisation"):
        self.bot = bot
        self.announce_channel_name = announce_channel_name
        self.store = EventStore(bot)
        self.console: Optional[ConsoleStore] = None
        self._conversations: Dict[int, List[str]] = {}
        self._records: dict[str, dict] = {}
        self._resource_locks: dict[str, asyncio.Lock] = {}
        self._category_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.log = logging.getLogger(__name__).getChild("EventConversation")

    def _task(self, operation):
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        def finished(done):
            self._tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.log.error("Event background task failed type=%s", type(done.exception()).__name__)
        task.add_done_callback(finished)
        return task

    async def cog_load(self):
        self.console = ConsoleStore(self.bot, channel_name=os.getenv("CHANNEL_CONSOLE", "console"))
        await self.store.connect()
        # setup_hook runs before the guild cache is populated. Never infer a
        # foreign guild or block login waiting synchronously for readiness.
        self._task(self._restore_after_ready())

    async def _restore_after_ready(self):
        await self.bot.wait_until_ready()
        data = await self.store.load()
        for event_id, event in data.get("events", {}).items():
            try:
                record = self._event_data_to_store_data(event_id, event)
            except (ValueError, TypeError, AttributeError):
                self.log.warning("Invalid stored event retained without actions id=%s", event_id)
                continue
            if record and is_allowed_guild(record.get("guild_id"), self.bot):
                self._records[str(record["event_id"])] = record
        if self.console is not None:
            for record in (await self.console.load_all()).values():
                self._records.setdefault(str(record["event_id"]), record)
        for record in list(self._records.values()):
            await self._restore_view(record)

    async def cog_unload(self):
        cancel_dm_flows(self.bot, "event")
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.close()

    @commands.command(name="event")
    @commands.has_role(STAFF_ROLE_NAME)
    @dm_workflow("event", timeout=DM_TIMEOUT)
    async def cmd_event(self, ctx: commands.Context):
        require_guild(ctx.guild, self.bot)
        try:
            provenance_key()
        except EventSecurityError as exc:
            return await ctx.send(str(exc))
        await self._current_staff(ctx.guild, ctx.author.id)
        try:
            await delete_invocation_message(ctx, delay=0)
        except discord.HTTPException:
            pass
        dm = await ctx.author.create_dm()
        await dm.send(
            "Décris ton événement en plusieurs messages, puis tape `terminé`. "
            "Tape `annuler` pour abandonner. Délai d'inactivité : 15 minutes."
        )
        await notify_private_workflow(ctx)
        transcript = []
        self._conversations[ctx.author.id] = transcript
        check = dm_check_for(self.bot)
        try:
            while True:
                try:
                    message = await self.bot.wait_for("message", timeout=DM_TIMEOUT, check=check)
                except asyncio.TimeoutError:
                    return await dm.send("Temps écoulé, conversation annulée.")
                text = (message.content or "").strip()
                if text.casefold() in {"annuler", "cancel", "stop"}:
                    return await dm.send("Événement annulé.")
                if text.casefold() == "terminé":
                    break
                transcript.append(text)
            finish_dm_input(self.bot)
            ia_cog = self.bot.get_cog("IACog")
            if ia_cog is None:
                return await dm.send("Le module IA n'est pas disponible.")
            try:
                response, _ = await ia_cog.generate_content_with_fallback_async(
                    f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n" + "\n".join(transcript)
                )
                draft = EventDraft.from_json(json.loads(self._extract_json(
                    response.text if hasattr(response, "text") else str(response)
                )))
                now = discord.utils.utcnow()
                if draft.start_time <= now + MIN_DELTA:
                    alternate = parse_french_datetime(" ".join(transcript))
                    if alternate and alternate > now + MIN_DELTA:
                        duration = draft.end_time - draft.start_time
                        draft.start_time, draft.end_time = alternate, alternate + duration
            except Exception:
                self.log.exception("Event draft generation failed")
                return await dm.send("Impossible d'analyser la proposition. Aucun événement n'a été créé.")
            view = ConfirmView(ctx.author.id, guild=ctx.guild, bot=self.bot)
            preview = await dm.send(embed=draft.to_preview_embed(), view=view)
            await view.wait()
            await preview.edit(view=None)
            if view.value is not True:
                return await dm.send("Événement annulé.")
            if draft.start_time < discord.utils.utcnow() + MIN_DELTA or draft.end_time <= draft.start_time:
                return await dm.send("Dates invalides : le début doit être dans au moins cinq minutes et précéder la fin.")
            await self._current_staff(ctx.guild, ctx.author.id)
            try:
                await self._publish_event(ctx, draft)
            except Exception:
                self.log.exception("Event creation failed")
                return await dm.send("Impossible de créer et sauvegarder l'événement. Ses actions n'ont pas été activées.")
            await dm.send("Événement créé, sauvegardé et annoncé.")
        finally:
            # Draft MP contents are deliberately never persisted to #console.
            self._conversations.pop(ctx.author.id, None)

    async def _publish_event(self, ctx, draft):
        guild = ctx.guild
        require_guild(guild, self.bot)
        provenance_key()
        event_uuid = str(uuid.uuid4())
        created = []
        persisted = False
        try:
            role = await self._create_event_role(guild, draft, event_uuid=event_uuid)
            created.append(role)
            private = await self._create_event_channel(guild, draft, role, event_uuid=event_uuid)
            created.append(private)
            scheduled = await guild.create_scheduled_event(
                name=draft.name, description=draft.description, start_time=draft.start_time,
                end_time=draft.end_time, entity_type=discord.EntityType.external,
                location=draft.location or private.jump_url, privacy_level=discord.PrivacyLevel.guild_only,
            )
            created.append(scheduled)
            announcement = resolve_text_channel(
                guild, id_env="ORGANISATION_CHANNEL_ID", name_env="ORGANISATION_CHANNEL_NAME",
                default_name=self.announce_channel_name,
            )
            if announcement is None:
                raise EventSecurityError("Le salon d'annonce des événements est introuvable.")
            require_guild(announcement.guild, self.bot)
            record = {
                "event_id": scheduled.id, "event_uuid": event_uuid,
                "message_id": None, "channel_id": announcement.id, "role_id": role.id,
                "event_channel_id": private.id, "category_id": private.category_id,
                "max_slots": draft.max_slots, "dungeon": draft.dungeon_name, "going": [],
                "ends_at": draft.end_time.isoformat(), "guild_id": guild.id,
                "title": draft.name, "description": draft.description,
                "starts_at": draft.start_time.isoformat(), "author_id": ctx.author.id,
                "timezone": str(LOCAL_TZ), "location": draft.location, "cleanup_enabled": True,
            }
            view = RSVPView(role, draft.max_slots, parent_cog=self, store_data=record)
            # A click before persistence is harmless: the unsigned record fails
            # authorise_rsvp. The signature is installed only after commit.
            message = await announcement.send(embed=draft.to_announce_embed(), view=view)
            created.append(message)
            record["message_id"] = message.id
            signed = sign_event(record)
            await self._persist_store_data(signed)
            record.update(signed)
            persisted = True
            self._task(self._schedule_cleanup(role, private, draft.end_time, scheduled.id))
            return record
        finally:
            if not persisted:
                # Every item in this list was created by this exact attempt.
                for resource in reversed(created):
                    try:
                        await resource.delete()
                    except discord.HTTPException:
                        self.log.warning("Event creation rollback failed resource=%s", getattr(resource, "id", None))

    async def _create_event_role(self, guild, draft, *, event_uuid):
        require_guild(guild, self.bot)
        name = f"Event {event_uuid[:8]} — {draft.name}"[:100]
        return await guild.create_role(name=name, permissions=discord.Permissions.none(), mentionable=True,
                                       reason="Rôle temporaire propre à un événement")

    async def _event_category(self, guild):
        async with self._category_lock:
            categories = [category for category in guild.categories if category.name == EVENT_CAT_NAME]
            if len(categories) > 1:
                raise EventSecurityError("Plusieurs catégories événements homonymes : intervention Staff nécessaire.")
            if categories:
                return categories[0]
            return await guild.create_category(EVENT_CAT_NAME, reason="Catégorie événements")

    async def _create_event_channel(self, guild, draft, role, *, event_uuid):
        require_guild(guild, self.bot)
        category = await self._event_category(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
            )
        staff = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        if staff is not None:
            overwrites[staff] = discord.PermissionOverwrite(view_channel=True)
        return await guild.create_text_channel(
            f"event-{slugify(draft.name)[:75]}-{event_uuid[:8]}", category=category, overwrites=overwrites,
            topic=resource_marker({"event_uuid": event_uuid}),
        )

    async def _current_staff(self, guild, user_id):
        require_guild(guild, self.bot)
        member = await guild.fetch_member(user_id)
        roles = await guild.fetch_roles()
        role_ids, _, _ = self._member_state(guild, member, roles)
        if not any(role.id in role_ids and role.name == STAFF_ROLE_NAME for role in roles):
            raise EventSecurityError("Le rôle Staff actuel est requis.")
        return member, roles

    @staticmethod
    def _member_state(guild, member, roles):
        """Use role definitions fetched now, not Member.roles' guild cache."""
        raw_ids = getattr(member, "_roles", None)
        ids = {int(role_id) for role_id in raw_ids} if raw_ids is not None else {role.id for role in member.roles}
        ids.add(guild.id)
        held = [role for role in roles if role.id in ids]
        bits = 0
        for role in held:
            bits |= role.permissions.value
        permissions = discord.Permissions(bits)
        if guild.owner_id == member.id or permissions.administrator:
            permissions = discord.Permissions.all()
        timed_out = getattr(member, "is_timed_out", None)
        if callable(timed_out) and timed_out():
            permissions = discord.Permissions.none()
        return ids, permissions, max((role.position for role in held), default=0)

    @staticmethod
    def _channel_permissions(channel, member, roles):
        # The SDK normally resolves permissions via guild._roles, even for a
        # freshly fetched Member. Build an isolated cache snapshot so that a
        # role permission change cannot be hidden by stale gateway state.
        if isinstance(member, discord.Member) and isinstance(channel, discord.abc.GuildChannel):
            guild_snapshot = copy.copy(channel.guild)
            guild_snapshot._roles = {role.id: role for role in roles}
            channel_snapshot = copy.copy(channel)
            channel_snapshot.guild = guild_snapshot
            member_snapshot = copy.copy(member)
            member_snapshot.guild = guild_snapshot
            return channel_snapshot.permissions_for(member_snapshot)
        return channel.permissions_for(member)

    async def _fetch_channel(self, channel_id, *, missing_ok=False):
        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            if missing_ok:
                return None
            raise EventSecurityError("Une ressource de l'événement a disparu.") from None
        require_guild(getattr(channel, "guild", None), self.bot)
        return channel

    async def _resources(self, record, *, verified=True, missing_ok=False):
        require_guild(record.get("guild_id"), self.bot)
        if verified:
            verify_event(record)
        announcement = await self._fetch_channel(record["channel_id"], missing_ok=missing_ok)
        guild = next((guild for guild in allowed_guilds(self.bot) if guild.id == int(record["guild_id"])), None)
        if guild is None:
            raise EventSecurityError("Le serveur de l'événement est indisponible.")
        if announcement is not None:
            try:
                message = await announcement.fetch_message(int(record["message_id"]))
            except discord.NotFound:
                if not missing_ok:
                    raise EventSecurityError("Le message d'annonce a disparu.") from None
            else:
                if (message.author.id != self.bot.user.id or getattr(message, "webhook_id", None) is not None):
                    raise EventSecurityError("Le message d'annonce n'appartient pas au bot.")
        private = await self._fetch_channel(record["event_channel_id"], missing_ok=missing_ok)
        roles = await guild.fetch_roles()
        role = next((role for role in roles if role.id == int(record["role_id"])), None)
        if role is None and not missing_ok:
            raise EventSecurityError("Le rôle de l'événement a disparu.")
        bot_member = await guild.fetch_member(self.bot.user.id)
        _, bot_permissions, bot_top_position = self._member_state(guild, bot_member, roles)
        if role is not None:
            require_guild(getattr(role, "guild", None), self.bot)
            if (role.id == guild.id or role.managed or role.permissions.value != 0
                    or role.position >= bot_top_position
                    or not bot_permissions.manage_roles):
                raise EventSecurityError("Le rôle de cet événement n'est pas un rôle temporaire sûr.")
        if private is not None:
            category = getattr(private, "category", None)
            if category is None or category.name != EVENT_CAT_NAME:
                raise EventSecurityError("Le salon n'est pas dans la catégorie événements.")
            if verified and (private.category_id != int(record["category_id"])
                             or resource_marker(record) not in (private.topic or "").splitlines()):
                raise EventSecurityError("La provenance du salon temporaire est invalide.")
        return guild, role, private, bot_member, roles

    async def authorise_rsvp(self, interaction, record):
        require_guild(interaction.guild_id, self.bot)
        if int(record.get("guild_id", 0)) != interaction.guild_id:
            raise EventSecurityError("Cet événement appartient à un autre serveur.")
        current = self._records.get(str(record.get("event_id")))
        if current is None or self.record_fingerprint(current) != self.record_fingerprint(record):
            raise EventSecurityError("Cet événement n'est pas activé ou a été modifié.")
        if utc_datetime(record.get("ends_at")) <= discord.utils.utcnow():
            raise EventSecurityError("Cet événement est terminé.")
        guild, role, _, _, _ = await self._resources(record)
        member = await guild.fetch_member(interaction.user.id)
        return role, member

    async def change_rsvp(self, interaction, record, *, join):
        event_id = str(record.get("event_id"))
        async with self._resource_locks.setdefault(event_id, asyncio.Lock()):
            role, member = await self.authorise_rsvp(interaction, record)
            current = copy.deepcopy(self._records[event_id])
            going = set(current.get("going") or [])
            limit = current.get("max_slots")
            if join and limit and len(going) >= limit and member.id not in going:
                raise EventSecurityError("Désolé, il n'y a plus de place.")
            had_role = any(held.id == role.id for held in member.roles)
            if join:
                await member.add_roles(role, reason="Inscription événement authentifié")
                going.add(member.id)
            else:
                await member.remove_roles(role, reason="Désinscription événement authentifié")
                going.discard(member.id)
            current["going"] = sorted(going)
            try:
                await self._persist_store_data(current)
            except BaseException:
                # Best-effort compensation, while retaining the last durable
                # record. No success is reported for an unpersisted change.
                try:
                    if join and not had_role:
                        await member.remove_roles(role, reason="Annulation : sauvegarde événement échouée")
                    elif not join and had_role:
                        await member.add_roles(role, reason="Annulation : sauvegarde événement échouée")
                except discord.HTTPException:
                    self.log.error("Event RSVP compensation failed event=%s", event_id)
                raise
            record["going"] = current["going"]
            return going

    async def _schedule_cleanup(self, role, channel, end_time, event_id):
        try:
            delay = max(0, (utc_datetime(end_time) - discord.utils.utcnow()).total_seconds())
            if delay:
                await asyncio.sleep(delay)
            await self._cleanup_event(str(event_id))
        except (EventSecurityError, commands.CheckFailure, discord.HTTPException) as exc:
            self.log.warning("Event cleanup suspended event=%s reason=%s", event_id, type(exc).__name__)

    async def _cleanup_event(self, event_id):
        async with self._resource_locks.setdefault(str(event_id), asyncio.Lock()):
            record = self._records.get(str(event_id))
            if record is None:
                raise EventSecurityError("Événement introuvable.")
            if not record.get("cleanup_enabled") or utc_datetime(record["ends_at"]) > discord.utils.utcnow():
                raise EventSecurityError("Le nettoyage de cet événement n'est pas autorisé.")
            guild, role, private, bot_member, roles = await self._resources(record, missing_ok=True)
            if private is not None:
                if not self._channel_permissions(private, bot_member, roles).manage_channels:
                    raise EventSecurityError("Le bot ne peut pas gérer ce salon.")
                await private.delete(reason="Fin de l'événement authentifié")
            if role is not None:
                await role.delete(reason="Fin de l'événement authentifié")
            await self.store.delete_event(str(event_id))
            self._records.pop(str(event_id), None)
            if self.console is not None:
                await self.console.delete(int(event_id))

    def _event_data_to_store_data(self, event_id, event):
        return {
            "event_id": int(event.scheduled_event_id or event_id),
            "message_id": event.announce_message_id, "channel_id": event.channel_id,
            "role_id": event.temp_role_id, "event_channel_id": event.event_channel_id,
            "max_slots": event.max_participants, "dungeon": event.dungeon_name,
            "going": event.going or [], "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            "guild_id": event.guild_id, "title": event.title, "description": event.description,
            "starts_at": event.starts_at.isoformat(), "author_id": event.author_id,
            "timezone": event.timezone, "location": event.location, "event_uuid": event.event_uuid,
            "category_id": event.event_category_id, "provenance": event.provenance,
            "cleanup_enabled": event.cleanup_enabled,
        }

    def _store_data_to_event(self, record):
        def parse(value):
            return value if value is None or isinstance(value, datetime) else datetime.fromisoformat(value)
        return EventData(
            guild_id=record["guild_id"], channel_id=record["channel_id"],
            title=record.get("title") or "Événement", description=record.get("description") or "",
            starts_at=parse(record.get("starts_at")) or discord.utils.utcnow(), ends_at=parse(record.get("ends_at")),
            max_participants=record.get("max_slots"), timezone=record.get("timezone"),
            temp_role_id=record.get("role_id"), author_id=record.get("author_id"), location=record.get("location"),
            dungeon_name=record.get("dungeon"), event_channel_id=record.get("event_channel_id"),
            announce_message_id=record.get("message_id"), scheduled_event_id=record.get("event_id"),
            going=record.get("going"), event_uuid=record.get("event_uuid"),
            event_category_id=record.get("category_id"), provenance=record.get("provenance"),
            cleanup_enabled=record.get("cleanup_enabled", False),
        )

    async def _persist_store_data(self, record):
        require_guild(record.get("guild_id"), self.bot)
        event = self._store_data_to_event(record)
        await self.store.save_event(str(record["event_id"]), event)
        self._records[str(record["event_id"])] = copy.deepcopy({key: value for key, value in record.items() if not key.startswith("_")})

    async def _restore_view(self, record):
        try:
            require_guild(record.get("guild_id"), self.bot)
            self._records.setdefault(str(record["event_id"]), record)
            _, role, private, _, _ = await self._resources(record)
        except (EventSecurityError, commands.CheckFailure, discord.HTTPException, KeyError, TypeError, ValueError):
            self.log.info("Event retained read-only event=%s", record.get("event_id"))
            return False
        if utc_datetime(record["ends_at"]) > discord.utils.utcnow():
            self.bot.add_view(RSVPView(role, record.get("max_slots"), parent_cog=self, store_data=record), message_id=int(record["message_id"]))
        if record.get("cleanup_enabled"):
            self._task(self._schedule_cleanup(role, private, utc_datetime(record["ends_at"]), record["event_id"]))
        return True

    @staticmethod
    def record_fingerprint(record):
        keys = ("guild_id", "event_id", "channel_id", "message_id", "role_id", "event_channel_id", "category_id",
                "event_uuid", "ends_at", "cleanup_enabled", "provenance")
        encoded = json.dumps({key: record.get(key) for key in keys}, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def _review_resources(self, interaction, record):
        provenance_key()
        require_guild(interaction.guild_id, self.bot)
        actor, _ = await self._current_staff(interaction.guild, interaction.user.id)
        guild, role, private, bot_member, roles = await self._resources(record, verified=False)
        if role is None or private is None:
            raise EventSecurityError("Les ressources doivent exister pour être revalidées.")
        role_ids, permissions, actor_top_position = self._member_state(guild, actor, roles)
        if not any(held.id in role_ids and held.name == STAFF_ROLE_NAME for held in roles):
            raise EventSecurityError("Le rôle Staff actuel est requis.")
        if (not permissions.manage_roles or not self._channel_permissions(private, actor, roles).manage_channels
                or not self._channel_permissions(private, bot_member, roles).manage_channels):
            raise EventSecurityError("La gestion des rôles et de ce salon est requise.")
        if guild.owner_id != actor.id and (role.position >= actor_top_position):
            raise EventSecurityError("Le rôle est hors de votre hiérarchie.")
        return role, private

    async def _present_security_review(self, interaction, event_id, *, cleanup=False):
        try:
            require_guild(interaction.guild_id, self.bot)
            record = self._records.get(str(event_id))
            if record is None:
                raise EventSecurityError("Événement introuvable dans les sauvegardes admises.")
            await self._review_resources(interaction, record)
            if cleanup:
                verify_event(record)
                if utc_datetime(record["ends_at"]) > discord.utils.utcnow():
                    raise EventSecurityError("Cet événement n'est pas terminé.")
            label = "SUPPRESSION de ces ressources" if cleanup else "Revalidation de ces ressources (sans suppression)"
            text = (f"{label}\nÉvénement {record['event_id']} — serveur {record['guild_id']}\n"
                    f"Rôle {record['role_id']} — salon temporaire {record['event_channel_id']}\n"
                    f"Annonce {record['channel_id']}/{record['message_id']} — échéance {record.get('ends_at')}\n"
                    "Cette confirmation expire dans cinq minutes.")
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                                                     view=SecurityReviewView(self, interaction, record, cleanup=cleanup))
        except (EventSecurityError, commands.CheckFailure, discord.HTTPException, ValueError, KeyError) as exc:
            if interaction.response.is_done():
                await interaction.followup.send(f"Action refusée : {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Action refusée : {exc}", ephemeral=True)

    @event_security.command(name="revalider", description="Réactiver un événement après vérification de ses ressources.")
    @app_commands.default_permissions(manage_roles=True, manage_channels=True)
    async def revalidate_event(self, interaction: discord.Interaction, evenement: str):
        await self._present_security_review(interaction, evenement)

    @event_security.command(name="nettoyer", description="Confirmer séparément le nettoyage d'un événement terminé.")
    @app_commands.default_permissions(manage_roles=True, manage_channels=True)
    async def cleanup_event(self, interaction: discord.Interaction, evenement: str):
        await self._present_security_review(interaction, evenement, cleanup=True)

    async def confirm_security_review(self, interaction, event_id, fingerprint, *, cleanup=False):
        event_id = str(event_id)
        async with self._resource_locks.setdefault(event_id, asyncio.Lock()):
            current = self._records.get(event_id)
            if current is None or self.record_fingerprint(current) != fingerprint:
                raise EventSecurityError("L'événement a changé. Rouvrez son aperçu.")
            _, private = await self._review_resources(interaction, current)
            record = copy.deepcopy({key: value for key, value in current.items() if not key.startswith("_")})
            if cleanup:
                verify_event(record)
                if utc_datetime(record["ends_at"]) > discord.utils.utcnow():
                    raise EventSecurityError("Cet événement n'est pas terminé.")
                record["cleanup_enabled"] = True
            else:
                ends_at = datetime.fromisoformat(record["ends_at"]) if isinstance(record["ends_at"], str) else record["ends_at"]
                if ends_at.tzinfo is None:
                    try:
                        ends_at = ends_at.replace(tzinfo=ZoneInfo(record.get("timezone") or str(LOCAL_TZ)))
                    except (ValueError, KeyError):
                        raise EventSecurityError("Le fuseau de cet ancien événement est invalide.") from None
                record["ends_at"] = ends_at.astimezone(timezone.utc).isoformat()
                record["event_uuid"] = str(uuid.uuid4())
                record["category_id"] = private.category_id
                record["cleanup_enabled"] = utc_datetime(record["ends_at"]) > discord.utils.utcnow()
                # Only an explicit, authorized human review adopts a legacy
                # resource. Failure to persist leaves it without a valid proof.
                old_topic = private.topic or ""
                marker = resource_marker(record)
                new_topic = old_topic + ("\n" if old_topic else "") + marker
                if len(new_topic) > 1024:
                    raise EventSecurityError("Le sujet du salon ne permet pas d'ajouter la provenance.")
                await private.edit(topic=new_topic, reason="Revalidation explicite Staff de l'événement")
            signed = sign_event(record)
            await self._persist_store_data(signed)
        if cleanup:
            await self._cleanup_event(event_id)
            return "Les ressources explicitement confirmées ont été supprimées."
        await self._restore_view(signed)
        if signed["cleanup_enabled"]:
            return "Événement revalidé. Ses actions sont réactivées."
        return "Événement terminé revalidé sans suppression. Utilisez /event-securite nettoyer pour un nettoyage distinct."

    @staticmethod
    def _extract_json(text):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON introuvable dans la réponse IA.")
        return text[start:end + 1]


async def setup(bot: commands.Bot):
    await bot.add_cog(EventConversationCog(bot))
