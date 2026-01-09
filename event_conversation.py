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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import dateparser
import discord


def _ensure_utils() -> None:
    if not hasattr(discord.utils, "evaluate_annotation"):
        discord.utils.evaluate_annotation = lambda *a, **k: None
    if not hasattr(discord.utils, "is_inside_class"):
        discord.utils.is_inside_class = lambda obj: False


_ensure_utils()
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

from models import EventData
from utils.console_store import ConsoleStore
from utils.storage import EventStore
from utils.datetime_utils import parse_french_datetime
from utils.channel_resolver import resolve_text_channel

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
    def __init__(self) -> None:
        super().__init__(timeout=DM_TIMEOUT)
        self.value: Optional[bool] = None

    @discord.ui.button(label="Valider ✅", style=discord.ButtonStyle.success)
    async def _confirm(self, itx: discord.Interaction, _: discord.ui.Button):
        self.value = True
        await itx.response.defer()
        self.stop()

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.danger)
    async def _cancel(self, itx: discord.Interaction, _: discord.ui.Button):
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
        if (
            self.max_slots
            and len(self._going) >= self.max_slots
            and itx.user.id not in self._going
        ):
            return await itx.response.send_message("Désolé, il n’y a plus de place !", ephemeral=True)

        try:
            await itx.user.add_roles(self.role, reason="Inscription événement")
        except discord.Forbidden:
            return await itx.response.send_message("Je n'ai pas la permission d'ajouter le rôle.", ephemeral=True)

        self._going.add(itx.user.id)
        self.store_data["going"] = list(self._going)
        if self.parent.console:
            await self.parent.console.upsert(self.store_data)
        await self.parent._persist_store_data(self.store_data)

        await itx.response.send_message("✅ Inscription enregistrée !", ephemeral=True)

    @discord.ui.button(label="Me désinscrire ❌", style=discord.ButtonStyle.danger, custom_id="rsvp_no")
    async def rsvp_no(self, itx: discord.Interaction, button: discord.ui.Button):
        try:
            await itx.user.remove_roles(self.role, reason="Désinscription événement")
        except discord.Forbidden:
            pass

        self._going.discard(itx.user.id)
        self.store_data["going"] = list(self._going)
        if self.parent.console:
            await self.parent.console.upsert(self.store_data)
        await self.parent._persist_store_data(self.store_data)

        await itx.response.send_message("Désinscription effectuée.", ephemeral=True)


# --------------------------------------------------------------------------- #
# Cog principal                                                               #
# --------------------------------------------------------------------------- #

