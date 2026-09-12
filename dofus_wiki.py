"""Je propose quatre recherches Rétro avec des fiches et une navigation guidée."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from io import BytesIO
import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from utils.dofus_wiki import (
    DofusWikiClient, INDEX_PATHS, WikiEntry, WikiError,
    equipment_category, equipment_suggestions, find_entries, search_key,
)
from utils.wiki_embeds import (
    display_text, enriched_item_sections, item_embeds, monster_embeds, recipe_embeds,
)
from utils.xixou_api import XixouClient
from utils.xixou_maps import XixouMapRenderer
from utils.wiki_images import WikiImageClient


log = logging.getLogger(__name__)
PAGE_SIZE = 10
MAX_QUANTITY = 10000
AUTO_CLIENT = object()
ITEM_SECTIONS = {
    "summary": "Résumé", "details": "Caractéristiques", "drops": "Drops",
    "zones": "Zones et carte", "harvest": "Récolte", "uses": "Utilisations",
}


@dataclass(frozen=True)
class ResultState:
    entries: tuple[WikiEntry, ...]
    action: str
    quantity: int
    title: str
    fuzzy: bool
    stale: bool
    page: int


def setting(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        log.debug("Wiki: invalid_setting name=%s fallback=%s", name, default)
        return default


class WikiView(discord.ui.View):
    """Je réserve la navigation à la personne qui a lancé la recherche."""

    def __init__(self, cog, owner_id: int):
        super().__init__(timeout=180)
        self.cog, self.owner_id = cog, owner_id
        self.message = None
        self.lock = asyncio.Lock()
        self._retired = False
        self.modals = set()
        cog.views.add(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Lance ta propre recherche avec /objet, /recette, /equipement ou /monstre.",
                ephemeral=True,
            )
            return False
        return True

    def stop(self):
        self._retired = True
        for modal in list(self.modals):
            modal.stop()
        self.cog.views.discard(self)
        super().stop()

    async def on_timeout(self):
        async with self.lock:
            if self._retired:
                return
            self.stop()
            for child in self.children:
                if not isinstance(child, discord.ui.Button) or child.url is None:
                    child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    log.debug("Wiki: expired_view_message_unavailable", exc_info=True)

    async def ensure_active(self, interaction):
        if self.is_finished() or self.cog._closed:
            await interaction.followup.send("Ce menu a expiré. Relance ta recherche.", ephemeral=True)
            return False
        return True

    async def send_page(self, sender, *, editing=False):
        options = {"attachments": []} if editing else {}
        return await sender(
            embed=self.embed(), view=self, allowed_mentions=discord.AllowedMentions.none(),
            **options,
        )

    async def publish(self, sender, *, editing=False, clear_attachments=False):
        """Je libère le menu si Discord ne peut pas le publier, même après une annulation."""
        try:
            if self.is_finished() or self.cog._closed:
                raise WikiError("L'encyclopédie redémarre. Relance ta recherche dans un instant.")
            self.message = await self.send_page(sender, editing=editing or clear_attachments)
        except BaseException:
            self.stop()
            log.debug("Wiki: view_publication_failed view=%s", type(self).__name__, exc_info=True)
            raise
        return self.message

    async def on_error(self, interaction, error, item):
        log.error("Wiki: component_failed component=%s", type(item).__name__, exc_info=error)
        sender = (
            interaction.followup.send if interaction.response.is_done()
            else interaction.response.send_message
        )
        await sender("La recherche n'a pas abouti. Relance la commande dans un instant.", ephemeral=True)


class ResultView(WikiView):
    def __init__(
        self, cog, owner_id, entries, action, quantity=1,
        *, title="Résultats", fuzzy=False, stale=False, page=0,
    ):
        super().__init__(cog, owner_id)
        self.entries, self.action, self.quantity = entries, action, quantity
        self.title, self.fuzzy, self.stale = title, fuzzy, stale
        self.page = max(0, min((len(entries) - 1) // PAGE_SIZE, page))
        self.select = discord.ui.Select(placeholder="Choisis la fiche à consulter", row=0)
        self.select.callback = self.choose
        self.add_item(self.select)
        self.previous = discord.ui.Button(label="Précédent", row=1)
        self.previous.callback = self.go_previous
        self.add_item(self.previous)
        self.next = discord.ui.Button(label="Suivant", row=1)
        self.next.callback = self.go_next
        self.add_item(self.next)
        self.refresh()

    def refresh(self):
        start = self.page * PAGE_SIZE
        self.select.options = [
            discord.SelectOption(label=entry.label, value=str(index))
            for index, entry in enumerate(self.entries[start:start + PAGE_SIZE], start)
        ]
        self.previous.disabled = self.page == 0
        self.next.disabled = start + PAGE_SIZE >= len(self.entries)

    def embed(self):
        visible = self.entries[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines = [
            f"**{display_text(entry.name, 120)}** · Niv. {display_text(entry.level, 30)}"
            f" · {display_text(entry.category, 60)}" for entry in visible
        ]
        prefix = (
            "Aucun nom exact trouvé. Voici les suggestions proches :\n\n" if self.fuzzy
            else "Choisis une fiche dans le menu ci-dessous.\n\n"
        )
        embed = discord.Embed(title=self.title[:256], description=prefix + "\n".join(lines), color=0x2479DB)
        count = (len(self.entries) + PAGE_SIZE - 1) // PAGE_SIZE
        footer = f"Wiki Dofus Rétro communautaire · Page {self.page + 1}/{count}"
        if self.stale:
            footer += " · Copie en cache"
        embed.set_footer(text=footer)
        return embed

    async def go_previous(self, interaction):
        await self.move(interaction, -1)

    async def go_next(self, interaction):
        await self.move(interaction, 1)

    async def move(self, interaction, direction):
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            previous_page = self.page
            self.page = max(0, min((len(self.entries) - 1) // PAGE_SIZE, self.page + direction))
            self.refresh()
            try:
                self.message = await interaction.edit_original_response(embed=self.embed(), view=self)
            except BaseException:
                self.page = previous_page
                self.refresh()
                raise

    def snapshot(self):
        return ResultState(tuple(self.entries), self.action, self.quantity, self.title,
                           self.fuzzy, self.stale, self.page)

    async def choose(self, interaction):
        selected = interaction.data.get("values", [])
        await interaction.response.defer()
        async with self.lock:
            try:
                if self.is_finished() or not selected or not selected[0].isdigit():
                    raise WikiError("Cette sélection a expiré. Relance la recherche.")
                index = int(selected[0])
                if not 0 <= index < len(self.entries):
                    raise WikiError("Cette sélection a expiré. Relance la recherche.")
                view = await self.cog.detail_view(
                    self.owner_id, self.entries[index], self.action, self.quantity,
                    results=self.snapshot(),
                )
            except WikiError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            if self.is_finished():
                view.stop()
                await self.ensure_active(interaction)
                return
            await view.publish(interaction.edit_original_response, editing=True)
            self.stop()


class DetailView(WikiView):
    def __init__(
        self, cog, owner_id, detail, action, quantity=1, *, results=None, enrichment=None,
        item_image=None,
    ):
        self.detail, self.action, self.quantity = detail, action, quantity
        self.results = results
        self.item_image = item_image
        self.enrichment = enrichment
        self.sections = {}
        if enrichment is not None and detail.entry.kind == "item":
            try:
                self.sections = enriched_item_sections(detail, enrichment)
            except Exception as exc:
                log.debug("Wiki: enrichment_display_unavailable error=%s", type(exc).__name__)
                self.enrichment = None
        self.section = "summary"
        self.section_positions = dict.fromkeys(self.sections, 0)
        self.map_link = None
        self.page = 0
        self.pages = self.build_pages()
        super().__init__(cog, owner_id)
        self.previous = discord.ui.Button(label="Précédent", row=0)
        self.previous.callback = self.go_previous
        self.add_item(self.previous)
        self.next = discord.ui.Button(label="Suivant", row=0)
        self.next.callback = self.go_next
        self.add_item(self.next)
        if detail.entry.kind == "item":
            self.toggle = discord.ui.Button(
                label="Voir l'objet" if action == "recipe" else "Voir la recette",
                style=discord.ButtonStyle.primary, row=0,
            )
            self.toggle.callback = self.toggle_action
            self.add_item(self.toggle)
            self.quantity_button = discord.ui.Button(label="Modifier la quantité", row=0)
            self.quantity_button.callback = self.open_quantity
            self.add_item(self.quantity_button)
        if results is not None:
            self.back = discord.ui.Button(label="Retour aux résultats", row=1)
            self.back.callback = self.return_to_results
            self.add_item(self.back)
        self.add_item(discord.ui.Button(label="Ouvrir le wiki", url=detail.entry.url, row=1))
        if self.sections or (item_image is not None
                             and item_image.source_url.startswith("https://xixou.io/")):
            self.add_item(discord.ui.Button(label="Source Xixou", url="https://xixou.io", row=1))
        if self.sections:
            self.section_select = discord.ui.Select(
                placeholder="Choisis une rubrique", row=2,
                options=[discord.SelectOption(label=label, value=key)
                         for key, label in ITEM_SECTIONS.items()],
            )
            self.section_select.callback = self.choose_section
            self.add_item(self.section_select)
        self.refresh()

    def build_pages(self):
        if self.action == "recipe":
            return recipe_embeds(self.detail, self.quantity)
        if self.action == "monster":
            return monster_embeds(self.detail)
        if self.sections:
            return [page.embed for page in self.sections[self.section]]
        return item_embeds(self.detail)

    def current_item_page(self):
        if self.action == "item" and self.sections:
            return self.sections[self.section][self.page]
        return None

    def refresh(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page + 1 >= len(self.pages)
        if hasattr(self, "quantity_button"):
            self.quantity_button.disabled = self.action != "recipe"
            self.toggle.label = "Voir l'objet" if self.action == "recipe" else "Voir la recette"
        if self.sections:
            self.section_select.disabled = self.action != "item"
            for option in self.section_select.options:
                option.default = option.value == self.section
            if self.map_link is not None:
                self.remove_item(self.map_link)
                self.map_link = None
            page = self.current_item_page()
            if page is not None and page.map_url:
                self.map_link = discord.ui.Button(
                    label="Explorer la carte", url=page.map_url, row=1,
                )
                self.add_item(self.map_link)
        if self._retired:
            for child in self.children:
                if not getattr(child, "url", None):
                    child.disabled = True

    def embed(self):
        return self.pages[self.page]

    async def go_previous(self, interaction):
        await self.move(interaction, -1)

    async def go_next(self, interaction):
        await self.move(interaction, 1)

    async def move(self, interaction, direction):
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            page = max(0, min(len(self.pages) - 1, self.page + direction))
            await self.change_page(interaction, page=page)

    async def choose_section(self, interaction):
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            selected = interaction.data.get("values", [])
            if self.action != "item" or len(selected) != 1 or selected[0] not in self.sections:
                await interaction.followup.send(
                    "Cette rubrique n'est pas disponible. Reviens à la fiche objet.", ephemeral=True,
                )
                return
            section = selected[0]
            await self.change_page(
                interaction, section=section, page=self.section_positions[section],
            )

    async def edit_page(self, interaction):
        """Je remplace ensemble la fiche et sa carte, avec repli sur le texte."""
        page = self.current_item_page()
        rendered = None
        if page is not None and page.map_spec is not None:
            try:
                rendered = await self.cog.map_renderer.render(page.map_spec)
            except Exception as exc:
                log.debug("Wiki: map_render_unavailable error=%s", type(exc).__name__)
        if not await self.ensure_active(interaction):
            return False
        self.message = await self.send_page(
            interaction.edit_original_response, editing=True, map_data=rendered,
        )
        return True

    async def send_page(self, sender, *, editing=False, map_data=None):
        """Je joins séparément l'objet et sa carte et je garde une miniature distante en secours."""
        fallback = self.embed().copy()
        if self.item_image is not None:
            fallback.set_thumbnail(url=self.item_image.source_url)
            if self.item_image.stale:
                fallback.set_footer(text=(fallback.footer.text or "") + " · Illustration en cache")
        embed = fallback.copy()
        attachments = []
        if self.item_image is not None:
            attachments.append(discord.File(BytesIO(self.item_image.data), filename="objet.png"))
            embed.set_thumbnail(url="attachment://objet.png")
        if map_data:
            attachments.append(discord.File(BytesIO(map_data), filename="carte-xixou.png"))
            embed.set_image(url="attachment://carte-xixou.png")
        options = {
            "embed": embed, "view": self, "allowed_mentions": discord.AllowedMentions.none(),
        }
        if editing:
            options["attachments"] = attachments
        elif attachments:
            options["files"] = attachments
        try:
            try:
                return await sender(**options)
            except discord.HTTPException:
                if not attachments or self.is_finished() or self.cog._closed:
                    raise
                log.debug("Wiki: attachment_unavailable fallback=remote_thumbnail editing=%s", editing)
                options["embed"] = fallback
                options.pop("files", None)
                if editing:
                    options["attachments"] = []
                return await sender(**options)
        finally:
            for attachment in attachments:
                attachment.close()
                attachment.fp.close()

    async def change_page(
        self, interaction, *, action=None, pages=None, quantity=None, section=None, page=0,
    ):
        """Je conserve l'état visible si la publication échoue ou si le menu expire."""
        previous = (
            self.action, self.pages, self.page, self.quantity, self.section,
            self.section_positions.copy(),
        )
        try:
            if self.action == "item" and self.sections:
                self.section_positions[self.section] = self.page
            self.action = self.action if action is None else action
            self.quantity = self.quantity if quantity is None else quantity
            self.section = self.section if section is None else section
            if pages is not None:
                self.pages = pages
            elif action is not None or quantity is not None or section is not None:
                self.pages = self.build_pages()
            self.page = max(0, min(len(self.pages) - 1, page))
            self.refresh()
            changed = await self.edit_page(interaction)
        except BaseException:
            self.restore_page(previous)
            raise
        if not changed:
            self.restore_page(previous)
        elif self.action == "item" and self.sections:
            self.section_positions[self.section] = self.page
        log.debug("Wiki: detail_navigation action=%s section=%s page=%s changed=%s",
                  self.action, self.section, self.page, changed)

    def restore_page(self, previous):
        (self.action, self.pages, self.page, self.quantity,
         self.section, self.section_positions) = previous
        self.refresh()

    async def toggle_action(self, interaction):
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            action = "item" if self.action == "recipe" else "recipe"
            try:
                pages = recipe_embeds(self.detail, self.quantity) if action == "recipe" else (
                    [page.embed for page in self.sections[self.section]] if self.sections
                    else item_embeds(self.detail)
                )
            except WikiError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            page = self.section_positions.get(self.section, 0) if action == "item" else 0
            await self.change_page(interaction, action=action, pages=pages, page=page)

    async def update_detail(self, interaction, action, pages, quantity):
        await self.change_page(interaction, action=action, pages=pages, quantity=quantity)

    async def return_to_results(self, interaction):
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            state = replace(self.results, quantity=self.quantity)
            view = ResultView(
                self.cog, self.owner_id, state.entries, state.action, state.quantity,
                title=state.title, fuzzy=state.fuzzy, stale=state.stale, page=state.page,
            )
            await view.publish(interaction.edit_original_response, clear_attachments=True)
            self.stop()
            log.debug("Wiki: results_restored page=%s quantity=%s", state.page, state.quantity)

    async def open_quantity(self, interaction):
        if self.is_finished():
            await interaction.response.send_message("Ce menu a expiré. Relance ta recherche.", ephemeral=True)
            return
        modal = RecipeQuantityModal(self)
        try:
            await interaction.response.send_modal(modal)
        except BaseException:
            modal.stop()
            raise


