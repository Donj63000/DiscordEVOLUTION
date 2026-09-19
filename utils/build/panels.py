"""Parcours guidés Discord. Tous les changements passent par BuildService.

Les formulaires ne sont jamais des sauvegardes implicites ; ils préparent une
modification calculée, puis le propriétaire confirme la révision affichée.
"""
from __future__ import annotations
from io import BytesIO

import discord

from .models import Profile, PRIMARY, STAT_LABELS, SLOTS, BuildError, revised, values, as_stats
from .conditions import norm
from .editor import (integer, stat_name, update_capital, edit_jets, SLOT_LABELS)
from .embeds import safe
from .renderer import metric_text
from .views import OwnerView, GuardedModal, ProfileModal, SearchModal


async def replace_view(interaction, view, *, content=None, embed=None):
    """Réutilise le message courant, sans accumuler les écrans périmés."""
    try:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
        view.message = await interaction.original_response()
    except Exception:
        view.stop()
        raise


class BuildListView(OwnerView):
    def __init__(self, cog, actor, builds, page=0):
        self.builds, self.page = tuple(builds), page
        super().__init__(cog, actor)
        page_rows = self.builds[page * 25:(page + 1) * 25]
        if page_rows:
            select = discord.ui.Select(placeholder="Ouvrir un de mes builds", row=0,
                options=[discord.SelectOption(label=b.name[:100], value=b.id,
                         description=f"{b.profile.classe.capitalize()} · niveau {b.profile.level} · révision {b.revision}") for b in page_rows])
            async def choose(interaction):
                await interaction.response.defer(ephemeral=True)
                self.stop()
                await cog.show(interaction, *(await cog.service.inspect(actor, select.values[0])))
            select.callback = choose
            self.add_item(select)
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= len(self.builds)

    def content(self):
        return (f"**Mes builds — {len(self.builds)} personnage(s)**\n"
                "Sélectionne un build pour l'ouvrir, ou crée ton premier personnage.\n"
                f"Page {self.page + 1}/{max(1, (len(self.builds) + 24) // 25)} · aucun appel IA.")

    @discord.ui.button(label="Nouveau personnage", style=discord.ButtonStyle.success, row=1)
    async def create(self, interaction, button):
        await interaction.response.send_modal(CreateModal(self.cog, self.actor))

    @discord.ui.button(label="Précédent", row=1)
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Suivant", row=1)
    async def next(self, interaction, button):
        await self.turn(interaction, 1)

    async def turn(self, interaction, delta):
        self.stop()
        view = BuildListView(self.cog, self.actor, self.builds, self.page + delta)
        await replace_view(interaction, view, content=view.content())


class CreateModal(GuardedModal):
    name = discord.ui.TextInput(label="Nom du build", placeholder="Mon Enutrof prospection", max_length=80)
    classe = discord.ui.TextInput(label="Classe", placeholder="Enutrof", max_length=20)
    level = discord.ui.TextInput(label="Niveau", placeholder="200", max_length=3)

    def __init__(self, cog, actor):
        super().__init__(cog, actor, "Créer mon personnage")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.guard(interaction)
        profile = Profile(classe=norm(str(self.classe)), level=integer(str(self.level), "Niveau", 1, 200))
        result = await self.cog.service.new(self.actor, str(self.name), profile, operation=f"modal:{interaction.id}")
        await self.cog.show(interaction, *result)


class ProfileEditorView(OwnerView):
    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor)
        select = discord.ui.Select(placeholder="Modifier capital et parchottage d'une caractéristique", row=0,
            options=[discord.SelectOption(label=STAT_LABELS[stat], value=stat) for stat in PRIMARY],
            disabled=build.profile.mode != "rules")
        async def choose(interaction):
            await interaction.response.send_modal(CapitalModal(cog, actor, build, select.values[0]))
        select.callback = choose
        self.add_item(select)

    def content(self):
        allocated = sum(value.value for value in self.build.profile.allocated)
        capital = 5 * (self.build.profile.level - 1)
        return (f"**Personnage — {safe(self.build.name, 80)}**\n"
                f"{self.build.profile.classe.capitalize()} · niveau {self.build.profile.level}\n"
                + (f"Capital : **{allocated}/{capital} points dépensés**. Les paliers calculent le gain réel.\n"
                   if self.build.profile.mode == "rules" else "Mode : statistiques nues déclarées (hors équipement).\n")
                + "Choisis une caractéristique puis saisis simplement deux nombres. Rien n'est modifié avant confirmation.\n"
                  "Les bases non validées restent signalées, même si le formulaire est entièrement rempli.")

    @discord.ui.button(label="Classe et niveau", row=1)
    async def identity(self, interaction, button):
        await interaction.response.send_modal(IdentityModal(self.cog, self.actor, self.build))

    @discord.ui.button(label="Saisie groupée / stats nues", row=1)
    async def advanced(self, interaction, button):
        await interaction.response.send_modal(ProfileModal(self.cog, self.actor, self.build))

    @discord.ui.button(label="Retour au build", row=1)
    async def back(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        self.stop()
        await self.cog.show(interaction, *(await self.cog.service.inspect(self.actor, self.build.id)))


class IdentityModal(GuardedModal):
    classe = discord.ui.TextInput(label="Classe", max_length=20)
    level = discord.ui.TextInput(label="Niveau", max_length=3)

    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor, "Classe et niveau")
        self.classe.default, self.level.default = build.profile.classe, str(build.profile.level)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        profile = revised(self.build.profile, classe=norm(str(self.classe)), level=integer(str(self.level), "Niveau", 1, 200))
        preview = await self.cog.service.profile(self.actor, self.build.id, self.build.revision, profile)
        await self.cog.show_preview(interaction, self.actor, preview)


