# coding: utf-8
"""Cog providing an interactive workflow to create scheduled events.

The command `!event` starts a private discussion with the user. Messages are
collected until the user types "terminé" or stops replying for 15 minutes. The
transcript is summarised via Gemini and parsed into an :class:`EventDraft`.
The user receives an embed preview and can confirm or cancel with buttons. On
confirmation a :class:`discord.GuildScheduledEvent` is created and an embed with
RSVP buttons is posted in the target channel. Participants receive a temporary
role which is removed once the event ends.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from models import EventData

import discord
from discord.ext import commands

from utils.storage import EventStore
from utils import parse_duration


SYSTEM_PROMPT = (
    "Tu es EvolutionBOT et tu aides à créer un événement Discord. "
    "À partir de la conversation suivante, fournis uniquement un JSON strict "
    "avec les clés: name, description, start_time, end_time, location, "
    "max_slots. Les dates sont au format JJ/MM/AAAA HH:MM. Mets null si une "
    "information est manquante. Aucune explication, seulement le JSON."
)

TIMEOUT = 900.0  # 15 minutes


@dataclass
class EventDraft:
    """Simple model for parsed event data returned by the LLM."""

    name: str
    description: str
    start_time: datetime
    # Event end timestamp. May be None if the user did not specify a duration.
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    max_slots: Optional[int] = None

    @staticmethod
    def from_dict(data: dict) -> "EventDraft":
        def parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            try:
                return datetime.strptime(val, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
            except Exception:
                return None

        start_time = parse_dt(data.get("start_time")) or discord.utils.utcnow()
        end_time = parse_dt(data.get("end_time"))

        return EventDraft(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            start_time=start_time,
            end_time=end_time,
            location=data.get("location"),
            max_slots=int(data["max_slots"]) if data.get("max_slots") is not None else None,
        )

    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.name, description=self.description, color=discord.Color.blue())
        embed.add_field(name="Début", value=self.start_time.strftime("%d/%m/%Y %H:%M"), inline=False)
        if self.end_time:
            embed.add_field(name="Fin", value=self.end_time.strftime("%d/%m/%Y %H:%M"), inline=False)
        if self.location:
            embed.add_field(name="Lieu", value=self.location, inline=False)
        if self.max_slots is not None:
            embed.add_field(name="Places", value=str(self.max_slots), inline=False)
        return embed


class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=TIMEOUT)
        self.value: Optional[bool] = None

    @discord.ui.button(emoji="✅", style=discord.ButtonStyle.success)
    async def validate(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class RSVPView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=None)
        self.role = role

    @discord.ui.button(label="Je participe ✅", style=discord.ButtonStyle.success)
    async def rsvp_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.role:
            try:
                await interaction.user.add_roles(self.role)
            except Exception:
                pass
        await interaction.response.send_message("Inscription enregistrée !", ephemeral=True)

    @discord.ui.button(label="Me désinscrire ❌", style=discord.ButtonStyle.danger)
    async def rsvp_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.role:
            try:
                await interaction.user.remove_roles(self.role)
            except Exception:
                pass
        await interaction.response.send_message("Désinscription enregistrée.", ephemeral=True)


class EventConversationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, target_channel: str = "organisation", role_name: str = "Participants événement"):
        self.bot = bot
        self.target_channel_name = target_channel
        self.role_name = role_name
        self.store = EventStore(bot)
        self.events: Dict[str, EventData] = {}
        self.ongoing_conversations: Dict[str, List[str]] = {}

    async def cog_load(self):
        await self.store.connect()
        data = await self.store.load()
        self.events = data.get("events", {})
        self.ongoing_conversations = data.get("conversations", {})

    async def save_event(self, event_id: str, payload: EventData):
        await self.store.save_event(event_id, payload)

    async def save_conversation_state(self, user_id: str, transcript: Optional[List[str]]):
        await self.store.save_conversation(user_id, transcript)

    @staticmethod
    def _extract_json(text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON introuvable")
        return text[start : end + 1]

    @commands.has_role("Staff")
    @commands.command(name="event")
    async def event_command(self, ctx: commands.Context):
        """Start an interactive event creation session in DM."""

        if ctx.guild is None:
            return await ctx.send("Cette commande doit être utilisée sur un serveur.")

        try:
            await ctx.message.delete()
        except Exception:
            pass

        dm = await ctx.author.create_dm()
        await dm.send(
            "Décris-moi ton événement en quelques messages. "
            "Tape **terminé** quand tu as fini. (15 min d'inactivité pour annuler)"
        )

        transcript: List[str] = []
        user_key = str(ctx.author.id)
        self.ongoing_conversations[user_key] = transcript
        await self.save_conversation_state(user_key, transcript)

        def check(m: discord.Message) -> bool:
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

        while True:
            try:
                msg = await self.bot.wait_for("message", timeout=TIMEOUT, check=check)
            except asyncio.TimeoutError:
                await dm.send("⏱️ Temps écoulé, conversation annulée.")
                await self.save_conversation_state(user_key, None)
                self.ongoing_conversations.pop(user_key, None)
                return
            content = msg.content.strip()
            if content.lower().startswith("terminé"):
                break
            transcript.append(content)
            await self.save_conversation_state(user_key, transcript)

        ia_cog = self.bot.get_cog("IACog")
        if ia_cog is None:
            await dm.send("Module IA indisponible.")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        prompt = f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n" + "\n".join(transcript)
        try:
            resp, _ = await ia_cog.generate_content_with_fallback_async(prompt)
        except Exception as e:
            await dm.send(f"Erreur IA : {e}")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        try:
            raw_json = self._extract_json(resp.text if hasattr(resp, "text") else str(resp))
            data = json.loads(raw_json)
            event = EventDraft.from_dict(data)
            if event.end_time is None:
                await dm.send(
                    "Combien de temps durera l\u2019événement ? (ex : \"2h\" ou \"01:30\")"
                )
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author,
                    timeout=TIMEOUT,
                )
                try:
                    event.end_time = event.start_time + parse_duration(msg.content)
                except Exception:
                    event.end_time = event.start_time + timedelta(hours=1)
        except Exception as e:
            await dm.send(f"Impossible de parser la réponse IA : {e}")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        preview = event.to_embed()
        view = ConfirmView()
        msg = await dm.send("Voici le résumé de l'événement :", embed=preview, view=view)
        await view.wait()
        await msg.edit(view=None)
        if view.value is not True:
            await dm.send("Événement annulé.")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        guild = ctx.guild
        role = discord.utils.get(guild.roles, name=self.role_name)
        if role is None:
            try:
                role = await guild.create_role(name=self.role_name)
            except Exception:
                role = None

        end_time = event.end_time or event.start_time + timedelta(hours=1)
        event.end_time = end_time
        try:
            scheduled = await guild.create_scheduled_event(
                name=event.name,
                description=event.description,
                start_time=event.start_time,
                end_time=end_time,
                entity_type=discord.EntityType.external,
                location=event.location or "Discord",
                privacy_level=discord.PrivacyLevel.guild_only,
            )
        except Exception as e:
            await dm.send(f"Erreur lors de la création de l'événement : {e}")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        target_chan = discord.utils.get(guild.text_channels, name=self.target_channel_name)
        if target_chan is None:
            await dm.send("Canal cible introuvable pour l'annonce de l'événement.")
            await self.save_conversation_state(user_key, None)
            self.ongoing_conversations.pop(user_key, None)
            return

        announce = event.to_embed()
        announce.set_footer(text="Réagissez avec les boutons ci-dessous pour vous inscrire")
        view_rsvp = RSVPView(role)
        await target_chan.send(embed=announce, view=view_rsvp)
        await dm.send("Événement créé et annoncé avec succès !")

        stored = EventData(
            guild_id=guild.id,
            channel_id=target_chan.id,
            title=event.name,
            description=event.description,
            starts_at=event.start_time,
            ends_at=event.end_time,
            max_participants=event.max_slots,
            timezone=None,
            recurrence=None,
            temp_role_id=role.id if role else None,
            banner_url=None,
            author_id=ctx.author.id,
        )

        await self.save_event(str(scheduled.id), stored)
        await self.save_conversation_state(user_key, None)
        self.ongoing_conversations.pop(user_key, None)

        if role and end_time:
            self.bot.loop.create_task(self._cleanup_role(role, end_time))

    async def _cleanup_role(self, role: discord.Role, end_time: datetime):
        delay = max(0, (end_time - discord.utils.utcnow()).total_seconds())
        await asyncio.sleep(delay)
        try:
            await role.delete()
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(EventConversationCog(bot))

