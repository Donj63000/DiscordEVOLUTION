# coding: utf-8
"""
Cog : EventConversation
-----------------------
Organise un workflow interactif (DM + IA) permettant aux membres du rôle « Staff »
de créer un événement Discord (Guild Scheduled Event) et de l’annoncer avec
des boutons RSVP.

• `!event` déclenche une DM où l’utilisateur décrit l’événement, puis tape
  `terminé`.
• Le transcript est envoyé à l’IA → JSON (name, description, dates, …).
• Pré‑visualisation dans le DM avec boutons ✅ / ❌.
• Après validation :
    – création du Scheduled Event ;
    – embed d’annonce dans #organisation (ou autre);
    – rôle temporaire « Participants événement » attribué aux inscrits ;
    – rôle supprimé automatiquement à la fin.

Dépendances : discord.py ≥ 2.2, dateparser ≥ 1.2, Python ≥ 3.9
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import dateparser
import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

# --- helpers internes (à adapter ou supprimer si inexistants) -------------- #
from models import EventData               # dataclass / pydantic perso
from utils import parse_fr_datetime    # fallback NLP local
from utils.storage import EventStore       # persistance JSON/DB
from utils.console_store import ConsoleStore   # persistance via #console
# --------------------------------------------------------------------------- #

__all__ = ["setup"]

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Configuration générale                                                      #
# --------------------------------------------------------------------------- #

LOCAL_TZ = ZoneInfo("Europe/Paris")        # Fuseau local du serveur
DM_TIMEOUT = 15 * 60                       # 15 min d’inactivité max
MIN_DELTA = timedelta(minutes=5)           # Discord exige ≥ 5 min dans le futur
MAX_DESC_LEN = 1_000                       # Limitation API Discord
SYSTEM_PROMPT = (
    "Tu es EvolutionBOT et tu aides à créer un événement Discord. "
    "À partir de la conversation suivante, fournis UNIQUEMENT un JSON strict "
    'avec les clés : name, description, start_time, end_time, location, max_slots. '
    "Les dates sont au format JJ/MM/AAAA HH:MM. Mets null si information manquante."
)

EMBED_COLOR_PREVIEW = 0x3498DB
EMBED_COLOR_ANNOUNCE = 0x1ABC9C


# --------------------------------------------------------------------------- #
# Data : brouillon d’événement                                                #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EventDraft:
    """Représentation minimale avant création du Scheduled Event."""

    name: str
    description: str
    start_time: datetime   # toujours UTC & aware
    end_time: datetime     # toujours UTC & aware
    location: Optional[str] = None
    max_slots: Optional[int] = None

    # --------------------- Parsing helpers -------------------------------- #

    @staticmethod
    def _parse_dt(raw: str | datetime | None) -> Optional[datetime]:
        """Convertit *raw* en datetime timezone‑aware UTC ou renvoie None."""
        if raw is None:
            return None

        # 1) déjà un datetime
        if isinstance(raw, datetime):
            dt = raw

        # 2) JJ/MM/AAAA HH:MM très rapide
        else:
            try:
                dt = datetime.strptime(raw, "%d/%m/%Y %H:%M")
            except ValueError:
                # 3) fallback NLP (dateparser FR)
                dt = dateparser.parse(
                    raw,
                    languages=["fr"],
                    settings={
                        "TIMEZONE": str(LOCAL_TZ),
                        "RETURN_AS_TIMEZONE_AWARE": True,
                        "PREFER_DATES_FROM": "future",
                    },
                )
                _log.debug("dateparser «%s» → %s", raw, dt)

        if dt is None:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)

        return dt.astimezone(timezone.utc)

    # --------------------- Construction depuis JSON ----------------------- #

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
            location=(str(obj["location"]) if obj.get("location") else None),
            max_slots=int(obj["max_slots"]) if obj.get("max_slots") is not None else None,
        )

    # --------------------- Embeds utilitaires ----------------------------- #

    def _fmt_dt(self, dt: datetime) -> str:
        return dt.strftime("%d/%m/%Y %H:%M UTC")

    def to_preview_embed(self) -> discord.Embed:
        e = discord.Embed(title=f"📅 {self.name}", description=self.description, colour=EMBED_COLOR_PREVIEW)
        e.add_field(name="Début", value=self._fmt_dt(self.start_time), inline=False)
        e.add_field(name="Fin", value=self._fmt_dt(self.end_time), inline=False)
        if self.location:
            e.add_field(name="Lieu", value=self.location, inline=False)
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
        if self.max_slots is not None:
            e.add_field(name="Places dispo", value=str(self.max_slots), inline=False)
        e.set_footer(text="Clique sur un des boutons pour t’inscrire ⤵️")
        return e


# --------------------------------------------------------------------------- #
# UI Components (Views)                                                       #
# --------------------------------------------------------------------------- #


class ConfirmView(discord.ui.View):
    """Deux boutons ✅ / ❌ pour confirmer ou annuler la création."""

    def __init__(self) -> None:
        super().__init__(timeout=DM_TIMEOUT)
        self.value: Optional[bool] = None

    @discord.ui.button(label="Valider ✅", style=discord.ButtonStyle.success)
    async def _confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.danger)
    async def _cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class RSVPView(discord.ui.View):
    """Boutons d’inscription attachés au message d’annonce."""

    def __init__(
        self,
        role: Optional[discord.Role],
        max_slots: Optional[int],
        *,
        parent_cog: "EventConversationCog",
        store_data: dict,
    ):
        super().__init__(timeout=None)
        self.role = role
        self.max_slots = max_slots
        self.parent = parent_cog
        self.store_data = store_data
        self._going: set[int] = set(store_data.get("going", []))

    # ---------- callbacks ---------- #

    @discord.ui.button(label="Je participe ✅", style=discord.ButtonStyle.success, custom_id="rsvp_yes")
    async def rsvp_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.max_slots and len(self._going) >= self.max_slots and interaction.user.id not in self._going:
            return await interaction.response.send_message(
                "Désolé, il n’y a plus de place !", ephemeral=True
            )
        if self.role:
            try:
                await interaction.user.add_roles(self.role, reason="Inscription événement")
            except discord.Forbidden:
                pass
        self._going.add(interaction.user.id)
        self.store_data["going"] = list(self._going)
        await self.parent.console.upsert(self.store_data)
        await interaction.response.send_message("✅ Inscription enregistrée !", ephemeral=True)

    @discord.ui.button(label="Me désinscrire ❌", style=discord.ButtonStyle.danger, custom_id="rsvp_no")
    async def rsvp_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.role:
            try:
                await interaction.user.remove_roles(self.role, reason="Désinscription événement")
            except discord.Forbidden:
                pass
        self._going.discard(interaction.user.id)
        self.store_data["going"] = list(self._going)
        await self.parent.console.upsert(self.store_data)
        await interaction.response.send_message("Désinscription effectuée.", ephemeral=True)


# --------------------------------------------------------------------------- #
# Cog principal                                                               #
# --------------------------------------------------------------------------- #


class EventConversationCog(commands.Cog):
    """Workflow complet de création d’événements assisté par IA."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        announce_channel_name: str = "organisation",
        participant_role_name: str = "Participants événement",
    ):
        self.bot = bot
        self.announce_channel_name = announce_channel_name
        self.participant_role_name = participant_role_name
        self.store = EventStore(bot)
        self.console: Optional[ConsoleStore] = None
        self._conversations: Dict[int, List[str]] = {}
        self.log = _log.getChild("EventConversation")

    # ------------------------- Cog lifecycle ------------------------------ #

    async def cog_load(self) -> None:
        await self.store.connect()
        self.console = ConsoleStore(self.bot, channel_name="console")
        # Restauration des RSVPView après reboot
        records = (await self.console.load_all()).values()
        for rec in records:
            # skip events passés (> 1 jour après fin)
            if "message_id" not in rec:
                continue
            try:
                chan = await self.bot.fetch_channel(rec["channel_id"])
                msg = await chan.fetch_message(rec["message_id"])
            except discord.NotFound:
                continue

            role = chan.guild.get_role(rec.get("role_id")) if rec.get("role_id") else None
            view = RSVPView(role, rec.get("max_slots"), parent_cog=self, store_data=rec)
            view._going.update(rec.get("going", []))
            self.bot.add_view(view, message_id=msg.id)
        self.cleanup_stale_roles.start()

    async def cog_unload(self) -> None:
        self.cleanup_stale_roles.cancel()

    # ---------------------------- Commande -------------------------------- #

    @commands.command(name="event")
    @commands.has_role("Staff")
    async def cmd_event(self, ctx: commands.Context) -> None:
        """Lance la conversation DM pour programmer un événement."""
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

        # ------------------- Collecte DM ------------------- #
        transcript: List[str] = []
        self._conversations[ctx.author.id] = transcript
        await self._save_conv(ctx.author.id, transcript)

        def check(m: discord.Message) -> bool:
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

        while True:
            try:
                msg = await self.bot.wait_for("message", timeout=DM_TIMEOUT, check=check)
            except asyncio.TimeoutError:
                await dm.send("⏱️ Temps écoulé, conversation annulée.")
                await self._clear_conv(ctx.author.id)
                return

            if msg.content.lower().startswith("terminé"):
                break
            transcript.append(msg.content.strip())
            await self._save_conv(ctx.author.id, transcript)

        # ------------------- Appel IA ---------------------- #
        ia_cog = self.bot.get_cog("IACog")
        if ia_cog is None:
            await dm.send("❌ Le module IA n’est pas disponible.")
            return

        prompt = f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n" + "\n".join(transcript)
        try:
            resp, _ = await ia_cog.generate_content_with_fallback_async(prompt)
            raw_json = self._extract_json(resp.text if hasattr(resp, "text") else str(resp))
            ai_payload = json.loads(raw_json)
            draft = EventDraft.from_json(ai_payload)
        except Exception as exc:  # noqa: BLE001
            self.log.exception("Erreur IA/parsing : %s", exc)
            await dm.send(f"Impossible d’analyser la réponse IA :\n```\n{exc}\n```")
            return

        # fallback local si l’IA renvoie une date trop proche
        if draft.start_time <= discord.utils.utcnow() + MIN_DELTA:
            alt = parse_fr_datetime(" ".join(transcript))
            if alt:
                delta = draft.end_time - draft.start_time
                draft = dataclasses.replace(
                    draft,
                    start_time=alt,
                    end_time=alt + delta,
                )

        # ------------------- Preview & validation ---------- #
        view_confirm = ConfirmView()
        preview_msg = await dm.send(embed=draft.to_preview_embed(), view=view_confirm)
        await view_confirm.wait()
        await preview_msg.edit(view=None)

        if view_confirm.value is not True:
            await dm.send("Événement annulé. 👍")
            await self._clear_conv(ctx.author.id)
            return

        # ------------------- Vérifs finales ---------------- #
        now = discord.utils.utcnow()
        if draft.start_time <= now + MIN_DELTA:
            return await dm.send("⚠️ La date de début doit être au moins 5 minutes dans le futur.")
        if draft.end_time <= draft.start_time:
            return await dm.send("⚠️ L’heure de fin doit être après l’heure de début.")

        # ------------------- Création Discord -------------- #
        guild: discord.Guild = ctx.guild  # type: ignore[assignment]
        role = await self._get_or_create_participant_role(guild)

        try:
            scheduled_event = await guild.create_scheduled_event(
                name=draft.name,
                description=draft.description,
                start_time=draft.start_time,
                end_time=draft.end_time,
                entity_type=discord.EntityType.external,
                location=draft.location or "Discord",
                privacy_level=discord.PrivacyLevel.guild_only,
            )
        except discord.HTTPException as exc:
            self.log.error("create_scheduled_event: %s", exc.text)
            return await dm.send(f"❌ Impossible de créer l’événement : {exc.text}")

        announce_channel = discord.utils.get(guild.text_channels, name=self.announce_channel_name)
        if announce_channel is None:
            return await dm.send(f"❌ Canal #{self.announce_channel_name} introuvable.")

        try:
            announce_msg = await announce_channel.send(embed=draft.to_announce_embed())
            store_data = {
                "event_id": scheduled_event.id,
                "message_id": announce_msg.id,
                "channel_id": announce_channel.id,
                "role_id": role.id if role else None,
                "max_slots": draft.max_slots,
                "going": [],
            }
            view_rsvp = RSVPView(
                role,
                draft.max_slots,
                parent_cog=self,
                store_data=store_data,
            )
            await announce_msg.edit(view=view_rsvp)
            assert self.console is not None
            await self.console.upsert(store_data)
        except discord.Forbidden:
            return await dm.send("Je n’ai pas la permission d’envoyer des messages dans le canal cible.")

        await dm.send("✅ Événement créé et annoncé ! Merci.")

        # ------------------- Persistance ------------------- #
        stored = EventData(
            guild_id=guild.id,
            channel_id=announce_channel.id,
            title=draft.name,
            description=draft.description,
            starts_at=draft.start_time,
            ends_at=draft.end_time,
            max_participants=draft.max_slots,
            timezone="UTC",
            temp_role_id=role.id if role else None,
            author_id=ctx.author.id,
            announce_message_id=announce_msg.id,
            discord_event_id=scheduled_event.id,
        )
        await self._save_event(scheduled_event.id, stored)
        await self._clear_conv(ctx.author.id)

        if role:
            self.bot.loop.create_task(
                self._schedule_role_cleanup(role, draft.end_time, scheduled_event.id)
            )

    # --------------------------------------------------------------------- #
    # --------------------  Helpers & persistance  ------------------------ #
    # --------------------------------------------------------------------- #

    async def _save_conv(self, user_id: int, transcript: Optional[List[str]]):
        self._conversations[user_id] = transcript or []
        await self.store.save_conversation(str(user_id), transcript)

    async def _clear_conv(self, user_id: int):
        await self.store.save_conversation(str(user_id), None)
        self._conversations.pop(user_id, None)

    async def _save_event(self, event_id: int, payload: EventData):
        await self.store.save_event(str(event_id), payload)

    @staticmethod
    def _extract_json(text: str) -> str:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON introuvable dans la réponse IA.")
        return text[start : end + 1]

    async def _get_or_create_participant_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role = discord.utils.get(guild.roles, name=self.participant_role_name)
        if role:
            return role
        try:
            return await guild.create_role(name=self.participant_role_name, reason="Participants événements")
        except discord.Forbidden:
            self.log.warning("Permissions insuffisantes pour créer le rôle participants.")
            return None

    async def _schedule_role_cleanup(
        self, role: discord.Role, end_time: datetime, event_id: int
    ) -> None:
        delay = max(0, (end_time - discord.utils.utcnow()).total_seconds())
        await asyncio.sleep(delay)
        try:
            await role.delete(reason="Fin événement – suppression rôle temporaire")
        except discord.HTTPException:
            pass
        assert self.console is not None
        await self.console.delete(event_id)

    # --------------------------------------------------------------------- #
    # -------------------  Background tasks  ------------------------------ #
    # --------------------------------------------------------------------- #

    @tasks.loop(hours=6)
    async def cleanup_stale_roles(self):
        """Supprime les rôles « Participants événement » âgés de ≥ 7 jours."""
        assert self.console is not None
        records = await self.console.load_all()
        mapping = {data.get("role_id"): eid for eid, data in records.items() if data.get("role_id")}
        for guild in self.bot.guilds:
            for role in guild.roles:
                if (
                    role.name == self.participant_role_name
                    and (discord.utils.utcnow() - role.created_at).days >= 7
                ):
                    try:
                        await role.delete(reason="Nettoyage automatique rôles obsolètes")
                    except discord.HTTPException:
                        continue
                    event_id = mapping.get(role.id)
                    if event_id:
                        assert self.console is not None
                        await self.console.delete(event_id)


# --------------------------------------------------------------------------- #
# Setup pour discord.py (≥ 2.0)                                               #
# --------------------------------------------------------------------------- #

async def setup(bot: commands.Bot):
    await bot.add_cog(EventConversationCog(bot))
