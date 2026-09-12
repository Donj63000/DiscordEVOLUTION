"""Fast activity forms, private navigation and persistent public registration cards."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone

import discord

from utils.activity_data import (
    ActivityError, DEFAULT_CAPACITY, PARIS, activity_end, activity_status, duration_minutes,
    utc, validate_draft,
)
from utils.activity_media import announcement_image, close_artwork, IMAGE_FILENAME
from utils.calendar_data import one_line, shorten
from utils.slash_errors import send_interaction_error
from utils.slash_support import invoke_from_component

log = logging.getLogger(__name__)


def safe(value: object, limit: int = 1000) -> str:
    return shorten(discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(value or ""))), limit)


def activity_embed(record: dict, *, preview=False, now=None, image_url=None) -> discord.Embed:
    starts = utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
    ends = activity_end(record)
    stamp = int(starts.timestamp())
    participants = record.get("participants", [])
    waiting = record.get("waitlist", [])
    capacity = int(record.get("capacity", DEFAULT_CAPACITY))
    status = activity_status(record, now or datetime.now(timezone.utc))
    places = max(0, capacity - len(participants))
    description = safe(record.get("description"), 1600) or "On se retrouve pour une sortie de guilde !"
    embed = discord.Embed(
        title="⚔️ " + safe(record.get("titre"), 180),
        description=description,
        colour=discord.Colour.orange() if record.get("cancelled") else discord.Colour.blue(),
    )
    embed.set_author(name="EVOLUTION • SORTIE DE GUILDE")
    if image_url:
        embed.set_image(url=image_url)
    embed.add_field(
        name="📅 Rendez-vous",
        value=f"**{starts.astimezone(PARIS):%d/%m/%Y à %H:%M} · heure de Paris**\n"
              f"<t:{stamp}:F> · <t:{stamp}:R>",
        inline=False,
    )
    embed.add_field(
        name="⏱️ Fin prévue",
        value=f"{ends.astimezone(PARIS):%d/%m/%Y à %H:%M} (Paris)\n"
              "Le rôle temporaire sera supprimé après cet horaire.",
        inline=False,
    )
    embed.add_field(name="📍 Rendez-vous", value=safe(record.get("lieu")) or "À préciser avec l'organisateur.")
    embed.add_field(name="👑 Organisateur", value=f"<@{record['creator_id']}>")
    embed.add_field(name="🎟️ Places restantes", value=f"**{places}** · {len(participants)}/{capacity} inscrits")
    embed.add_field(
        name=f"✅ Participants · {len(participants)}/{capacity} (organisateur compris)",
        value=shorten("\n".join(f"<@{uid}>" for uid in participants), 1000) or "Aucun inscrit.",
        inline=False,
    )
    if waiting:
        embed.add_field(
            name=f"⌛ Liste d'attente · {len(waiting)}",
            value=shorten("\n".join(f"{i}. <@{uid}>" for i, uid in enumerate(waiting, 1)), 1000),
            inline=False,
        )
    if record.get("role_id"):
        embed.add_field(name="🛡️ Équipe temporaire", value=f"<@&{record['role_id']}>", inline=False)
    if record.get("role_error") and status not in {"Terminée", "Annulée"}:
        embed.add_field(name="Rôle en attente", value=safe(record["role_error"], 300), inline=False)
    embed.add_field(name="Statut", value=status, inline=False)
    if status in {"Inscriptions ouvertes", "Complet · liste d'attente ouverte"}:
        embed.add_field(
            name="Comment participer ?",
            value="**S'inscrire** pour participer, **Se désinscrire** pour libérer ta place.\n"
                  "Complet ? Rejoins la **liste d'attente** : la prochaine place libérée lui revient.",
            inline=False,
        )
    source = record.get("announcement_url")
    if source:
        embed.add_field(name="Annonce du membre", value=f"[Lire l'annonce d'origine]({source})",
                        inline=False)
    footer = ("Aperçu privé · Rien n'est encore enregistré" if preview else
              f"Activité #{record['id']} · Participants : liste complète · /activite rejoindre · /calendrier")
    embed.set_footer(text=footer)
    return embed


class ActivityView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        log.exception("Activity interface failed item=%s", getattr(item, "custom_id", None),
                      exc_info=error)
        await send_interaction_error(
            interaction, str(error) if isinstance(error, ActivityError) else
            "L'action n'a pas pu être confirmée. Rouvre la fiche pour vérifier son état.",
        )


class OwnedActivityView(ActivityView):
    def __init__(self, cog, owner_id: int, guild_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.message = None

    async def interaction_check(self, interaction):
        if (interaction.user.id, interaction.guild_id) != (self.owner_id, self.guild_id):
            await send_interaction_error(interaction, "Ce panneau privé appartient à un autre membre.")
            return False
        if self.is_finished():
            await send_interaction_error(interaction, "Panneau expiré. Rouvre `/activite liste`.")
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                log.debug("Activity private view expired without editable message")


class ActivityModal(discord.ui.Modal):
    def __init__(self, cog, owner_id: int, guild_id: int, *, values=None,
                 event_id=None, revision=None, creation_key=None, announcement_url=None):
        super().__init__(title="Modifier la sortie" if event_id else "Proposer une sortie", timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.event_id = event_id
        self.revision = revision
        self.creation_key = creation_key or uuid.uuid4().hex
        self.announcement_url = announcement_url
        values = values or {}
        self.duration = duration_minutes(values.get("duree"))
        self.title_input = discord.ui.TextInput(
            label="Quoi ? Nom de la sortie", placeholder="Donjon Blop, session drop…",
            default=str(values.get("titre", ""))[:85], max_length=85,
        )
        self.when_input = discord.ui.TextInput(
            label="Quand ? Date et heure de Paris",
            placeholder="demain 21h ou 18/09/2026 20:30",
            default=str(values.get("date", ""))[:60], max_length=60,
        )
        self.where_input = discord.ui.TextInput(
            label="Où ? Lieu / rendez-vous", placeholder="Entrée du donjon, vocal Sorties…",
            default=str(values.get("lieu", ""))[:120], max_length=120, required=False,
        )
        self.capacity_input = discord.ui.TextInput(
            label="Places (toi compris) · 8 par défaut",
            default=str(values.get("capacite", DEFAULT_CAPACITY)),
            max_length=3, required=False,
        )
        self.description_input = discord.ui.TextInput(
            label="Précisions (facultatif)", style=discord.TextStyle.paragraph,
            placeholder="Objectif, durée estimée, prérequis, matériel à apporter…",
            default=str(values.get("description", ""))[:1500], max_length=1500, required=False,
        )
        for field in (self.title_input, self.when_input, self.where_input,
                      self.capacity_input, self.description_input):
            self.add_item(field)

    async def on_submit(self, interaction):
        if (interaction.user.id, interaction.guild_id) != (self.owner_id, self.guild_id):
            return await send_interaction_error(interaction, "Ce formulaire appartient à un autre membre.")
        values = {
            "titre": self.title_input.value, "date": self.when_input.value,
            "lieu": self.where_input.value, "capacite": self.capacity_input.value,
            "description": self.description_input.value, "duree": self.duration,
        }
        try:
            draft = validate_draft(values, now=self.cog.now())
        except ActivityError as exc:
            view = DraftView(self, values, valid=False)
            await interaction.response.send_message(
                f"{exc}\n**Corriger** conserve ce que tu as déjà saisi.",
                view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            values["date"] = utc(datetime.fromisoformat(draft["starts_at"])).astimezone(PARIS).strftime(
                "%d/%m/%Y %H:%M"
            )
            view = DraftView(self, values, valid=True)
            current = self.cog.activities_data["events"].get(str(self.event_id), {})
            preview = {
                **current, **draft, "id": self.event_id or "aperçu",
                "creator_id": current.get("creator_id", interaction.user.id),
                "participants": current.get("participants", [interaction.user.id]),
                "announcement_url": self.announcement_url,
            }
            await interaction.response.defer(thinking=True, ephemeral=True)
            artwork, _ = announcement_image(getattr(interaction, "channel", None))
            try:
                view.message = await interaction.edit_original_response(
                    content=(
                        "Vérifie la sortie puis clique **Créer la sortie** : annonce automatique dans "
                        "#organisation, inscription et rôle temporaire. **Aucun ping général**."
                        if not self.event_id else "Vérifie les changements avant de les enregistrer."
                    ),
                    embed=activity_embed(
                        preview, preview=True, now=self.cog.now(),
                        image_url=f"attachment://{IMAGE_FILENAME}" if artwork else None,
                    ), view=view, attachments=[artwork] if artwork else [],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                close_artwork(artwork)
        if view.message is None:
            view.message = await interaction.original_response()

    async def on_error(self, interaction, error):
        log.exception("Activity form failed", exc_info=error)
        await send_interaction_error(interaction, "Formulaire indisponible. Rouvre `/activite creer`.")


class DraftView(OwnedActivityView):
    def __init__(self, modal: ActivityModal, values: dict, *, valid: bool):
        super().__init__(modal.cog, modal.owner_id, modal.guild_id)
        self.modal = modal
        self.values = dict(values)
        self._used = False
        self._lock = asyncio.Lock()
        self.confirm.disabled = not valid
        self.confirm.label = "Enregistrer les changements" if modal.event_id else "Créer la sortie"

    @discord.ui.button(label="Créer la sortie", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        async with self._lock:
            if self._used:
                return await send_interaction_error(interaction, "Cette demande a déjà été traitée.")
            self._used = True
            await interaction.response.edit_message(view=None)
            self.stop()
            values = {
                **self.values, "_draft": True, "_revision": self.modal.revision,
                "_creation_key": self.modal.creation_key,
                "_announcement_url": self.modal.announcement_url,
            }
            action = f"modifier {self.modal.event_id}" if self.modal.event_id else "creer"
            ctx = await invoke_from_component(
                self.cog.bot, interaction, "activite", action, values=values,
            )
            if not getattr(ctx, "activity_committed", False):
                retry = DraftView(self.modal, self.values, valid=True)
                retry.message = await interaction.followup.send(
                    "Brouillon conservé : tu peux corriger ou réessayer après résolution du problème.",
                    view=retry, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                )

    @discord.ui.button(label="Corriger", style=discord.ButtonStyle.secondary)
    async def correct(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        async with self._lock:
            if self._used:
                return await send_interaction_error(interaction, "Cette demande a déjà été traitée.")
            self._used = True
            self.stop()
            await interaction.response.send_modal(ActivityModal(
                self.cog, self.owner_id, self.guild_id, values=self.values,
                event_id=self.modal.event_id, revision=self.modal.revision,
                creation_key=self.modal.creation_key, announcement_url=self.modal.announcement_url,
            ))

    @discord.ui.button(label="Abandonner", style=discord.ButtonStyle.secondary)
    async def abandon(self, interaction, button):
        async with self._lock:
            if not await self.interaction_check(interaction) or self._used:
                return
            self._used = True
            self.stop()
            await interaction.response.edit_message(
                content="Brouillon abandonné. Rien n'a été enregistré.", embed=None, view=None,
            )


class ActivityCardView(ActivityView):
    def __init__(self, cog, record: dict, *, persistent=True):
        super().__init__(timeout=None if persistent else 600)
        self.cog = cog
        self.event_id = str(record["id"])
        for child in self.children:
            child.custom_id = f"evo:activity:{self.event_id}:{child.custom_id}"
        closed = record.get("cancelled") or utc(datetime.fromisoformat(
            record.get("starts_at") or record["date_str"])) <= utc(cog.now())
        self.join.disabled = self.leave.disabled = bool(closed)
        self.join.label = (
            "Liste d'attente" if len(record.get("participants", [])) >= record.get("capacity", DEFAULT_CAPACITY)
            else "S'inscrire"
        )

    async def run(self, interaction, action):
        await invoke_from_component(self.cog.bot, interaction, "activite", f"{action} {self.event_id}")

    @discord.ui.button(label="S'inscrire", style=discord.ButtonStyle.success, custom_id="join")
    async def join(self, interaction, button):
        await self.run(interaction, "join")

    @discord.ui.button(label="Se désinscrire", style=discord.ButtonStyle.secondary, custom_id="leave")
    async def leave(self, interaction, button):
        await self.run(interaction, "leave")

    @discord.ui.button(label="Détails", style=discord.ButtonStyle.secondary, custom_id="info")
    async def info(self, interaction, button):
        await self.run(interaction, "info")

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, custom_id="roster")
    async def roster(self, interaction, button):
        await self.run(interaction, "participants")

    @discord.ui.button(label="Gérer", style=discord.ButtonStyle.secondary, custom_id="manage")
    async def manage(self, interaction, button):
        await self.run(interaction, "gerer")

    @discord.ui.button(label="Calendrier", style=discord.ButtonStyle.secondary, custom_id="calendar", row=1)
    async def calendar(self, interaction, button):
        await invoke_from_component(self.cog.bot, interaction, "calendrier", "")



class ActivityManageView(OwnedActivityView):
    def __init__(self, cog, owner_id, guild_id, event_id):
        super().__init__(cog, owner_id, guild_id)
        self.event_id = str(event_id)

    @discord.ui.button(label="Modifier", style=discord.ButtonStyle.primary)
    async def modify(self, interaction, button):
        await invoke_from_component(
            self.cog.bot, interaction, "activite", f"modifier {self.event_id}", defer_response=False,
        )

    @discord.ui.button(label="Annuler la sortie", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        from utils.slash_catalog import custom_routes
        from utils.slash_confirm import request_confirmation

        route = next(route for route in custom_routes() if route.path == ("activite", "annuler"))
        await request_confirmation(
            self.cog.bot, interaction, route, f"annuler {self.event_id}",
            {"identifiant": self.event_id},
        )

    @discord.ui.button(label="Réparer / publier la fiche", style=discord.ButtonStyle.secondary)
    async def publish(self, interaction, button):
        await invoke_from_component(
            self.cog.bot, interaction, "activite", f"publier {self.event_id}",
        )


class ActivityListView(OwnedActivityView):
    def __init__(self, cog, owner_id, guild_id, *, action="info"):
        super().__init__(cog, owner_id, guild_id)
        self.action = action
        self.mode = "inscrit" if action == "leave" else "avenir"
        self.page = 0
        if action != "info":
            self.remove_item(self.filter_events)
        self.choose.placeholder = {
            "join": "Choisir une sortie pour s'inscrire",
            "leave": "Choisir la sortie à quitter",
        }.get(action, "Choisir une sortie · Détails et inscription")

    def build_embed(self):
        now = utc(self.cog.now())
        events = []
        for record in self.cog.events_for_guild(self.guild_id).values():
            starts = utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
            past = starts <= now or record.get("cancelled", False)
            if self.mode == "historique":
                selected = past
            elif self.mode == "inscrit":
                selected = not past and self.owner_id in (
                    record.get("participants", []) + record.get("waitlist", [])
                )
            elif self.mode == "organise":
                selected = not past and record["creator_id"] == self.owner_id
            else:
                selected = not past
            if self.action == "join":
                selected = selected and self.owner_id not in (
                    record.get("participants", []) + record.get("waitlist", [])
                ) and len(record.get("waitlist", [])) < 100
            if selected:
                events.append(record)
        events.sort(key=lambda e: utc(datetime.fromisoformat(e.get("starts_at") or e["date_str"])),
                    reverse=self.mode == "historique")
        pages = max(1, (len(events) + 5) // 6)
        self.page = min(self.page, pages - 1)
        subset = events[self.page * 6:self.page * 6 + 6]
        embed = discord.Embed(
            title={"join": "Rejoindre une activité", "leave": "Me désinscrire"}.get(
                self.action, "Les sorties de la guilde"),
            description={
                "join": "Choisis dans le menu ci-dessous : **tu seras inscrit directement**.\n"
                        "Une sortie complète t'inscrit en liste d'attente, sans rôle pour l'instant.",
                "leave": "Choisis dans le menu la sortie à quitter : ta place sera libérée.",
            }.get(self.action, "Choisis une sortie pour voir l'annonce, les participants et t'inscrire."),
            colour=discord.Colour.blue(),
        )
        for record in subset:
            starts = utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
            participants = record.get("participants", [])
            capacity = record.get("capacity", DEFAULT_CAPACITY)
            places = max(0, capacity - len(participants))
            names = " ".join(f"<@{uid}>" for uid in participants[:8]) or "Aucun inscrit"
            if len(participants) > 8:
                names += f" + {len(participants) - 8} autre(s)"
            text = (
                f"**{starts.astimezone(PARIS):%d/%m/%Y à %H:%M} (Paris)** · <t:{int(starts.timestamp())}:R>\n"
                f"**{len(participants)}/{capacity} inscrits · {places} place(s) restante(s)**"
                f" · {len(record.get('waitlist', []))} en attente\n"
                f"{names}\n{safe(record.get('lieu') or 'Rendez-vous à préciser', 160)}"
            )
            if record.get("cancelled"):
                text += "\n**Annulée**"
            embed.add_field(name=safe(f"#{record['id']} · {record['titre']}", 180),
                            value=text, inline=False)
        if not subset:
            embed.description += "\n\nAucune sortie dans ce filtre. Le bouton Créer ouvre le formulaire."
        self.choose.options = [
            discord.SelectOption(
                label=one_line(e["titre"], 95), value=str(e["id"]),
                description=one_line(
                    f"{utc(datetime.fromisoformat(e.get('starts_at') or e['date_str'])).astimezone(PARIS):%d/%m/%Y %H:%M}"
                    f" · {len(e.get('participants', []))}/{e.get('capacity', DEFAULT_CAPACITY)} inscrits"
                    f" · {max(0, e.get('capacity', DEFAULT_CAPACITY) - len(e.get('participants', [])))} libres", 100,
                ),
            ) for e in subset
        ] or [discord.SelectOption(label="Aucune sortie", value="empty")]
        self.choose.disabled = not subset
        self.previous.disabled = self.page <= 0
        self.next_page.disabled = self.page >= pages - 1
        embed.set_footer(text=f"Page {self.page + 1}/{pages} · Panneau privé · Expire après 10 min")
        return embed

    @discord.ui.select(placeholder="Afficher…", row=0, options=[
        discord.SelectOption(label="À venir", value="avenir"),
        discord.SelectOption(label="Mes inscriptions et ma liste d'attente", value="inscrit"),
        discord.SelectOption(label="Mes sorties organisées", value="organise"),
        discord.SelectOption(label="Historique et sorties annulées", value="historique"),
    ])
    async def filter_events(self, interaction, select):
        self.mode = select.values[0]
        self.page = 0
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(placeholder="Choisir une sortie", row=1)
    async def choose(self, interaction, select):
        await invoke_from_component(
            self.cog.bot, interaction, "activite", f"{self.action} {select.values[0]}",
        )
        if self.action != "info" and interaction.message is not None:
            try:
                await interaction.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                log.debug("Activity picker refresh failed", exc_info=True)

    @discord.ui.button(label="Précédent", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Suivant", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction, button):
        self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Créer une sortie", style=discord.ButtonStyle.success, row=3)
    async def create(self, interaction, button):
        await invoke_from_component(
            self.cog.bot, interaction, "activite", "creer", defer_response=False,
        )

    @discord.ui.button(label="Calendrier", style=discord.ButtonStyle.secondary, row=3)
    async def calendar(self, interaction, button):
        await invoke_from_component(self.cog.bot, interaction, "calendrier", "")


def roster_file(record: dict, guild) -> discord.File:
    lines = [record["titre"], f"Activité #{record['id']}", "", "INSCRITS"]
    for heading, ids in (
        (None, record.get("participants", [])), ("LISTE D'ATTENTE", record.get("waitlist", [])),
    ):
        if heading:
            lines.extend(["", heading])
        for position, user_id in enumerate(ids, 1):
            member = guild.get_member(user_id)
            lines.append(f"{position}. {getattr(member, 'display_name', 'Membre')} (ID {user_id})")
    return discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename="participants.txt")
