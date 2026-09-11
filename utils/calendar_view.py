"""Discord-native agenda, monthly overview and private activity details."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Awaitable

import discord

from calendrier import MonthlyRenderer, gen_cal
from utils.calendar_data import (
    DAY_NAMES_FR, FILTERS, GROUP_CAPACITY, MAX_DATE, MIN_DATE, MODES, PAGE_SIZE,
    CalendarState, make_page, one_line,
    paris_time, parse_anchor, plain_text, shorten, snapshot_events,
)
from utils.datetime_utils import PARIS

log = logging.getLogger(__name__)
SESSION_TIMEOUT = 600
EMBED_COLOR = 0x99ABFF
FILTER_LABELS = {
    "toutes": "Toutes les activités",
    "inscrit": "Mes inscriptions",
    "disponibles": "Places disponibles",
}
EventSource = Callable[[], Mapping[str, Any]]
ActivityAction = Callable[[discord.Interaction, str, str], Awaitable[None]]


def safe_text(value: Any, limit: int) -> str:
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(plain_text(value)))
    return shorten(text, limit).rstrip("\\") or "—"


def close_files(files: list[discord.File]) -> None:
    for file in files:
        file.close()
        file.fp.close()


async def notify(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
    except discord.HTTPException:
        log.debug("Calendar: could not deliver interaction notice", exc_info=True)


class CalendarSession(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int | None):
        super().__init__(timeout=SESSION_TIMEOUT)
        self.author_id = author_id
        self.guild_id = guild_id
        self.message: discord.Message | None = None
        self._lock = asyncio.Lock()
        self._last_embed: discord.Embed | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await notify(interaction, "Ouvre ton propre agenda avec `/calendrier` pour naviguer ou t’inscrire.")
            return False
        if self.guild_id is not None and interaction.guild_id != self.guild_id:
            await notify(interaction, "Ce calendrier appartient à un autre serveur.")
            return False
        if self.is_finished():
            await notify(interaction, "Cette session est fermée. Relance `/calendrier`.")
            return False
        if self.message is None:
            self.message = interaction.message
        return True

    async def on_error(self, interaction, error, item) -> None:
        log.error("Calendar: component failed user_id=%s item=%s", self.author_id,
                  type(item).__name__, exc_info=(type(error), error, error.__traceback__))
        await notify(interaction, "Impossible de terminer cette action. Réessaie ou relance `/calendrier`.")

    def _finished_embed(self, reason: str) -> discord.Embed | None:
        if self._last_embed is None:
            return None
        embed = self._last_embed.copy()
        embed.set_footer(text=f"{reason} · Relance /calendrier · Aucune activité supprimée.")
        return embed

    async def on_timeout(self) -> None:
        async with self._lock:
            self.stop()
            for child in self.children:
                child.disabled = True
            if self.message is not None:
                try:
                    await self.message.edit(
                        embed=self._finished_embed("Session expirée"), view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    log.debug("Calendar: timeout edit failed user_id=%s", self.author_id, exc_info=True)

    async def finish(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self._lock:
            self.stop()
            try:
                await interaction.edit_original_response(
                    embed=self._finished_embed("Session fermée"), view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.debug("Calendar: close edit failed user_id=%s", self.author_id, exc_info=True)
                await notify(interaction, "La session est fermée ; le message n’a pas pu être mis à jour.")

    async def publish_edit(
        self, interaction: discord.Interaction, embed: discord.Embed, files: list[discord.File],
    ) -> None:
        try:
            try:
                message = await interaction.edit_original_response(
                    content=None, embed=embed, attachments=files, view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                raise
            except discord.HTTPException:
                if not files:
                    raise
                log.debug("Calendar: attachment edit failed; retrying as text", exc_info=True)
                embed = embed.copy()
                embed.set_image(url=None)
                embed.add_field(
                    name="Aperçu indisponible",
                    value="Discord a refusé l’image. La liste et les menus restent disponibles.",
                    inline=False,
                )
                message = await interaction.edit_original_response(
                    content=None, embed=embed, attachments=[], view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            if message is not None:
                self.message = message
            self._last_embed = embed
        finally:
            close_files(files)


class CalendarDateModal(discord.ui.Modal):
    def __init__(self, owner: CalendrierView):
        super().__init__(title="Aller à une date", timeout=180)
        self.owner = owner
        self.target = discord.ui.TextInput(
            label="Date · JJ/MM/AAAA", default=owner.state.anchor.strftime("%d/%m/%Y"),
            placeholder="11/09/2026", min_length=10, max_length=10,
        )
        self.add_item(self.target)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.owner.interaction_check(interaction):
            return
        try:
            target = parse_anchor(str(self.target.value), self.owner.highlight_date)
        except ValueError as exc:
            await notify(interaction, str(exc))
            return
        await self.owner.change(interaction, lambda state: replace(state, anchor=target, page=0))

    async def on_error(self, interaction, error) -> None:
        await self.owner.on_error(interaction, error, self)


class CalendrierView(CalendarSession):
    def __init__(
        self, author, events: Mapping[str, Any], bg_image=None, highlight: date | None = None,
        *, source: EventSource | None = None, state: CalendarState | None = None,
        guild=None, renderer: MonthlyRenderer | None = None, action: ActivityAction | None = None,
        clock: Callable[[], datetime] | None = None, attach_files: bool = True,
        attachment_limit: int = 8 * 1024 * 1024,
    ):
        super().__init__(author.id, getattr(guild, "id", None))
        self.events = events
        self.source = source or (lambda: self.events)
        self.guild = guild
        self.renderer = renderer or MonthlyRenderer()
        self.action = action
        self.clock = clock or (lambda: datetime.now(PARIS))
        self.attach_files = attach_files
        self.attachment_limit = attachment_limit
        self.state = state or CalendarState(highlight or self.highlight_date)
        self._load()

    @property
    def highlight_date(self) -> date:
        return paris_time(self.clock()).date()

    @property
    def year(self) -> int:
        return self.state.anchor.year

    @property
    def month(self) -> int:
        return self.state.anchor.month

    def _load(self) -> None:
        self.snapshot = snapshot_events(self.source())
        self.page = make_page(self.snapshot, self.state, self.author_id, self.clock())
        self.state = self.page.state
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.previous_period.disabled = self.state.contains(MIN_DATE)
        self.next_period.disabled = self.state.contains(MAX_DATE)
        self.go_today.disabled = self.state.contains(self.highlight_date) and self.state.page == 0
        self.previous_page.disabled = self.state.page == 0
        self.next_page.disabled = self.state.page + 1 >= self.page.pages
        self.upcoming.disabled = self.page.next_event is None
        unit = "semaine" if self.state.mode == "semaine" else "mois"
        self.previous_period.label = f"{unit.capitalize()} −"
        self.next_period.label = f"{unit.capitalize()} +"
        self.choose_mode.options = [
            discord.SelectOption(label="Agenda de la semaine" if mode == "semaine" else "Vue du mois",
                                 value=mode, default=self.state.mode == mode)
            for mode in MODES
        ]
        self.choose_filter.options = [
            discord.SelectOption(label=FILTER_LABELS[name], value=name, default=self.state.filter == name)
            for name in FILTERS
        ]
        self.choose_event.disabled = not bool(self.page.events)
        self.choose_event.options = [
            discord.SelectOption(
                label=one_line(event.title, 100), value=event.id,
                description=one_line(
                    f"{event.starts_at:%d/%m %H:%M} · {len(event.participants)}/{GROUP_CAPACITY} · "
                    f"{event.status(self.author_id, self.clock())}", 100,
                ),
            ) for event in self.page.events
        ] or [discord.SelectOption(label="Aucune activité sur cette page", value="empty")]

    def build_content(self) -> str:
        return f"Calendrier des activités · {self.state.title}"

    def build_file(self) -> discord.File:
        """Compatibility helper; production rendering uses the asynchronous renderer."""
        return discord.File(
            gen_cal(self.source(), None, self.year, self.month, self.highlight_date),
            filename="calendrier.png",
        )

    def build_embed(self) -> discord.Embed:
        count = len(self.page.period_events)
        label = "Agenda de la semaine" if self.state.mode == "semaine" else "Calendrier du mois"
        description = (
            f"**{count} activité{'s' if count != 1 else ''}** · {FILTER_LABELS[self.state.filter]}\n"
            "Horaires fixes : **Europe/Paris**. Les délais sont calculés par Discord."
        )
        if self.state.filter != "toutes":
            description += f"\n{self.page.unfiltered_count} activité(s) avant filtrage."
        if not count:
            description += (
                "\n\nAucune activité pour cette période et ce filtre.\n"
                "Essaie **Prochaine activité**, une autre période ou le filtre **Toutes les activités**.\n"
                "Pour proposer une sortie : `/activite creer`."
            )
        else:
            description += "\nChoisis une activité dans le menu pour les détails et l’inscription."
        if self.snapshot.skipped:
            description += (
                f"\n⚠️ {self.snapshot.skipped} entrée(s) invalide(s) ignorée(s). "
                "Le Staff peut vérifier les logs."
            )
        embed = discord.Embed(
            title=f"{label} · {self.state.title}", description=description, color=EMBED_COLOR,
        )
        for event in self.page.events:
            day = f"{DAY_NAMES_FR[event.day.weekday()]} {event.starts_at:%d/%m}"
            if event.day == self.highlight_date:
                day += " · Aujourd’hui"
            name = safe_text(f"{day} · {event.starts_at:%H:%M} — {event.title}", 256)
            value = (
                f"**{event.status(self.author_id, self.clock())}** · "
                f"{len(event.participants)}/{GROUP_CAPACITY} inscrits · <t:{event.timestamp}:R>\n"
                f"{safe_text(event.description or 'Aucune précision supplémentaire.', 180)}\n"
                f"ID : `{safe_text(event.id, 100)}`"
            )
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text=(
            f"Page {self.state.page + 1}/{self.page.pages} · Actualiser pour les dernières données · "
            "Session : 10 min d’inactivité · /calendrier pour ta propre vue"
        ))
        return embed

    async def build_payload(self) -> tuple[discord.Embed, list[discord.File]]:
        self._load()
        embed = self.build_embed()
        files = []
        if self.state.mode == "mois" and self.attach_files:
            try:
                png = await self.renderer.render(
                    self.page.period_events, self.year, self.month, self.highlight_date,
                )
                if len(png) > self.attachment_limit:
                    raise ValueError("monthly image exceeds attachment limit")
                digest = hashlib.sha256(png).hexdigest()[:10]
                filename = f"calendrier-{self.year}-{self.month:02d}-{digest}.png"
                files.append(discord.File(
                    io.BytesIO(png), filename=filename,
                    description=f"{self.state.title} · Horaires de Paris · Détails dans le texte",
                ))
                embed.set_image(url=f"attachment://{filename}")
            except Exception:
                log.exception("Calendar: image unavailable; keeping native agenda")
                embed.add_field(
                    name="Aperçu mensuel indisponible",
                    value="Les activités restent consultables dans la liste et les menus.",
                    inline=False,
                )
        elif self.state.mode == "mois":
            embed.add_field(
                name="Mode texte",
                value="L’aperçu nécessite la permission « Joindre des fichiers ». L’agenda reste utilisable.",
                inline=False,
            )
        self._last_embed = embed
        return embed, files

    async def change(self, interaction, transform: Callable[[CalendarState], CalendarState]) -> None:
        await interaction.response.defer()
        async with self._lock:
            if self.is_finished():
                await notify(interaction, "La session est terminée. Relance `/calendrier`.")
                return
            previous = self.state
            previous_embed = self._last_embed
            try:
                self.state = transform(self.state)
                embed, files = await self.build_payload()
                if self.is_finished():
                    close_files(files)
                    return
                await self.publish_edit(interaction, embed, files)
                log.debug("Calendar: refreshed user_id=%s mode=%s anchor=%s page=%s",
                          self.author_id, self.state.mode, self.state.anchor, self.state.page)
            except discord.NotFound:
                self.stop()
                await notify(interaction, "Le calendrier a été supprimé. Relance `/calendrier`.")
            except Exception:
                self.state = previous
                self._last_embed = previous_embed
                self._load()
                log.exception("Calendar: refresh failed user_id=%s", self.author_id)
                await notify(interaction, "Mise à jour impossible. Réessaie avec Actualiser.")

    @discord.ui.button(label="Semaine −", style=discord.ButtonStyle.secondary, row=0)
    async def previous_period(self, interaction, button):
        await self.change(interaction, lambda state: state.step(-1))

    @discord.ui.button(label="Aujourd’hui", style=discord.ButtonStyle.primary, row=0)
    async def go_today(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, anchor=self.highlight_date, page=0))

    @discord.ui.button(label="Semaine +", style=discord.ButtonStyle.secondary, row=0)
    async def next_period(self, interaction, button):
        await self.change(interaction, lambda state: state.step(1))

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction, button):
        await self.change(interaction, lambda state: state)

    @discord.ui.button(label="Aller à…", style=discord.ButtonStyle.secondary, row=0)
    async def jump(self, interaction, button):
        await interaction.response.send_modal(CalendarDateModal(self))

    @discord.ui.select(placeholder="Choisir une vue", row=1)
    async def choose_mode(self, interaction, select):
        mode = select.values[0]
        if mode not in MODES:
            await notify(interaction, "Vue inconnue.")
            return
        await self.change(interaction, lambda state: replace(state, mode=mode, page=0))

    @discord.ui.select(placeholder="Filtrer les activités", row=2)
    async def choose_filter(self, interaction, select):
        selected = select.values[0]
        if selected not in FILTERS:
            await notify(interaction, "Filtre inconnu.")
            return
        await self.change(interaction, lambda state: replace(state, filter=selected, page=0))

    @discord.ui.select(placeholder="Détails et inscription · choisir une activité", row=3)
    async def choose_event(self, interaction, select):
        await interaction.response.defer(ephemeral=True, thinking=True)
        event_id = select.values[0]
        detail = ActivityDetailView(
            self.author_id, self.guild, self.source, event_id, self.action, self.clock,
        )
        if snapshot_events(self.source()).get(event_id) is None:
            detail.stop()
            await notify(interaction, "Cette activité a été supprimée ou annulée. Actualise le calendrier.")
            return
        embed = detail.build_embed()
        try:
            detail.message = await interaction.followup.send(
                embed=embed, view=detail, ephemeral=True, wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            detail.stop()
            raise

    @discord.ui.button(label="Page −", style=discord.ButtonStyle.secondary, row=4)
    async def previous_page(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, page=state.page - 1))

    @discord.ui.button(label="Page +", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, page=state.page + 1))

    @discord.ui.button(label="Prochaine activité", style=discord.ButtonStyle.secondary, row=4)
    async def upcoming(self, interaction, button):
        def target(state):
            snapshot = snapshot_events(self.source())
            page = make_page(snapshot, state, self.author_id, self.clock())
            if page.next_event is None:
                return state
            target_state = replace(state, anchor=page.next_event.day, page=0)
            target_page = make_page(snapshot, target_state, self.author_id, self.clock())
            index = next((index for index, event in enumerate(target_page.period_events)
                          if event.id == page.next_event.id), 0)
            return replace(target_state, page=index // PAGE_SIZE)
        await self.change(interaction, target)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary, row=4)
    async def close(self, interaction, button):
        await self.finish(interaction)


class ActivityDetailView(CalendarSession):
    def __init__(
        self, author_id: int, guild, source: EventSource, event_id: str,
        action: ActivityAction | None, clock: Callable[[], datetime],
    ):
        super().__init__(author_id, getattr(guild, "id", None))
        self.guild = guild
        self.source = source
        self.event_id = event_id
        self.action = action
        self.clock = clock

    def _member_name(self, user_id: int) -> str:
        member = self.guild.get_member(user_id) if self.guild else None
        return safe_text(getattr(member, "display_name", f"Membre {user_id}"), 80)

    def build_embed(self) -> discord.Embed:
        event = snapshot_events(self.source()).get(self.event_id)
        if event is None:
            self.join.disabled = self.leave.disabled = True
            embed = discord.Embed(
                title="Activité indisponible",
                description="Elle a été annulée ou retirée du stockage. Actualise le calendrier.",
                color=EMBED_COLOR,
            )
        else:
            self.join.disabled = (
                self.action is None or not event.places or event.has_started(self.clock())
                or self.author_id in event.participants
            )
            self.leave.disabled = self.action is None or self.author_id not in event.participants
            embed = discord.Embed(
                title=safe_text(event.title, 256),
                description=safe_text(event.description or "Aucune description renseignée.", 2500),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="Rendez-vous",
                value=(f"**{event.starts_at:%d/%m/%Y à %H:%M} · Europe/Paris**\n"
                       f"Dans ton fuseau Discord : <t:{event.timestamp}:F> · <t:{event.timestamp}:R>"),
                inline=False,
            )
            embed.add_field(name="Statut", value=event.status(self.author_id, self.clock()), inline=True)
            embed.add_field(name="Organisateur", value=self._member_name(event.creator_id), inline=True)
            members = "\n".join(self._member_name(user_id) for user_id in event.participants)
            embed.add_field(
                name=f"Participants · {len(event.participants)}/{GROUP_CAPACITY}",
                value=shorten(members, 1000) or "Aucun participant pour le moment.", inline=False,
            )
            embed.add_field(
                name="Commandes alternatives",
                value=(f"`/activite rejoindre identifiant:{safe_text(event.id, 100)}`\n"
                       f"`/activite quitter identifiant:{safe_text(event.id, 100)}`"),
                inline=False,
            )
            if len(event.description) > 2500 or len(event.title) > 256:
                embed.add_field(
                    name="Texte abrégé",
                    value="Utilise le bouton Texte complet pour consulter le titre et la description.",
                    inline=False,
                )
        self.full_text.disabled = event is None
        embed.set_footer(text=f"ID : {self.event_id} · Détails privés · Session : 10 min d’inactivité")
        self._last_embed = embed
        return embed

    async def update(self, interaction, action: str | None = None) -> None:
        await interaction.response.defer()
        async with self._lock:
            if self.is_finished():
                await notify(interaction, "La fiche est fermée. Rouvre-la depuis `/calendrier`.")
                return
            if action is not None:
                event = snapshot_events(self.source()).get(self.event_id)
                if event is None:
                    await notify(interaction, "Cette activité n’est plus disponible.")
                elif action == "join" and event.has_started(self.clock()):
                    await notify(interaction, "Le début de cette activité est déjà passé.")
                elif self.action is not None:
                    await self.action(interaction, self.event_id, action)
            await self.publish_edit(interaction, self.build_embed(), [])

    @discord.ui.button(label="Rejoindre", style=discord.ButtonStyle.success, row=0)
    async def join(self, interaction, button):
        await self.update(interaction, "join")

    @discord.ui.button(label="Quitter", style=discord.ButtonStyle.secondary, row=0)
    async def leave(self, interaction, button):
        await self.update(interaction, "leave")

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction, button):
        await self.update(interaction)

    @discord.ui.button(label="Texte complet", style=discord.ButtonStyle.secondary, row=1)
    async def full_text(self, interaction, button):
        await interaction.response.defer()
        self.message = await interaction.original_response()
        event = snapshot_events(self.source()).get(self.event_id)
        if event is None:
            await notify(interaction, "Cette activité n’est plus disponible.")
            return
        text = (
            f"{event.title}\n{event.starts_at:%d/%m/%Y %H:%M} (Europe/Paris)\n"
            f"ID : {event.id}\n\n{event.description or 'Aucune description.'}\n"
        ).encode("utf-8")
        if len(text) > 1024 * 1024:
            await notify(interaction, "Ce texte dépasse 1 Mio. Le Staff peut le consulter dans #console.")
            return
        file = discord.File(io.BytesIO(text), filename="activite.txt")
        try:
            await interaction.followup.send(
                file=file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            close_files([file])

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction, button):
        await self.finish(interaction)
