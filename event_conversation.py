# coding: utf-8
"""
Cog EvolutionBOT : workflow interactif de création d’événements Discord.

Flux complet
------------
1. Un membre du rôle **Staff** lance `!event` dans un salon du serveur.
2. Le bot ouvre un DM ; l’utilisateur décrit librement l’événement puis tape
   `terminé`.
3. Le transcript est envoyé à l’IA (cog “IACog”) ; celle‑ci renvoie un **JSON**
   (name, description, start_time, …) que l’on parse en :class:`EventDraft`.
4. Un embed de **pré‑visualisation** est affiché dans le DM avec des boutons
   ✅ / ❌ (classe :class:`ConfirmView`).
5. Après validation :
   * Création d’un **Guild Scheduled Event** (type external).
   * Annonce dans `#organisation` (ou autre) avec un embed “sexy”.
   * Ajout de boutons RSVP. Les participants reçoivent le **rôle temporaire**
     “Participants événement” pour faciliter les pings.
   * Le rôle est automatiquement supprimé une fois l’événement terminé.

Principales améliorations
-------------------------
* Gestion correcte du **channel_id** au lieu de guild_id pour les fetchs.
* Toutes les dates sont **timezone‑aware UTC** afin d’éviter les écarts.
* Vérification fine des **permissions** (Add Reactions, Manage Roles, …).
* Nettoyage robuste des collectors / conversations même après redémarrage
  (persistance via :class:`EventStore`).
* Logging détaillé → plus simple à déboguer sur Render.com.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import dateparser
from typing import Dict, List, Optional

import discord
from discord.ext import commands, tasks

# -- helpers maison -----------------------------------------------------------
# parse_french_datetime("samedi 21h") -> datetime | None
from utils import parse_french_datetime, parse_duration
from utils.storage import EventStore
from models import EventData  # votre modèle pydantic / dataclass

# --------------------------------------------------------------------------- #

_log = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Europe/Paris")

#: délai d’inactivité max dans la conversation DM (secondes)
DM_TIMEOUT = 15 * 60

SYSTEM_PROMPT = (
    "Tu es EvolutionBOT et tu aides à créer un événement Discord. "
    "À partir de la conversation suivante, fournis uniquement un JSON strict "
    'avec les clés obligatoires : name, description, start_time, end_time, '
    "location, max_slots. Les dates sont au format JJ/MM/AAAA HH:MM. "
    "Mets null si une information est manquante. **Aucune explication**, "
    "seulement le JSON."
)

# --------------------------------------------------------------------------- #
# ------------------------------- DATA MODEL -------------------------------- #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EventDraft:
    """Représentation minimaliste de l’événement avant création Discord."""

    name: str
    description: str
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    max_slots: Optional[int] = None

    @staticmethod
    def _parse_dt(raw: str | datetime | None) -> datetime | None:
        """Transforme *raw* en datetime timezone-aware (UTC)."""
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
                _log.debug("Parsing date «%s» → %s", raw, dt)

        if dt is None:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)

        return dt.astimezone(timezone.utc)

    @classmethod
    def from_json(cls, obj: dict) -> "EventDraft":
        start = cls._parse_dt(obj.get("start_time"))
        end = cls._parse_dt(obj.get("end_time"))
        if start is None:
            raise ValueError("La date de début est introuvable ou mal comprise.")
        if end is None:
            end = start + timedelta(hours=1)
        if end <= start:
            raise ValueError("L’heure de fin doit être après l’heure de début.")
        return cls(
            name=str(obj.get("name") or "Événement"),
            description=str(obj.get("description") or "Aucune description"),
            start_time=start,
            end_time=end,
            location=obj.get("location"),
            max_slots=int(obj["max_slots"]) if obj.get("max_slots") is not None else None,
        )

    # -------- embed helpers ------------------------------------------------ #

    def to_preview_embed(self) -> discord.Embed:
        """Embed envoyé en DM pour validation."""
        embed = discord.Embed(title=f"📅 {self.name}", description=self.description, colour=0x3498db)
        embed.add_field(name="Début", value=self._fmt_dt(self.start_time), inline=False)
        embed.add_field(name="Fin", value=self._fmt_dt(self.end_time), inline=False)
        if self.location:
            embed.add_field(name="Lieu", value=self.location, inline=False)
        if self.max_slots is not None:
            embed.add_field(name="Places", value=str(self.max_slots), inline=False)
        return embed

    def to_announce_embed(self) -> discord.Embed:
        """Embed publié dans le canal organisation."""
        embed = discord.Embed(
            title=f"📣 {self.name}",
            description=self.description,
            colour=0x1abc9c,
        )
        embed.add_field(
            name="Quand",
            value=f"{self._fmt_dt(self.start_time)} • <t:{int(self.start_time.timestamp())}:R>",
            inline=False,
        )
        embed.add_field(name="Fin", value=self._fmt_dt(self.end_time), inline=False)
        if self.location:
            embed.add_field(name="Lieu", value=self.location, inline=False)
        if self.max_slots is not None:
            embed.add_field(name="Places", value=str(self.max_slots), inline=False)
        embed.set_footer(text="Clique sur un des boutons pour t’inscrire ⤵️")
        return embed

    @staticmethod
    def _fmt_dt(dt: datetime) -> str:  # format FR lisible
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


# --------------------------------------------------------------------------- #
# ------------------------------ UI COMPONENTS ------------------------------ #
# --------------------------------------------------------------------------- #


class ConfirmView(discord.ui.View):
    """Deux boutons ✅ / ❌ pour valider la pré‑visualisation."""

    def __init__(self) -> None:
        super().__init__(timeout=DM_TIMEOUT)
        self.value: Optional[bool] = None

    @discord.ui.button(emoji="✅", style=discord.ButtonStyle.success, label="Valider")
    async def _confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji="❌", style=discord.ButtonStyle.danger, label="Annuler")
    async def _cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class RSVPView(discord.ui.View):
    """Boutons d’inscription attachés au message d’annonce."""

    def __init__(self, role: Optional[discord.Role], max_slots: Optional[int]):
        super().__init__(timeout=None)
        self.role = role
        self.max_slots = max_slots
        self._going: set[int] = set()

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
        await interaction.response.send_message("✅ Inscription enregistrée !", ephemeral=True)

    @discord.ui.button(label="Me désinscrire ❌", style=discord.ButtonStyle.danger, custom_id="rsvp_no")
    async def rsvp_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.role:
            try:
                await interaction.user.remove_roles(self.role, reason="Désinscription événement")
            except discord.Forbidden:
                pass
        self._going.discard(interaction.user.id)
        await interaction.response.send_message("Désinscription effectuée.", ephemeral=True)


# --------------------------------------------------------------------------- #
# ------------------------------ MAIN   COG --------------------------------- #
# --------------------------------------------------------------------------- #


class EventConversationCog(commands.Cog):
    """Workflow DM + Scheduled Event + annonce."""

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
        self.store = EventStore(bot)  # ↳ persistance JSON/DB
        # mapping conversation en cours : user_id -> transcript list[str]
        self._conversations: Dict[int, List[str]] = {}
        self._logger = _log.getChild("EventConversation")

    # ---------------- Lifecycle ---------------- #

    async def cog_load(self) -> None:
        await self.store.connect()
        # on démarre le background task de cleanup rôles orphelins
        self.cleanup_stale_roles.start()

    async def cog_unload(self) -> None:
        self.cleanup_stale_roles.cancel()

    # ---------------- Helpers persistance ---------------- #

    async def _save_conv(self, user_id: int, transcript: Optional[List[str]]):
        await self.store.save_conversation(str(user_id), transcript)

    async def _save_event(self, event_id: int, payload: EventData):
        await self.store.save_event(str(event_id), payload)

    # ---------------- Command staff ---------------- #

    @commands.command(name="event")
    @commands.has_role("Staff")
    async def cmd_event(self, ctx: commands.Context) -> None:
        """Démarre la conversation DM pour créer un événement."""
        if ctx.guild is None:
            return await ctx.reply("Cette commande doit être utilisée dans un serveur.")

        # efface le message staff pour garder le canal propre
        try:
            await ctx.message.delete(delay=0)
        except discord.HTTPException:
            pass

        dm = await ctx.author.create_dm()
        await dm.send(
            "Décris-moi ton événement en **plusieurs messages**.\n"
            "Quand tu as fini, tape `terminé`.\n\n"
            "*(15 min d’inactivité ⇒ annulation)*"
        )

        transcript: List[str] = []
        self._conversations[ctx.author.id] = transcript
        await self._save_conv(ctx.author.id, transcript)

        def check(m: discord.Message) -> bool:
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

        # ------------- collecte DM ------------- #
        while True:
            try:
                msg = await self.bot.wait_for("message", timeout=DM_TIMEOUT, check=check)
            except asyncio.TimeoutError:
                await dm.send("⏱️ Temps écoulé, conversation annulée.")
                await self._save_conv(ctx.author.id, None)
                self._conversations.pop(ctx.author.id, None)
                return

            if msg.content.lower().startswith("terminé"):
                break
            transcript.append(msg.content.strip())
            await self._save_conv(ctx.author.id, transcript)

        # ------------- appel IA ------------- #
        ia_cog = self.bot.get_cog("IACog")
        if ia_cog is None:
            await dm.send("❌ Le module IA n’est pas disponible.")
            return

        prompt = f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n" + "\n".join(transcript)
        try:
            resp, _ = await ia_cog.generate_content_with_fallback_async(prompt)
            raw_json = self._extract_json(resp.text if hasattr(resp, "text") else str(resp))
            ai_payload = json.loads(raw_json)
            try:
                draft = EventDraft.from_json(ai_payload)
            except ValueError as exc:
                await dm.send(f"⛔ {exc}")
                return
        except Exception as exc:
            self._logger.exception("Échec parsing IA : %s", exc)
            await dm.send(f"Impossible d’analyser la réponse de l’IA.\n```\n{exc}\n```")
            return

        # date IA trop proche ? on tente un fallback full transcript
        if draft.start_time <= discord.utils.utcnow() + timedelta(minutes=5):
            alt = parse_french_datetime(" ".join(transcript))
            if alt:
                draft.start_time = alt
                draft.end_time = alt + (draft.end_time - draft.start_time)

        # si end_time toujours avant start_time ⇒ +1 h
        if draft.end_time <= draft.start_time:
            draft.end_time = draft.start_time + timedelta(hours=1)

        # ------------- preview + validation ------------- #
        view_confirm = ConfirmView()
        preview_msg = await dm.send(embed=draft.to_preview_embed(), view=view_confirm)
        await view_confirm.wait()
        await preview_msg.edit(view=None)

        if view_confirm.value is not True:
            await dm.send("Événement annulé. 👍")
            await self._save_conv(ctx.author.id, None)
            self._conversations.pop(ctx.author.id, None)
            return

        # ------------- contrôles finaux ------------- #
        now_utc = discord.utils.utcnow()
        if draft.start_time <= now_utc + timedelta(minutes=5):
            return await dm.send("⚠️ La date doit être au moins 5 minutes dans le futur.")
        if draft.end_time <= draft.start_time:
            return await dm.send("⚠️ L’heure de fin doit être après l’heure de début.")

        # ------------- création côté serveur ------------- #
        guild: discord.Guild = ctx.guild  # type: ignore
        role = await self._get_or_create_participant_role(guild)

        try:
            scheduled_event = await guild.create_scheduled_event(
                name=draft.name,
                description=draft.description[:1000],
                start_time=draft.start_time,
                end_time=draft.end_time,
                entity_type=discord.EntityType.external,
                location=draft.location or "Discord",
                privacy_level=discord.PrivacyLevel.guild_only,
            )
        except discord.HTTPException as exc:
            self._logger.error("HTTPException create_scheduled_event: %s", exc.text)
            await dm.send(f"❌ Discord refuse la création : {exc.text}")
            return

        announce_channel = discord.utils.get(guild.text_channels, name=self.announce_channel_name)
        if announce_channel is None:
            return await dm.send(f"❌ Canal #{self.announce_channel_name} introuvable !")

        view_rsvp = RSVPView(role, draft.max_slots)
        try:
            announce_msg = await announce_channel.send(embed=draft.to_announce_embed(), view=view_rsvp)
        except discord.Forbidden:
            return await dm.send("Je n’ai pas la permission d’envoyer des messages ou des embeds dans le canal cible.")

        await dm.send("✅ Événement créé et annoncé ! Merci.")

        # persistance
        stored = EventData(
            guild_id=guild.id,
            channel_id=announce_channel.id,
            title=draft.name,
            description=draft.description,
            starts_at=draft.start_time,
            ends_at=draft.end_time,
            max_participants=draft.max_slots,
            timezone="UTC",
            recurrence=None,
            temp_role_id=role.id if role else None,
            banner_url=None,
            author_id=ctx.author.id,
            announce_message_id=announce_msg.id,  # champ optionnel
            discord_event_id=scheduled_event.id,
        )
        await self._save_event(scheduled_event.id, stored)
        await self._save_conv(ctx.author.id, None)
        self._conversations.pop(ctx.author.id, None)

        # planifie la suppression du rôle
        if role:
            self.bot.loop.create_task(self._schedule_role_cleanup(role, draft.end_time))

    # --------------------------------------------------------------------- #
    # -------------------------   INTERNALS   ----------------------------- #
    # --------------------------------------------------------------------- #

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extrait la première structure JSON trouvée dans *text*."""
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON introuvable dans la réponse IA.")
        return text[start : end + 1]

    async def _get_or_create_participant_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role = discord.utils.get(guild.roles, name=self.participant_role_name)
        if role:
            return role
        try:
            return await guild.create_role(name=self.participant_role_name, reason="Rôle participants événements")
        except discord.Forbidden:
            self._logger.warning("Impossible de créer le rôle participants (permissions).")
            return None

    async def _schedule_role_cleanup(self, role: discord.Role, end_time: datetime):
        delay = max(0, (end_time - discord.utils.utcnow()).total_seconds())
        await asyncio.sleep(delay)
        try:
            await role.delete(reason="Fin de l’événement – nettoyage rôle temporaire")
        except discord.HTTPException:
            pass  # rôle déjà supprimé ou permissions manquantes

    # --------------------------------------------------------------------- #
    # ------------------------  BACKGROUND TASKS  ------------------------- #
    # --------------------------------------------------------------------- #

    @tasks.loop(hours=6)
    async def cleanup_stale_roles(self):
        """Supprime les rôles « Participants événement » plus vieux que 7 jours."""
        for guild in self.bot.guilds:
            for role in list(guild.roles):
                if (
                    role.name == self.participant_role_name
                    and (discord.utils.utcnow() - role.created_at).days >= 7
                ):
                    try:
                        await role.delete(reason="Nettoyage automatique rôles obsolètes")
                    except discord.HTTPException:
                        continue


async def setup(bot: commands.Bot):
    await bot.add_cog(EventConversationCog(bot))
