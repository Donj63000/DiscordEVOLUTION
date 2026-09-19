"""Vues temporaires liées à l'acteur réel. Les vues ne possèdent aucun client HTTP."""
from __future__ import annotations
import asyncio
import secrets
import time
from io import BytesIO
import discord
from .models import Actor, Profile, BuildError, SLOTS, canonical, values
from .catalog import search
from .import_export import parse_json, export_build
from .renderer import text_report
from .embeds import card, safe
from .editor import parse_stats, format_stats, integer, stat_name, SLOT_LABELS


class OwnerView(discord.ui.View):
    def __init__(self, cog, actor, *, timeout=600):
        super().__init__(timeout=timeout)
        self.cog, self.actor = cog, actor
        self.created = time.monotonic()
        self.message = None
        self.busy = False
        self.operation = "view:" + secrets.token_hex(16)
        cog.track(self)

    async def interaction_check(self, interaction):
        try:
            actual = self.cog.actor(interaction, self.actor.guild_id)
            if actual != self.actor or self.is_finished() or time.monotonic() - self.created > 600:
                raise BuildError("Cette vue ne t'appartient pas ou a expiré. Utilise /build ouvrir.")
            self.cog.ensure_ready()
            return True
        except BuildError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return False

    async def on_timeout(self):
        self.stop()
        if self.message:
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def stop(self):
        self.cog.untrack(self)
        super().stop()

    async def on_error(self, interaction, error, item):
        self.busy = False
        await self.cog.error(interaction, error)