class CapitalModal(GuardedModal):
    spent = discord.ui.TextInput(label="Points de capital DÉPENSÉS", max_length=4)
    scroll = discord.ui.TextInput(label="Parchottage (0 à 101)", max_length=3)

    def __init__(self, cog, actor, build, stat):
        self.build, self.stat = build, stat
        super().__init__(cog, actor, f"Modifier {STAT_LABELS[stat]}")
        self.spent.default = str(as_stats(build.profile.allocated).get(stat, 0))
        self.scroll.default = str(as_stats(build.profile.scrolled).get(stat, 0))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        profile = update_capital(self.build.profile, self.stat,
            integer(str(self.spent), "Capital dépensé", 0, 995), integer(str(self.scroll), "Parchottage", 0, 101))
        preview = await self.cog.service.profile(self.actor, self.build.id, self.build.revision, profile)
        await self.cog.show_preview(interaction, self.actor, preview)


class SlotActionsView(OwnerView):
    def __init__(self, cog, actor, build, catalog, slot):
        self.build, self.catalog, self.slot = build, catalog, slot
        super().__init__(cog, actor)
        self.remove.disabled = self.jets.disabled = build.in_slot(slot) is None

    def content(self):
        instance = self.build.in_slot(self.slot)
        item = self.catalog.resolve(instance) if instance else None
        text = f"**{SLOT_LABELS[self.slot]}** — " + (safe(item.name, 100) if item else "emplacement vide")
        if item:
            text += f"\nNiveau {item.level} · {item.category}\n"
            finals = {v.ref: v.value for v in instance.final_values}
            lines = []
            for effect in item.effects:
                if effect.kind == "stat":
                    lines.append(f"{STAT_LABELS[effect.stat]} : **{finals.get(effect.ref, effect.high)}** (naturel {effect.low} à {effect.high})")
                elif effect.text:
                    lines.append(safe(effect.text, 130))
            text += "\n".join(lines[:12])
            if len(lines) > 12:
                text += f"\n… {len(lines) - 12} lignes supplémentaires dans Détails."
            text += "\nLes jets affichés ne prouvent pas la possession ni la réalisabilité de l'objet."
        return text[:1900]

    @discord.ui.button(label="Rechercher / remplacer", style=discord.ButtonStyle.primary, row=0)
    async def find(self, interaction, button):
        await interaction.response.send_modal(SearchModal(self.cog, self.actor, self.build, self.slot))

    @discord.ui.button(label="Modifier les jets", row=0)
    async def jets(self, interaction, button):
        instance = self.build.in_slot(self.slot)
        if instance is None:
            raise BuildError("Emplacement vide.")
        view = JetsEditorView(self.cog, self.actor, self.build, self.catalog.resolve(instance), self.slot, instance)
        self.stop()
        await replace_view(interaction, view, content=view.content())

    @discord.ui.button(label="Retirer", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        preview = await self.cog.service.remove(self.actor, self.build.id, self.build.revision, self.slot)
        await self.cog.show_preview(interaction, self.actor, preview)


class JetsEditorView(OwnerView):
    def __init__(self, cog, actor, build, template, slot, instance, page=0):
        self.build, self.template, self.slot = build, template, slot
        self.instance, self.page = instance, page
        self.draft_revision = 0
        self.effects = tuple(e for e in template.effects if e.kind == "stat")
        super().__init__(cog, actor)
        select = discord.ui.Select(placeholder="Mode de jets", row=0, options=[
            discord.SelectOption(label="Jets naturels personnalisés", value="natural_custom", default=instance.mode != "declared_fm"),
            discord.SelectOption(label="FM déclarée (over / exos)", value="declared_fm", default=instance.mode == "declared_fm")])
        async def mode(interaction):
            candidate = revised(self.instance, mode="declared_fm", final_values=self.instance.final_values)
            candidate = edit_jets(self.template, candidate, {})
            if select.values[0] == "natural_custom":
                candidate = revised(candidate, mode="natural_custom", extras=())
                candidate = edit_jets(self.template, candidate, {})  # refuse un over encore présent
            self.instance = candidate
            self.draft_revision += 1
            self.sync_mode()
            await interaction.response.edit_message(content=self.content(), view=self)
        select.callback = mode
        self.mode_select = select
        self.add_item(select)
        self.edit.disabled = not self.effects
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 5 >= len(self.effects)

    def sync_mode(self):
        for option in self.mode_select.options:
            option.default = option.value == ("declared_fm" if self.instance.mode == "declared_fm" else "natural_custom")

    def content(self):
        mode = "FM déclarée : réalisation non certifiée" if self.instance.mode == "declared_fm" else "Jets naturels"
        current = {v.ref: v.value for v in self.instance.final_values}
        rows = [f"{STAT_LABELS[e.stat]} : **{current.get(e.ref, e.high)}** · naturel {e.low} à {e.high}"
                for e in self.effects[self.page * 5:(self.page + 1) * 5]]
        extra = ", ".join(f"{STAT_LABELS[v.stat]} {v.value:+d}" for v in self.instance.extras) or "aucun"
        return (f"**Jets — {safe(self.template.name, 100)}**\n{mode}\n"
                f"Page {self.page + 1}/{max(1, (len(self.effects) + 4) // 5)}\n"
                + "\n".join(rows) + f"\nExos : {safe(extra, 500)}\n"
                "**Brouillon local non enregistré.** Termine par Aperçu et confirmer.")

    @discord.ui.button(label="Saisir ces jets", style=discord.ButtonStyle.primary, row=1)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(JetsModal(self.cog, self.actor, self))

    @discord.ui.button(label="Exo + / −", row=1)
    async def extra(self, interaction, button):
        if self.instance.mode != "declared_fm":
            raise BuildError("Sélectionne d'abord FM déclarée pour modifier un exo.")
        await interaction.response.send_modal(ExoModal(self.cog, self.actor, self))

    @discord.ui.button(label="Précédent", row=2)
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Suivant", row=2)
    async def next(self, interaction, button):
        await self.turn(interaction, 1)

    async def turn(self, interaction, delta):
        self.stop()
        view = JetsEditorView(self.cog, self.actor, self.build, self.template, self.slot, self.instance, self.page + delta)
        await replace_view(interaction, view, content=view.content())

    @discord.ui.button(label="Meilleurs jets naturels", row=3)
    async def reset(self, interaction, button):
        self.instance = revised(self.instance, mode="natural_best", final_values=(), extras=())
        self.draft_revision += 1
        self.sync_mode()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Aperçu et confirmer", style=discord.ButtonStyle.success, row=3)
    async def apply(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        preview = await self.cog.service.jets(self.actor, self.build.id, self.build.revision, self.slot,
                    self.instance.mode, self.instance.final_values, self.instance.extras)
        await self.cog.show_preview(interaction, self.actor, preview)


class JetsModal(GuardedModal):
    def __init__(self, cog, actor, editor):
        self.editor = editor
        self.expected_draft = editor.draft_revision
        super().__init__(cog, actor, "Saisir les jets affichés")
        previous = {v.ref: v.value for v in editor.instance.final_values}
        self.inputs = []
        for effect in editor.effects[editor.page * 5:(editor.page + 1) * 5]:
            field = discord.ui.TextInput(label=f"{STAT_LABELS[effect.stat]} ({effect.low} à {effect.high})"[:45],
                        default=str(previous.get(effect.ref, effect.high)), max_length=6)
            self.inputs.append((effect.ref, field))
            self.add_item(field)

    async def on_submit(self, interaction):
        await self.guard(interaction)
        if self.editor.is_finished():
            raise BuildError("L'éditeur de jets a expiré. Rouvre l'équipement.")
        if self.editor.draft_revision != self.expected_draft:
            raise BuildError("Le brouillon a changé. Rouvre le formulaire de jets avant de confirmer.")
        changes = {ref: integer(str(field), "Jet", -10000, 10000) for ref, field in self.inputs}
        self.editor.instance = edit_jets(self.editor.template, self.editor.instance, changes)
        self.editor.draft_revision += 1
        await interaction.response.edit_message(content=self.editor.content(), view=self.editor)


class ExoModal(GuardedModal):
    stat = discord.ui.TextInput(label="Caractéristique (ex. PM, PA, Portée)", max_length=40)
    value = discord.ui.TextInput(label="Valeur de l'exo (0 pour retirer)", max_length=5)

    def __init__(self, cog, actor, editor):
        self.editor = editor
        self.expected_draft = editor.draft_revision
        super().__init__(cog, actor, "Exo déclaré — aucune garantie de réalisation")

    async def on_submit(self, interaction):
        await self.guard(interaction)
        if self.editor.is_finished() or self.editor.instance.mode != "declared_fm":
            raise BuildError("Rouvre l'éditeur et sélectionne FM déclarée.")
        if self.editor.draft_revision != self.expected_draft:
            raise BuildError("Le brouillon a changé. Rouvre le formulaire d’exo.")
        stat = stat_name(str(self.stat))
        value = integer(str(self.value), "Exo", 0, 10000)
        if any(e.stat == stat for e in self.editor.effects):
            raise BuildError("Cette caractéristique existe déjà : modifie son jet naturel pour faire un over.")
        extras = as_stats(self.editor.instance.extras)
        if value:
            extras[stat] = value
        else:
            extras.pop(stat, None)
        candidate = edit_jets(self.editor.template, self.editor.instance, {})
        self.editor.instance = revised(candidate, extras=values(extras))
        self.editor.draft_revision += 1
        await interaction.response.edit_message(content=self.editor.content(), view=self.editor)


class HistoryView(OwnerView):
    def __init__(self, cog, actor, current, revisions):
        self.current = current
        super().__init__(cog, actor)
        options = [discord.SelectOption(label=f"Révision {b.revision} · {b.name}"[:100], value=str(b.revision),
                   description=f"{b.profile.classe.capitalize()} {b.profile.level} · {b.updated_at[:16]}")
                   for b in revisions if b.revision != current.revision]
        if options:
            select = discord.ui.Select(placeholder="Prévisualiser une ancienne version", options=options[:20])
            async def chosen(interaction):
                await interaction.response.defer(ephemeral=True)
                preview = await cog.service.restore_revision(actor, current.id, current.revision, int(select.values[0]))
                await cog.show_preview(interaction, actor, preview)
            select.callback = chosen
            self.add_item(select)


class DetailsView(OwnerView):
    def __init__(self, cog, actor, build, report, catalog, page=0):
        self.build, self.report, self.catalog, self.page = build, report, catalog, page
        super().__init__(cog, actor)
        self.previous.disabled = page == 0
        self.next.disabled = page == 3

    def embed(self):
        embed = discord.Embed(title=f"Détails — {safe(self.build.name, 100)}", colour=0x2A79BB)
        if self.page in (0, 1):
            metrics = self.report.metrics[:21] if self.page == 0 else self.report.metrics[21:]
            embed.description = "\n".join(f"{STAT_LABELS[m.stat]} : **{metric_text(self.report, m.stat)}**" for m in metrics)
        elif self.page == 2:
            counts = {}
            for row in self.build.slots:
                item = self.catalog.resolve(row.item)
                if item.set_ref:
                    counts.setdefault(item.set_ref, set()).add(item.ref)
            lines = []
            for ref, items in counts.items():
                definition = self.catalog.by_set.get(ref)
                tier = next((t for t in definition.tiers if t.pieces == len(items)), None) if definition else None
                bonus = ", ".join(f"{STAT_LABELS[e.stat]} {e.high:+d}" for e in tier.effects if e.kind == "stat") if tier else "bonus manquant"
                lines.append(f"**{safe(ref, 100)} · {len(items)} pièce(s)**\n{safe(bonus or 'Aucun bonus', 450)}")
            embed.description = "\n\n".join(lines)[:3900] or "Aucune panoplie équipée."
        else:
            diagnostics = self.report.errors + self.report.warnings
            embed.description = "\n".join(f"• {safe(d.text, 200)}" for d in diagnostics[:16]) or "Aucune réserve."
            if len(diagnostics) > 16:
                embed.description += f"\n… {len(diagnostics) - 16} autres réserves dans le rapport texte."
        embed.set_footer(text=f"Page {self.page + 1}/4 · * : total partiel · Données Xixou / Wiki Moon")
        return embed

    @discord.ui.button(label="Précédent", row=0)
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Suivant", row=0)
    async def next(self, interaction, button):
        await self.turn(interaction, 1)

    async def turn(self, interaction, delta):
        self.stop()
        view = DetailsView(self.cog, self.actor, self.build, self.report, self.catalog, self.page + delta)
        await replace_view(interaction, view, embed=view.embed())

    @discord.ui.button(label="Rapport complet TXT", row=0)
    async def export(self, interaction, button):
        from .renderer import text_report
        await interaction.response.defer(ephemeral=True)
        raw = text_report(self.build, self.report, self.catalog, self.cog.repository.durable).encode()
        await interaction.followup.send(file=discord.File(BytesIO(raw), filename="evolution-build-details.txt"), ephemeral=True)