class RecipeQuantityModal(discord.ui.Modal, title="Quantité à fabriquer"):
    def __init__(self, view):
        super().__init__(timeout=180)
        self.view = view
        self.quantity = discord.ui.TextInput(
            label="Nombre d'exemplaires (1 à 10 000)", default=str(view.quantity),
            min_length=1, max_length=5,
        )
        self.add_item(self.quantity)
        view.modals.add(self)

    def stop(self):
        self.view.modals.discard(self)
        super().stop()

    async def on_timeout(self):
        self.stop()

    async def interaction_check(self, interaction):
        return await self.view.interaction_check(interaction)

    async def on_submit(self, interaction):
        await interaction.response.defer()
        try:
            async with self.view.lock:
                if not await self.view.ensure_active(interaction):
                    return
                value = self.quantity.value.strip()
                if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= MAX_QUANTITY:
                    await interaction.followup.send(
                        "La quantité doit être comprise entre 1 et 10 000.", ephemeral=True,
                    )
                    return
                quantity = int(value)
                try:
                    pages = recipe_embeds(self.view.detail, quantity)
                except WikiError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                await self.view.update_detail(interaction, "recipe", pages, quantity)
                log.debug("Wiki: recipe_quantity_changed quantity=%s", quantity)
        finally:
            self.stop()

    async def on_error(self, interaction, error):
        await self.view.on_error(interaction, error, self)