class EventConversationCog(commands.Cog):
    def __new__(cls, *args, **kwargs):
        _ensure_utils()
        return super().__new__(cls)

    def __init__(
        self,
        bot: commands.Bot,
        *,
        announce_channel_name: str = "organisation",
    ):
        self.bot = bot
        self.announce_channel_name = announce_channel_name
        self.store = EventStore(bot)
        self.console: Optional[ConsoleStore] = None
        self._conversations: Dict[int, List[str]] = {}
        self.log = logging.getLogger(__name__).getChild("EventConversation")

    # ---------- lifecycle ------------------------------------------------- #
    async def cog_load(self) -> None:
        self.console = ConsoleStore(self.bot, channel_name=os.getenv("CHANNEL_CONSOLE", "console"))
        await self.store.connect()
        data = await self.store.load()
        self._conversations = {int(k): v for k, v in data.get("conversations", {}).items()}

        restored = False
        for event_id, event_data in data.get("events", {}).items():
            rec = self._event_data_to_store_data(event_id, event_data)
            if rec:
                await self._restore_view(rec)
                restored = True

        if restored:
            return

        chan = await self.console._channel()
        if chan is None:
            return

        records = await self.console.load_all()
        for rec in records.values():
            await self._restore_view(rec)

    # --------------------------------------------------------------------- #
    # Commande !event                                                       #
    # --------------------------------------------------------------------- #
    @commands.command(name="event")
    @commands.has_role(STAFF_ROLE_NAME)
    async def cmd_event(self, ctx: commands.Context) -> None:
        """Workflow DM pour créer un événement."""
        if ctx.guild is None:
            return await ctx.reply("Cette commande doit être utilisée dans un serveur.")

        try:
            await ctx.message.delete(delay=0)
        except discord.HTTPException:
            pass

        dm = await ctx.author.create_dm()
        await dm.send(
            "Décris ton événement en **plusieurs messages** puis tape `terminé`.\n"
            "*(15 min d’inactivité ⇒ annulation)*"
        )

        # ----- collecte DM -----
        transcript: List[str] = []
        self._conversations[ctx.author.id] = transcript
        await self.store.save_conversation(str(ctx.author.id), transcript)

        try:
            def check(m: discord.Message) -> bool:
                return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

            while True:
                try:
                    msg = await self.bot.wait_for(
                        "message", timeout=DM_TIMEOUT, check=check
                    )
                except asyncio.TimeoutError:
                    await dm.send("⏱️ Temps écoulé, conversation annulée.")
                    self._conversations.pop(ctx.author.id, None)
                    return

                if msg.content.lower().startswith("terminé"):
                    break
                transcript.append(msg.content.strip())
                await self.store.save_conversation(str(ctx.author.id), transcript)

            # ----- appel IA -----
            ia_cog = self.bot.get_cog("IACog")
            if ia_cog is None:
                return await dm.send("❌ Le module IA n’est pas disponible.")

            prompt = f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n" + "\n".join(transcript)
            try:
                resp, _ = await ia_cog.generate_content_with_fallback_async(prompt)
                raw_json = self._extract_json(
                    resp.text if hasattr(resp, "text") else str(resp)
                )
                ai_payload = json.loads(raw_json)
                draft = EventDraft.from_json(ai_payload)

                # -------- Fallback date si IA renvoie un passé / trop proche --------
                now = discord.utils.utcnow()
                if draft.start_time <= now + MIN_DELTA:
                    alt = parse_french_datetime(" ".join(transcript))
                    if alt and alt > now + MIN_DELTA:
                        delta = draft.end_time - draft.start_time
                        draft.start_time = alt
                        draft.end_time = alt + delta
            except Exception as exc:
                self.log.exception("Erreur IA/parsing : %s", exc)
                return await dm.send(
                    f"Impossible d’analyser la réponse IA :\n```\n{exc}\n```")

            # ----- preview -----
            view_confirm = ConfirmView()
            preview_msg = await dm.send(
                embed=draft.to_preview_embed(), view=view_confirm
            )
            await view_confirm.wait()
            await preview_msg.edit(view=None)

            if view_confirm.value is not True:
                await dm.send("Événement annulé. 👍")
                return

            # ----- vérifs -----
            now = discord.utils.utcnow()
            if draft.start_time < now + MIN_DELTA:
                return await dm.send(
                    "⚠️ La date de début doit être au moins 5 minutes dans le futur."
                )
            if draft.end_time <= draft.start_time:
                return await dm.send("⚠️ L’heure de fin doit être après l’heure de début.")

            guild: discord.Guild = ctx.guild  # type: ignore[assignment]

            role = None
            private_channel = None
            scheduled_event = None
            announce_channel = None
            cleanup_needed = False
            try:
                # --- création rôle + salon privés ---------------------------- #
                role = await self._create_event_role(guild, draft)
                private_channel = await self._create_event_channel(guild, draft, role)

                # --- Guild Scheduled Event ----------------------------------- #
                try:
                    scheduled_event = await guild.create_scheduled_event(
                        name=draft.name,
                        description=draft.description,
                        start_time=draft.start_time,
                        end_time=draft.end_time,
                        entity_type=discord.EntityType.external,
                        location=draft.location or private_channel.jump_url,
                        privacy_level=discord.PrivacyLevel.guild_only,
                    )
                except discord.HTTPException as exc:
                    cleanup_needed = True
                    self.log.error("create_scheduled_event: %s", exc.text)
                    await dm.send(f"❌ Impossible de créer l’événement : {exc.text}")
                    return

                # --- annonce publique --------------------------------------- #
                announce_channel = resolve_text_channel(
                    guild,
                    id_env="ORGANISATION_CHANNEL_ID",
                    name_env="ORGANISATION_CHANNEL_NAME",
                    default_name=self.announce_channel_name,
                )
                if announce_channel is None:
                    cleanup_needed = True
                    await dm.send(
                        f"❌ Canal #{self.announce_channel_name} introuvable."
                    )
                    return
            except Exception as exc:
                cleanup_needed = True
                self.log.exception("Failed to setup event resources: %s", exc)
                await dm.send("❌ Impossible de préparer l’événement.")
                return
            finally:
                if cleanup_needed:
                    self.log.debug("Starting cleanup for event resources.")
                    if scheduled_event is not None:
                        try:
                            await scheduled_event.delete()
                            self.log.debug("Scheduled event deleted during cleanup.")
                        except discord.HTTPException as exc:
                            self.log.error(
                                "Failed to delete scheduled event during cleanup: %s", exc.text
                            )
                    if private_channel is not None:
                        try:
                            await private_channel.delete(
                                reason="Cleanup échec création événement"
                            )
                            self.log.debug("Private channel deleted during cleanup.")
                        except discord.HTTPException as exc:
                            self.log.error(
                                "Failed to delete private channel during cleanup: %s", exc.text
                            )
                    if role is not None:
                        try:
                            await role.delete(
                                reason="Cleanup échec création événement"
                            )
                            self.log.debug("Role deleted during cleanup.")
                        except discord.HTTPException as exc:
                            self.log.error(
                                "Failed to delete role during cleanup: %s", exc.text
                            )
                    self.log.debug("Cleanup phase completed.")

            store_data = {
                "event_id": scheduled_event.id,
                "message_id": None,  # rempli après send
                "channel_id": announce_channel.id,
                "role_id": role.id,
                "event_channel_id": private_channel.id,
                "max_slots": draft.max_slots,
                "dungeon": draft.dungeon_name,
                "going": [],
                "ends_at": draft.end_time.isoformat(),
                "guild_id": guild.id,
                "title": draft.name,
                "description": draft.description,
                "starts_at": draft.start_time.isoformat(),
                "author_id": ctx.author.id,
                "timezone": str(LOCAL_TZ),
                "location": draft.location,
            }
            view_rsvp = RSVPView(
                role, draft.max_slots, parent_cog=self, store_data=store_data
            )

            announce_msg = await announce_channel.send(
                embed=draft.to_announce_embed(), view=view_rsvp
            )
            store_data["message_id"] = announce_msg.id
            if self.console:
                await self.console.upsert(store_data)
            await self._persist_store_data(store_data)

            await dm.send("✅ Événement créé, salon privé ouvert et annoncé !")

            # --- planifie la suppression rôle + salon ------------------------ #
            self.bot.loop.create_task(
                self._schedule_cleanup(
                    role, private_channel, draft.end_time, scheduled_event.id
                )
            )
        finally:
            self._conversations.pop(ctx.author.id, None)
            await self.store.save_conversation(str(ctx.author.id), None)

    # ------------------------------------------------------------------ #
    # Helpers create role / channel                                      #
    # ------------------------------------------------------------------ #
    async def _create_event_role(self, guild: discord.Guild, draft: EventDraft) -> discord.Role:
        role_name = f"Participe à l'event {draft.name}"[:100]
        existing = discord.utils.get(guild.roles, name=role_name)
        if existing:
            return existing
        return await guild.create_role(name=role_name, mentionable=True, reason="Rôle participants event")

    async def _create_event_channel(
        self, guild: discord.Guild, draft: EventDraft, role: discord.Role
    ) -> discord.TextChannel:
        slug = slugify(draft.name)
        category = discord.utils.get(guild.categories, name=EVENT_CAT_NAME)
        if category is None:
            category = await guild.create_category(EVENT_CAT_NAME, reason="Catégorie événements")
        existing = discord.utils.get(category.text_channels, name=f"event-{slug}")
        if existing:
            return existing
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        staff = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(view_channel=True)
        return await guild.create_text_channel(f"event-{slug}", overwrites=overwrites, category=category)

    # ------------------------------------------------------------------ #
    # Cleanup                                                            #
    # ------------------------------------------------------------------ #
    async def _schedule_cleanup(
        self, role: discord.Role, channel: discord.TextChannel, end_time: datetime, event_id: int
    ):
        delay = max(0, (end_time - discord.utils.utcnow()).total_seconds())
        if delay:
            await asyncio.sleep(delay)
        try:
            await channel.delete(reason="Fin de l’événement – suppression salon privé")
        except discord.HTTPException:
            pass
        try:
            await role.delete(reason="Fin de l’événement – suppression rôle temporaire")
        except discord.HTTPException:
            pass
        if self.console:
            await self.console.delete(event_id)

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #
    def _event_data_to_store_data(self, event_id: str | int, event: EventData) -> Optional[dict]:
        try:
            starts_at = event.starts_at.isoformat()
        except AttributeError:
            return None
        try:
            eid_int = int(event_id)
        except (TypeError, ValueError):
            eid_int = event.scheduled_event_id or event_id

        return {
            "event_id": eid_int,
            "message_id": event.announce_message_id,
            "channel_id": event.channel_id,
            "role_id": event.temp_role_id,
            "event_channel_id": event.event_channel_id,
            "max_slots": event.max_participants,
            "dungeon": event.dungeon_name,
            "going": event.going or [],
            "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            "guild_id": event.guild_id,
            "title": event.title,
            "description": event.description,
            "starts_at": starts_at,
            "author_id": event.author_id,
            "timezone": event.timezone,
            "location": event.location,
        }

    def _store_data_to_event(self, store_data: dict) -> EventData:
        def _parse_dt(val: datetime | str | None) -> Optional[datetime]:
            if val is None:
                return None
            return val if isinstance(val, datetime) else datetime.fromisoformat(val)

        return EventData(
            guild_id=store_data.get("guild_id") or 0,
            channel_id=store_data.get("channel_id") or 0,
            title=store_data.get("title") or "Événement",
            description=store_data.get("description") or "",
            starts_at=_parse_dt(store_data.get("starts_at")) or discord.utils.utcnow(),
            ends_at=_parse_dt(store_data.get("ends_at")),
            max_participants=store_data.get("max_slots"),
            timezone=store_data.get("timezone"),
            temp_role_id=store_data.get("role_id"),
            author_id=store_data.get("author_id"),
            location=store_data.get("location"),
            dungeon_name=store_data.get("dungeon"),
            event_channel_id=store_data.get("event_channel_id"),
            announce_message_id=store_data.get("message_id"),
            scheduled_event_id=store_data.get("event_id"),
            going=store_data.get("going"),
        )

    async def _persist_store_data(self, store_data: dict) -> None:
        try:
            event_data = self._store_data_to_event(store_data)
        except Exception:
            self.log.exception("Failed to serialize event %s for persistence", store_data.get("event_id"))
            return
        await self.store.save_event(str(store_data.get("event_id")), event_data)

    # ------------------------------------------------------------------ #
    # Restaurer les views après reboot                                   #
    # ------------------------------------------------------------------ #
    async def _restore_view(self, rec: dict):
        try:
            rec["event_id"] = int(rec.get("event_id"))
        except (TypeError, ValueError):
            pass
        try:
            chan = await self.bot.fetch_channel(rec["channel_id"])
            msg: discord.Message = await chan.fetch_message(rec["message_id"])
        except Exception:
            return
        guild = chan.guild
        role = guild.get_role(rec["role_id"])
        if role is None:
            return
        guild_id = getattr(guild, "id", None)
        if guild_id is not None:
            rec.setdefault("guild_id", guild_id)
        embeds = getattr(msg, "embeds", None) or []
        first_embed = embeds[0] if embeds else None
        rec.setdefault("title", first_embed.title if first_embed else "Événement")
        rec.setdefault(
            "description", first_embed.description if first_embed else ""
        )
        rec.setdefault("starts_at", None)
        author_id = getattr(msg, "author", None)
        if author_id is not None and getattr(author_id, "id", None) is not None:
            rec.setdefault("author_id", author_id.id)
        rec.setdefault("timezone", str(LOCAL_TZ))
        view = RSVPView(role, rec.get("max_slots"), parent_cog=self, store_data=rec)
        view._going.update(rec.get("going", []))
        self.bot.add_view(view, message_id=msg.id)

        ends_iso = rec.get("ends_at")
        channel_id = rec.get("event_channel_id")
        if ends_iso and channel_id:
            ends_at = datetime.fromisoformat(ends_iso)
            chan_priv = guild.get_channel(channel_id)
            now = discord.utils.utcnow()
            if ends_at.tzinfo and now.tzinfo is None:
                now = now.replace(tzinfo=ends_at.tzinfo)
            elif ends_at.tzinfo is None and now.tzinfo:
                ends_at = ends_at.replace(tzinfo=now.tzinfo)
            if chan_priv and role:
                if ends_at <= now:
                    self.bot.loop.create_task(
                        self._schedule_cleanup(role, chan_priv, now, rec["event_id"])
                    )
                elif ends_at > now:
                    self.bot.loop.create_task(
                        self._schedule_cleanup(role, chan_priv, ends_at, rec["event_id"])
                    )

    # ------------------------------------------------------------------ #
    # Misc helpers                                                       #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_json(text: str) -> str:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON introuvable dans la réponse IA.")
        return text[start : end + 1]


# --------------------------------------------------------------------------- #
# Setup                                                                       #
# --------------------------------------------------------------------------- #
async def setup(bot: commands.Bot):
    await bot.add_cog(EventConversationCog(bot))
