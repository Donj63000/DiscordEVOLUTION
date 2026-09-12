"""Calendrier Discord sans formulaire initial, navigation compacte et inscriptions privées."""

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
    DAY_NAMES_FR, FILTERS, GROUP_CAPACITY, MAX_DATE, MIN_DATE, PAGE_SIZE,
    CalendarEvent, CalendarState, make_page, one_line,
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

    async def _check_context(self, interaction: discord.Interaction) -> bool:
        """Vérifie le serveur et l'expiration avant toute ouverture ou modification."""
        if self.guild_id is not None and interaction.guild_id != self.guild_id:
            await notify(interaction, "Ce calendrier appartient à un autre serveur.")
            return False
        if self.is_finished():
            await notify(interaction, "Cette session est fermée. Relance `/calendrier`.")
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self._check_context(interaction):
            return False
        if interaction.user.id != self.author_id:
            await notify(interaction, "Ouvre ton propre agenda avec `/calendrier`.")
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
                        embed=self._finished_embed("Session expirée"), view=None,
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
        """Réutilise une image inchangée ; chaque objet File est fermé même en cas d'échec."""
        attachments = files
        if len(files) == 1 and self.message is not None:
            retained = next(
                (item for item in getattr(self.message, "attachments", ())
                 if item.filename == files[0].filename), None,
            )
            if retained is not None:
                attachments = [retained]
                log.debug("Calendar: reusing published monthly attachment")
        try:
            try:
                message = await interaction.edit_original_response(
                    content=None, embed=embed, attachments=attachments, view=self,
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
    """Un mois immédiat, des commandes courantes visibles et des réglages à la demande."""

    def __init__(
        self, author, events: Mapping[str, Any], bg_image=None, highlight: date | None = None,
        *, source: EventSource | None = None, state: CalendarState | None = None,
        guild=None, renderer: MonthlyRenderer | None = None, action: ActivityAction | None = None,
        clock: Callable[[], datetime] | None = None, attach_files: bool = True,
        attachment_limit: int = 8 * 1024 * 1024, private: bool = False, registry=None,
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
        self.private = private
        self.registry = registry
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Les visiteurs peuvent consulter une fiche, mais jamais déplacer la vue d'autrui."""
        if not await self._check_context(interaction):
            return False
        if not self.private and interaction.message is not None:
            self.message = interaction.message
        if interaction.user.id == self.author_id:
            if self.message is None:
                self.message = interaction.message
            return True
        data = getattr(interaction, "data", None) or {}
        component_id = data.get("custom_id")
        if not self.private:
            if component_id == self.choose_event.custom_id:
                return True
            if (component_id == self.choose_options.custom_id
                    and data.get("values") in (["private"], ["inscrit"])):
                return True
        await notify(
            interaction,
            "La navigation de ce calendrier est réservée à son auteur. "
            "Choisis **Filtres et options → Ouvrir en privé**, ou lance `/calendrier`.",
        )
        return False

    def _load(self) -> None:
        """Chaque action lit un instantané récent sans modifier les données persistées."""
        self.snapshot = snapshot_events(self.source())
        self.page = make_page(self.snapshot, self.state, self.author_id, self.clock())
        self.state = self.page.state
        self._sync_controls()

    def _status(self, event: CalendarEvent) -> str:
        """Une vue publique ne doit pas présenter l'inscription de son auteur comme la tienne."""
        return event.status(self.author_id if self.private else None, self.clock())

    def _sync_controls(self) -> None:
        """La pagination et le menu d'activités n'occupent de place que s'ils sont utiles."""
        self.previous_period.disabled = self.state.contains(MIN_DATE)
        self.next_period.disabled = self.state.contains(MAX_DATE)
        self.go_today.disabled = self.state.contains(self.highlight_date) and self.state.page == 0
        self.previous_page.disabled = self.state.page == 0
        self.next_page.disabled = self.state.page + 1 >= self.page.pages
        self.switch_mode.label = "Semaine" if self.state.mode == "mois" else "Mois"
        self.switch_mode.emoji = "📋" if self.state.mode == "mois" else "📅"
        self.choose_event.options = [
            discord.SelectOption(
                label=one_line(event.title, 100), value=event.id,
                description=one_line(
                    f"{event.starts_at:%d/%m à %H:%M} · "
                    f"{len(event.participants)}/{event.capacity} inscrits · {self._status(event)}", 100,
                ),
            ) for event in self.page.events
        ] or [discord.SelectOption(label="Aucune activité sur cette page", value="empty")]
        self.choose_event.disabled = not bool(self.page.events)
        self.choose_event.placeholder = "Choisir une activité · Détails et inscription"
        self.choose_options.placeholder = f"Filtres et options · {FILTER_LABELS[self.state.filter]}"
        self.choose_options.options = [
            discord.SelectOption(
                label=FILTER_LABELS["toutes"], value="toutes", emoji="📅",
                description="Afficher toutes les activités de la période.",
                default=self.state.filter == "toutes",
            ),
            discord.SelectOption(
                label=FILTER_LABELS["inscrit"], value="inscrit", emoji="✅",
                description="Voir tes sorties dans un calendrier privé.",
                default=self.state.filter == "inscrit",
            ),
            discord.SelectOption(
                label=FILTER_LABELS["disponibles"], value="disponibles", emoji="🎟️",
                description="Afficher les sorties à venir qui ne sont pas complètes.",
                default=self.state.filter == "disponibles",
            ),
        ]
        if self.page.next_event is not None:
            self.choose_options.add_option(
                label="Prochaine activité", value="upcoming", emoji="⏭️",
                description="Aller à la prochaine sortie correspondant au filtre.",
            )
        self.choose_options.add_option(
            label="Actualiser", value="refresh", emoji="🔄",
            description="Relire les horaires, les inscriptions et les nouvelles sorties.",
        )
        self.choose_options.add_option(
            label="Aller à une date", value="date", emoji="🗓️",
            description="Choisir une date précise, seulement quand tu en as besoin.",
        )
        if not self.private:
            self.choose_options.add_option(
                label="Ouvrir en privé", value="private", emoji="🔒",
                description="Ouvrir ta propre copie, visible uniquement par toi.",
            )
        self.choose_options.add_option(
            label="Fermer le calendrier", value="close", emoji="✖️",
            description="Retirer les contrôles, sans supprimer d'activité.",
        )
        self.clear_items()
        for item in (
            self.previous_period, self.go_today, self.next_period, self.switch_mode,
        ):
            self.add_item(item)
        if self.page.events:
            self.add_item(self.choose_event)
        self.add_item(self.choose_options)
        if self.page.pages > 1:
            self.add_item(self.previous_page)
            self.add_item(self.next_page)

    def build_content(self) -> str:
        return f"Calendrier des activités · {self.state.title}"

    def build_file(self) -> discord.File:
        """Préserve l'ancien point d'entrée ; la production utilise le rendu asynchrone."""
        return discord.File(
            gen_cal(self.source(), None, self.year, self.month, self.highlight_date),
            filename="calendrier.png",
        )

    def build_embed(self) -> discord.Embed:
        count = len(self.page.period_events)
        now = self.clock()
        upcoming_count = sum(not event.has_started(now) for event in self.page.period_events)
        description = f"**{count} activité{'s' if count != 1 else ''}** · {upcoming_count} à venir"
        if self.state.filter != "toutes":
            description += f"\nFiltre : **{FILTER_LABELS[self.state.filter]}**"
        if count:
            description += "\nChoisis une activité ci-dessous pour les détails et l’inscription."
        elif self.state.filter != "toutes" and self.page.unfiltered_count:
            description += (
                "\n\nAucune activité ne correspond à ce filtre sur cette période.\n"
                "Choisis **Toutes les activités** dans **Filtres et options**."
            )
        elif self.page.next_event is not None:
            event = self.page.next_event
            description += (
                f"\n\nRien de prévu ici. Prochaine sortie le **{event.starts_at:%d/%m à %H:%M}**.\n"
                "Pour y aller : **Filtres et options → Prochaine activité**."
            )
        else:
            description += (
                "\n\nAucune activité prévue ici, ni à venir avec ce filtre.\n"
                "Pour proposer une sortie : `/activite creer`."
            )
        if self.snapshot.skipped:
            description += (
                f"\n⚠️ {self.snapshot.skipped} entrée(s) illisible(s). Le Staff peut vérifier les logs."
            )
        title = (
            f"📅 {self.state.title}" if self.state.mode == "mois"
            else f"📋 Semaine du {self.state.title}"
        )
        embed = discord.Embed(title=title, description=description, color=EMBED_COLOR)
        for event in self.page.events:
            day = (
                "Aujourd’hui" if event.day == self.highlight_date
                else f"{DAY_NAMES_FR[event.day.weekday()]} {event.starts_at:%d/%m}"
            )
            name = safe_text(f"{day} · {event.starts_at:%H:%M} — {one_line(event.title, 200)}", 256)
            value = (
                f"**{self._status(event)}** · {len(event.participants)}/{event.capacity} inscrits"
                f" · <t:{event.timestamp}:R>"
            )
            if event.description:
                value += f"\n{safe_text(one_line(event.description, 160), 200)}"
            embed.add_field(name=name, value=value, inline=False)
        visibility = "Vue privée" if self.private else "Vue publique · navigation réservée à l’auteur"
        pagination = f"Page {self.state.page + 1}/{self.page.pages} · " if self.page.pages > 1 else ""
        embed.set_footer(text=(
            f"{pagination}Horaires de Paris · Actualisé à {paris_time(now):%H:%M}\n"
            f"{visibility} · Expire après 10 min sans action"
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
                value="L’image nécessite « Joindre des fichiers ». La liste reste utilisable.",
                inline=False,
            )
        self._last_embed = embed
        return embed, files

    async def refresh_from_source(self):
        """Refresh an open session after a durable activity change, without a user click."""
        async with self._lock:
            if self.is_finished() or self.message is None:
                return
            files = []
            try:
                embed, files = await self.build_payload()
                retained = {item.filename: item for item in getattr(self.message, "attachments", ())}
                attachments = [retained.get(file.filename, file) for file in files]
                message = await self.message.edit(
                    content=None, embed=embed, attachments=attachments, view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if message is not None:
                    self.message = message
                self._last_embed = embed
                log.debug("Calendar: synchronized from activity store author_id=%s", self.author_id)
            finally:
                close_files(files)

    async def send_initial(self, sender: Callable[..., Awaitable[Any]]) -> None:
        """Partage l'envoi initial et son repli texte entre la commande et les copies privées."""
        files = []
        try:
            embed, files = await self.build_payload()
            try:
                message = await sender(
                    embed=embed, files=files, view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                raise
            except discord.HTTPException:
                if not files:
                    raise
                log.debug("Calendar: initial upload failed; retrying as text", exc_info=True)
                embed.set_image(url=None)
                embed.add_field(
                    name="Aperçu indisponible",
                    value="Discord a refusé l’image. La liste et les menus restent disponibles.",
                    inline=False,
                )
                message = await sender(
                    embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none(),
                )
            self.message = message
            self._last_embed = embed
            if self.registry is not None:
                self.registry.add(self)
        except BaseException:
            self.stop()
            raise
        finally:
            close_files(files)

    async def open_private(
        self, interaction: discord.Interaction, *, selected_filter: str | None = None,
    ) -> None:
        """Crée une session indépendante : aucune navigation ni inscription d'autrui n'est copiée."""
        if not await self._check_context(interaction):
            return
        state = replace(
            self.state,
            filter=selected_filter or self.state.filter,
            page=0 if selected_filter is not None else self.state.page,
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        private_view = CalendrierView(
            interaction.user, {}, source=self.source, state=state,
            guild=self.guild, renderer=self.renderer, action=self.action, clock=self.clock,
            attach_files=self.attach_files, attachment_limit=self.attachment_limit, private=True,
            registry=self.registry,
        )

        async def sender(**kwargs):
            return await interaction.followup.send(ephemeral=True, wait=True, **kwargs)

        await private_view.send_initial(sender)
        log.debug("Calendar: private copy opened user_id=%s source_user_id=%s",
                  interaction.user.id, self.author_id)

    async def change(self, interaction, transform: Callable[[CalendarState], CalendarState]) -> None:
        await interaction.response.defer()
        async with self._lock:
            if self.is_finished():
                await notify(interaction, "La session est terminée. Relance `/calendrier`.")
                return
            previous = (self.state, self.snapshot, self.page, self._last_embed)
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
                self.state, self.snapshot, self.page, self._last_embed = previous
                self._sync_controls()
                log.exception("Calendar: refresh failed user_id=%s", self.author_id)
                await notify(
                    interaction,
                    "Mise à jour impossible. Réessaie via **Filtres et options → Actualiser**.",
                )

    @discord.ui.button(label="Précédent", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def previous_period(self, interaction, button):
        await self.change(interaction, lambda state: state.step(-1))

    @discord.ui.button(label="Aujourd’hui", style=discord.ButtonStyle.primary, row=0)
    async def go_today(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, anchor=self.highlight_date, page=0))

    @discord.ui.button(label="Suivant", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def next_period(self, interaction, button):
        await self.change(interaction, lambda state: state.step(1))

    @discord.ui.button(label="Semaine", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def switch_mode(self, interaction, button):
        await self.change(
            interaction,
            lambda state: replace(
                state, mode="semaine" if state.mode == "mois" else "mois", page=0,
            ),
        )

    @discord.ui.select(placeholder="Choisir une activité · Détails et inscription", row=1)
    async def choose_event(self, interaction, select):
        event_id = select.values[0] if select.values else ""
        await interaction.response.defer(ephemeral=True, thinking=True)
        if snapshot_events(self.source()).get(event_id) is None:
            await notify(interaction, "Cette activité a été supprimée ou annulée. Actualise le calendrier.")
            return
        detail = ActivityDetailView(
            interaction.user.id, self.guild, self.source, event_id, self.action, self.clock,
        )
        try:
            embed = detail.build_embed()
            detail.message = await interaction.followup.send(
                embed=embed, view=detail, ephemeral=True, wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except BaseException:
            detail.stop()
            raise

    @discord.ui.select(placeholder="Filtres et options", row=2)
    async def choose_options(self, interaction, select):
        selected = select.values[0] if select.values else ""
        if selected == "private":
            await self.open_private(interaction)
        elif selected == "inscrit" and not self.private:
            await self.open_private(interaction, selected_filter="inscrit")
        elif selected in FILTERS:
            await self.change(interaction, lambda state: replace(state, filter=selected, page=0))
        elif selected == "refresh":
            await self.refresh(interaction)
        elif selected == "upcoming":
            await self.upcoming(interaction)
        elif selected == "date":
            await interaction.response.send_modal(CalendarDateModal(self))
        elif selected == "close":
            await self.finish(interaction)
        else:
            await notify(interaction, "Option inconnue. Relance `/calendrier`.")

    async def refresh(self, interaction) -> None:
        await self.change(interaction, lambda state: state)

    async def upcoming(self, interaction) -> None:
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

    @discord.ui.button(label="Activités précédentes", style=discord.ButtonStyle.secondary, row=3)
    async def previous_page(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, page=state.page - 1))

    @discord.ui.button(label="Activités suivantes", style=discord.ButtonStyle.secondary, row=3)
    async def next_page(self, interaction, button):
        await self.change(interaction, lambda state: replace(state, page=state.page + 1))


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
        """Met l'inscription au premier plan et masque les commandes techniques redondantes."""
        event = snapshot_events(self.source()).get(self.event_id)
        show_full_text = False
        if event is None:
            self.join.disabled = self.leave.disabled = True
            embed = discord.Embed(
                title="Activité indisponible",
                description="Elle a été annulée ou retirée. Actualise le calendrier.",
                color=EMBED_COLOR,
            )
        else:
            joined = self.author_id in event.participants or self.author_id in event.waitlist
            self.join.disabled = (
                self.action is None or event.has_started(self.clock()) or joined
            )
            self.leave.disabled = self.action is None or not joined or event.has_started(self.clock())
            self.join.label = (
                "Déjà commencée" if event.has_started(self.clock())
                else "Liste d’attente" if not event.places else "S’inscrire"
            )
            self.join.style = (
                discord.ButtonStyle.secondary if self.join.disabled else discord.ButtonStyle.success
            )
            embed = discord.Embed(
                title=safe_text(event.title, 256),
                description=safe_text(event.description or "Aucune description renseignée.", 2500),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="Rendez-vous",
                value=(f"**{event.starts_at:%d/%m/%Y à %H:%M} · Heure de Paris**\n"
                       f"Chez toi : <t:{event.timestamp}:F> · <t:{event.timestamp}:R>"),
                inline=False,
            )
            if event.location:
                embed.add_field(name="Lieu / rendez-vous", value=safe_text(event.location, 200), inline=False)
            if event.message_url:
                embed.add_field(name="Fiche de la sortie", value=f"[Ouvrir la fiche]({event.message_url})",
                                inline=False)
            if event.waitlist:
                embed.add_field(
                    name=f"Liste d'attente · {len(event.waitlist)}",
                    value=shorten("\n".join(self._member_name(uid) for uid in event.waitlist), 1000),
                    inline=False,
                )
            embed.add_field(name="Ton statut", value=event.status(self.author_id, self.clock()), inline=True)
            embed.add_field(name="Organisateur", value=self._member_name(event.creator_id), inline=True)
            members = "\n".join(self._member_name(user_id) for user_id in event.participants)
            embed.add_field(
                name=f"Participants · {len(event.participants)}/{event.capacity}",
                value=shorten(members, 1000) or "Aucun participant pour le moment.", inline=False,
            )
            for raw, limit in ((event.description, 2500), (event.title, 256)):
                escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(raw))
                show_full_text |= len(escaped.encode("utf-16-le")) > limit * 2
            if show_full_text:
                embed.add_field(
                    name="Texte abrégé",
                    value="Le bouton Texte complet donne accès à l’intégralité du contenu.",
                    inline=False,
                )
        self.full_text.disabled = event is None
        self.clear_items()
        if event is not None:
            self.add_item(self.leave if self.author_id in event.participants or self.author_id in event.waitlist else self.join)
        self.add_item(self.refresh)
        self.add_item(self.close)
        if show_full_text:
            self.add_item(self.full_text)
        embed.set_footer(text=f"ID : {self.event_id} · Fiche privée · Expire après 10 min sans action")
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

    @discord.ui.button(label="S’inscrire", style=discord.ButtonStyle.success, row=0)
    async def join(self, interaction, button):
        await self.update(interaction, "join")

    @discord.ui.button(label="Se désinscrire", style=discord.ButtonStyle.secondary, row=0)
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

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary, row=0)
    async def close(self, interaction, button):
        await self.finish(interaction)