class DofusWikiCog(commands.Cog):
    def __init__(
        self, bot, *, client=None, enrichment_client=AUTO_CLIENT, map_renderer=None,
        image_client=AUTO_CLIENT,
    ):
        self.bot = bot
        self.client = client or DofusWikiClient(
            ttl=setting("DOFUS_WIKI_CACHE_TTL", 3600),
            timeout=setting("DOFUS_WIKI_TIMEOUT", 10),
        )
        self.enrichment_client = (
            XixouClient(
                api_key=os.getenv("XIXOU_API_KEY", ""),
                ttl=setting("XIXOU_CACHE_TTL", 3600),
                timeout=setting("XIXOU_TIMEOUT", 10),
            ) if enrichment_client is AUTO_CLIENT else enrichment_client
        )
        self.map_renderer = map_renderer if map_renderer is not None else XixouMapRenderer()
        self.image_client = WikiImageClient() if image_client is AUTO_CLIENT else image_client
        self.views: set[WikiView] = set()
        self._warmup = None
        self._enrichment_warmup = None
        self._closed = False

    async def cog_load(self):
        self.start_warmup()
        if self.enrichment_client is not None and self.enrichment_client.enabled:
            self._enrichment_warmup = asyncio.create_task(self.enrichment_client.warmup())

    def start_warmup(self):
        if self._closed:
            return
        if self._warmup is None or self._warmup.done():
            self._warmup = asyncio.create_task(self.client.warmup())

    async def cog_unload(self):
        self._closed = True
        warmups = [task for task in (self._warmup, self._enrichment_warmup) if task is not None]
        for task in warmups:
            task.cancel()
        await asyncio.gather(*warmups, return_exceptions=True)
        for view in list(self.views):
            view.stop()
        clients = [self.client, self.map_renderer]
        if self.enrichment_client is not None:
            clients.append(self.enrichment_client)
        if self.image_client is not None:
            clients.append(self.image_client)
        outcomes = await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                log.debug("Wiki: client_close_failed error=%s", type(outcome).__name__)

    async def autocomplete(self, kind: str, current: str):
        if kind == "wiki_types":
            return [app_commands.Choice(name=name, value=name)
                    for name in equipment_suggestions(current)]
        path = INDEX_PATHS[1 if kind == "wiki_monsters" else 0]
        entries = self.client.peek(path) or ()
        if self.client.is_stale(path):
            self.start_warmup()
        if not entries and current.strip() and len(current) <= 100:
            return [app_commands.Choice(
                name=f"Catalogue en cours de chargement · Rechercher « {current} »"[:100],
                value=current,
            )]
        matches, _ = find_entries(entries, current, 25)
        return [app_commands.Choice(name=entry.label, value=entry.token)
                for entry in matches if len(entry.token) <= 100]

    async def detail_view(self, owner_id, entry, action, quantity=1, *, results=None):
        if self._closed:
            raise WikiError("L'encyclopédie redémarre. Relance ta recherche dans un instant.")
        detail = await self.client.detail(entry)
        if self._closed:
            raise WikiError("L'encyclopédie redémarre. Relance ta recherche dans un instant.")
        enrichment = None
        if (entry.kind == "item" and self.enrichment_client is not None
                and self.enrichment_client.enabled):
            try:
                async with asyncio.timeout(12):
                    enrichment = await self.enrichment_client.enrich(detail)
            except Exception as exc:
                log.debug("Wiki: enrichment_unavailable error=%s", type(exc).__name__)
        if self._closed:
            raise WikiError("L'encyclopédie redémarre. Relance ta recherche dans un instant.")
        item_image = None
        if entry.kind == "item" and self.image_client is not None:
            item_image = await self.resolve_item_image(detail, enrichment)
            detail = replace(detail, icon=item_image.source_url if item_image is not None else None)
        if self._closed:
            raise WikiError("L'encyclopédie redémarre. Relance ta recherche dans un instant.")
        return DetailView(
            self, owner_id, detail, action, quantity, results=results, enrichment=enrichment,
            item_image=item_image,
        )

    async def resolve_item_image(self, detail, enrichment):
        """Je résous l'illustration sans retarder indéfiniment la consultation de l'objet."""
        try:
            async with asyncio.timeout(5):
                candidates = enrichment.image_candidates if enrichment is not None else ()
                resolved = await self.image_client.resolve(candidates) if candidates else None
                if resolved is not None or self._closed:
                    return resolved
                moon_icon = await self.client.item_icon(detail.entry)
                if self._closed:
                    return None
                candidates = tuple(dict.fromkeys(url for url in (moon_icon, detail.icon) if url))
                return await self.image_client.resolve(candidates) if candidates else None
        except Exception as exc:
            log.debug("Wiki: item_image_unavailable error=%s", type(exc).__name__)
            return None

    async def search(self, ctx, query, action, quantity=1):
        if not query or not query.strip():
            names = {"item": "objet", "recipe": "recette", "monster": "monstre"}
            command = names[action]
            if getattr(ctx, "interaction", None) is not None:
                message = f"Utilise `/{command}` et choisis un nom dans les suggestions."
            else:
                example = "Bouftou Royal" if action == "monster" else "Gelano"
                message = f"Indique un nom : `!{command} {example}`."
                if action == "recipe":
                    message += " Pour plusieurs fabrications : `!recette 3 Gelano`."
            await ctx.send(message)
            return
        if len(query) > 250:
            await ctx.send("Indique un nom de 250 caractères maximum.")
            return
        log.debug("Wiki: search action=%s quantity=%s", action, quantity)
        try:
            entries = await (self.client.monsters() if action == "monster" else self.client.items())
            matches, fuzzy = find_entries(entries, query)
            if not matches:
                await ctx.send("Aucune fiche trouvée. Essaie un nom plus court ou les suggestions de la commande /.")
                return
            if len(matches) == 1 and not fuzzy:
                view = await self.detail_view(ctx.author.id, matches[0], action, quantity)
            else:
                path = INDEX_PATHS[1 if action == "monster" else 0]
                view = ResultView(self, ctx.author.id, matches, action, quantity,
                                  fuzzy=fuzzy, stale=self.client.is_stale(path))
            await view.publish(ctx.send)
        except WikiError as exc:
            log.debug("Wiki: search_failed action=%s reason=%s", action, exc)
            await ctx.send(str(exc), allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="objet")
    @commands.guild_only()
    @commands.cooldown(4, 10, commands.BucketType.user)
    async def objet_command(self, ctx, *, nom: str = ""):
        """Je consulte les caractéristiques d'un objet Rétro."""
        await self.search(ctx, nom, "item")

    @commands.command(name="recette")
    @commands.guild_only()
    @commands.cooldown(4, 10, commands.BucketType.user)
    async def recette_command(self, ctx, *, recherche: str = ""):
        """Je calcule une recette avec !recette [quantité] nom de l'objet."""
        match = re.fullmatch(r"([+-]?\d+)\s+(.+)", recherche.strip(), flags=re.DOTALL)
        quantity, query = (int(match[1]), match[2]) if match else (1, recherche)
        if not 1 <= quantity <= MAX_QUANTITY:
            await ctx.send("La quantité doit être comprise entre 1 et 10 000.")
            return
        await self.search(ctx, query, "recipe", quantity)

    @commands.command(name="monstre")
    @commands.guild_only()
    @commands.cooldown(4, 10, commands.BucketType.user)
    async def monstre_command(self, ctx, *, nom: str = ""):
        """Je consulte les statistiques et résistances du monstre sélectionné."""
        await self.search(ctx, nom, "monster")

    @commands.command(name="equipement")
    @commands.guild_only()
    @commands.cooldown(4, 10, commands.BucketType.user)
    async def equipement_command(
        self, ctx, categorie: str = "", niveau: int = 200, niveau_min: int = 1, *, nom: str = "",
    ):
        """Je cherche un type d'équipement jusqu'au niveau demandé."""
        if not categorie:
            await ctx.send("Utilise `/equipement type:Chapeau niveau:100` ou `!equipement coiffe 100`.")
            return
        try:
            category = equipment_category(categorie)
            if not 1 <= niveau <= 200:
                raise WikiError("Le niveau maximum doit être compris entre 1 et 200.")
            if not 1 <= niveau_min <= niveau:
                raise WikiError("Le niveau minimum doit être compris entre 1 et le niveau maximum.")
            if len(nom) > 250:
                raise WikiError("Indique un nom de 250 caractères maximum.")
            words = search_key(nom).split()
            entries = await self.client.items()
            matches = sorted(
                (entry for entry in entries if entry.category == category
                 and type(entry.level) is int and niveau_min <= entry.level <= niveau
                 and all(word in entry.key for word in words)),
                key=lambda entry: (-entry.level, entry.key, entry.identifier),
            )
            if not matches:
                await ctx.send("Aucun équipement trouvé pour ce type, ces niveaux et ce nom.")
                return
            view = ResultView(self, ctx.author.id, matches, "item",
                              title=f"{category} · Niveau {niveau_min} à {niveau}",
                              stale=self.client.is_stale(INDEX_PATHS[0]))
            await view.publish(ctx.send)
            log.debug("Wiki: equipment_list category=%s level=%s matches=%s", category, niveau, len(matches))
        except WikiError as exc:
            await ctx.send(str(exc), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(DofusWikiCog(bot))