class ConfirmView(OwnerView):
    def __init__(self, cog, actor, preview):
        self.preview = preview
        super().__init__(cog, actor)

    @discord.ui.button(label="Confirmer et enregistrer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if self.busy:
            await interaction.response.send_message("Confirmation déjà en cours.", ephemeral=True)
            return
        self.busy = True
        await interaction.response.defer(ephemeral=True)
        result = await self.cog.service.apply(self.actor, self.preview, self.operation)
        self.stop()
        await self.cog.show(interaction, *result)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Proposition annulée. Aucun changement enregistré.", embed=None, view=None)


class ActionConfirm(OwnerView):
    def __init__(self, cog, actor, callback, label="Confirmer"):
        self.callback = callback
        super().__init__(cog, actor)
        self.confirm.label = label

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.busy:
            await interaction.response.send_message("Confirmation déjà en cours.", ephemeral=True)
            return
        self.busy = True
        await interaction.response.defer(ephemeral=True)
        await self.callback(interaction, self.operation)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Action annulée.", embed=None, view=None)


class BuildView(OwnerView):
    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor)

    @discord.ui.button(label="Équipement", style=discord.ButtonStyle.primary, row=0)
    async def equipment(self, interaction, button):
        view = SlotsView(self.cog, self.actor, self.build)
        await interaction.response.send_message("Choisis l'emplacement à remplacer.", view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @discord.ui.button(label="Personnage", row=0)
    async def profile(self, interaction, button):
        from .panels import ProfileEditorView
        await interaction.response.defer(ephemeral=True)
        build, _, _ = await self.cog.service.inspect(self.actor, self.build.id)
        view = ProfileEditorView(self.cog, self.actor, build)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)

    @discord.ui.button(label="Détails / panoplies", row=0)
    async def details(self, interaction, button):
        from .panels import DetailsView
        await interaction.response.defer(ephemeral=True)
        build, report, catalog = await self.cog.service.inspect(self.actor, self.build.id)
        view = DetailsView(self.cog, self.actor, build, report, catalog)
        await self.cog.send_view(interaction, embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Historique / restaurer", row=2)
    async def history(self, interaction, button):
        from .panels import HistoryView
        await interaction.response.defer(ephemeral=True)
        current = await self.cog.repository.get(self.actor, self.build.id)
        rows = await self.cog.repository.revisions(self.actor, self.build.id)
        view = HistoryView(self.cog, self.actor, current, rows)
        await self.cog.send_view(interaction, content=("**Historique privé** — les 20 dernières révisions.\n"
            "Une restauration est prévisualisée puis enregistrée dans une NOUVELLE révision."
            if len(rows) > 1 else "Aucune ancienne révision conservée pour ce build."), view=view, ephemeral=True)

    @discord.ui.button(label="Image", row=0)
    async def image(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.send_image(interaction, self.actor, self.build.id)

    @discord.ui.button(label="Exporter JSON", row=1)
    async def export(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        build = await self.cog.repository.get(self.actor, self.build.id)
        await interaction.followup.send(file=discord.File(BytesIO(export_build(build)), filename="evolution-build.json"), ephemeral=True)

    @discord.ui.button(label="Partager", row=1)
    async def share(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.confirm_share(interaction, self.actor, self.build.id)

    @discord.ui.button(label="Recharger", row=1)
    async def refresh(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        self.stop()
        await self.cog.show(interaction, *(await self.cog.service.inspect(self.actor, self.build.id)))


class SlotsView(OwnerView):
    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor)
        select = discord.ui.Select(placeholder="Choisir un emplacement", options=[discord.SelectOption(label=SLOT_LABELS[s], value=s, description="Équipé" if build.in_slot(s) else "Emplacement vide") for s in SLOTS])
        async def chosen(interaction):
            from .panels import SlotActionsView
            await interaction.response.defer(ephemeral=True)
            current, _, catalog = await cog.service.inspect(actor, build.id)
            view = SlotActionsView(cog, actor, current, catalog, select.values[0])
            self.stop()
            await cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)
        select.callback = chosen
        self.add_item(select)


class GuardedModal(discord.ui.Modal):
    def __init__(self, cog, actor, title):
        super().__init__(title=title, timeout=300)
        self.cog, self.actor, self.created = cog, actor, time.monotonic()

    async def guard(self, interaction):
        if time.monotonic() - self.created > 300 or self.cog.actor(interaction, self.actor.guild_id) != self.actor:
            raise BuildError("Ce formulaire a expiré ou ne t'appartient pas.")
        self.cog.ensure_ready()

    async def on_error(self, interaction, error):
        await self.cog.error(interaction, error)


class SearchModal(GuardedModal):
    query = discord.ui.TextInput(label="Nom (vide pour parcourir les objets)", required=False, max_length=80)
    priority = discord.ui.TextInput(label="Trier par (ex. Force, Prospection, PA)", required=False, max_length=40)
    minimum = discord.ui.TextInput(label="Niveau minimum (facultatif)", required=False, max_length=3)

    def __init__(self, cog, actor, build, slot):
        self.build, self.slot = build, slot
        super().__init__(cog, actor, "Rechercher un équipement")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        _, _, catalog = await self.cog.service.inspect(self.actor, self.build.id)
        priority = stat_name(str(self.priority)) if str(self.priority).strip() else None
        minimum = integer(str(self.minimum) or "1", "Niveau minimum", 1, self.build.profile.level)
        results = search(catalog, str(self.query), self.slot, self.build.profile.level, priority=priority, minimum=minimum)
        if not results:
            raise BuildError("Aucun objet correspondant dans ce catalogue et ce niveau.")
        view = ItemResults(self.cog, self.actor, self.build, self.slot, results, catalog=catalog, query=str(self.query), priority=priority, minimum=minimum)
        await self.cog.send_view(interaction, content="Sélectionne un objet pour voir le nouveau total et les changements de panoplie.", view=view, ephemeral=True)


class ItemResults(OwnerView):
    def __init__(self, cog, actor, build, slot, items, *, catalog=None, query="", priority=None, minimum=1, page=0):
        self.build, self.slot, self.catalog = build, slot, catalog
        self.query, self.priority, self.minimum, self.page = query, priority, minimum, page
        super().__init__(cog, actor)
        select = discord.ui.Select(placeholder=f"Choisir un objet — page {page + 1}", row=0,
            options=[discord.SelectOption(label=i.name[:100], value=i.ref,
                description=(f"Niv. {i.level} · {i.category}" +
                    (f" · {sum(e.high for e in i.effects if e.kind == 'stat' and e.stat == priority):+d} {priority}" if priority else ""))[:100]) for i in items])
        async def chosen(interaction):
            await interaction.response.defer(ephemeral=True)
            preview = await cog.service.equipment(actor, build.id, build.revision, slot, select.values[0])
            await cog.show_preview(interaction, actor, preview)
        select.callback = chosen
        self.add_item(select)
        self.previous.disabled = catalog is None or page == 0
        self.next.disabled = catalog is None or not search(catalog, query, slot, build.profile.level,
            offset=(page + 1) * 25, priority=priority, minimum=minimum)

    @discord.ui.button(label="Précédent", row=1)
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Suivant", row=1)
    async def next(self, interaction, button):
        await self.turn(interaction, 1)

    @discord.ui.button(label="Changer la recherche", row=1)
    async def change(self, interaction, button):
        await interaction.response.send_modal(SearchModal(self.cog, self.actor, self.build, self.slot))

    async def turn(self, interaction, delta):
        from .panels import replace_view
        page = self.page + delta
        if page < 0 or self.catalog is None:
            raise BuildError("Page indisponible.")
        items = search(self.catalog, self.query, self.slot, self.build.profile.level,
                       offset=page * 25, priority=self.priority, minimum=self.minimum)
        if not items:
            raise BuildError("Fin des résultats.")
        self.stop()
        view = ItemResults(self.cog, self.actor, self.build, self.slot, items, catalog=self.catalog,
            query=self.query, priority=self.priority, minimum=self.minimum, page=page)
        await replace_view(interaction, view, content="Sélectionne un objet pour prévisualiser le remplacement complet.")


class ProfileModal(GuardedModal):
    classe = discord.ui.TextInput(label="Classe", max_length=20)
    level = discord.ui.TextInput(label="Niveau", max_length=3)
    allocated = discord.ui.TextInput(label="Points dépensés (ex. Chance: 300)", style=discord.TextStyle.paragraph, required=False, max_length=300)
    scrolled = discord.ui.TextInput(label="Parchottage (ex. Chance: 101)", style=discord.TextStyle.paragraph, required=False, max_length=300)
    naked = discord.ui.TextInput(label="Stats nues (ex. Chance: 101), sinon vide", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor, "Profil du personnage")
        p = build.profile
        self.classe.default, self.level.default = p.classe, str(p.level)
        self.allocated.default = format_stats(p.allocated)
        self.scrolled.default = format_stats(p.scrolled)
        self.naked.default = format_stats(p.naked_stats) if p.mode == "declared" else ""

    async def on_submit(self, interaction):
        from .conditions import norm
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        def stats(value):
            return parse_stats(value)
        declared = bool(str(self.naked).strip())
        if declared and any(stats(str(field)) for field in (self.allocated, self.scrolled)):
            raise BuildError("Les stats nues incluent déjà les points et le parcho : vider ces deux champs.")
        profile = Profile(classe=norm(str(self.classe)), level=integer(str(self.level), "Niveau", 1, 200),
                          allocated=stats(str(self.allocated)), scrolled=stats(str(self.scrolled)),
                          naked_stats=stats(str(self.naked)) if declared else (), mode="declared" if declared else "rules",
                          alignment=self.build.profile.alignment, grade=self.build.profile.grade)
        preview = await self.cog.service.profile(self.actor, self.build.id, self.build.revision, profile)
        await self.cog.show_preview(interaction, self.actor, preview)


class OptimizationView(OwnerView):
    def __init__(self, cog, actor, results, original):
        self.results, self.original = results, original
        super().__init__(cog, actor)
        choices = results.get("solutions", []) + results.get("tentative", [])
        select = discord.ui.Select(placeholder="Choisir une proposition", options=[discord.SelectOption(
            label=f"Proposition {i + 1} · score {r['score']}"[:100], value=str(i),
            description="Contraintes couvertes vérifiées" if r["constraints_verified"] else "Piste partielle NON CERTIFIÉE") for i, r in enumerate(choices)])
        async def chosen(interaction):
            from .models import Build
            await interaction.response.defer(ephemeral=True)
            candidate = Build.model_validate(choices[int(select.values[0])]["build"])
            preview = await cog.service.preview(actor, candidate, {"action": "optimizer", "id": original.id,
                                "revision": original.revision, "slots": candidate.model_dump(mode="json")["slots"]})
            await cog.show_preview(interaction, actor, preview)
        select.callback = chosen
        self.add_item(select)
