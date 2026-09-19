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
        # send_modal est la réponse initiale : pas de defer avant.
        await interaction.response.send_modal(ProfileModal(self.cog, self.actor, self.build))

    @discord.ui.button(label="Détails", row=0)
    async def details(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        build, report, catalog = await self.cog.service.inspect(self.actor, self.build.id)
        raw = text_report(build, report, catalog, self.cog.repository.durable).encode()
        await interaction.followup.send(file=discord.File(BytesIO(raw), filename="evolution-build-details.txt"), ephemeral=True)

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
        select = discord.ui.Select(placeholder="Emplacement", options=[discord.SelectOption(label=s.replace("_", " ").capitalize(), value=s) for s in SLOTS])
        async def chosen(interaction):
            await interaction.response.send_modal(SearchModal(cog, actor, build, select.values[0]))
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
    query = discord.ui.TextInput(label="Nom de l'objet", max_length=80)

    def __init__(self, cog, actor, build, slot):
        self.build, self.slot = build, slot
        super().__init__(cog, actor, "Rechercher un équipement")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        _, _, catalog = await self.cog.service.inspect(self.actor, self.build.id)
        results = search(catalog, str(self.query), self.slot, self.build.profile.level)
        if not results:
            raise BuildError("Aucun objet correspondant dans ce catalogue et ce niveau.")
        view = ItemResults(self.cog, self.actor, self.build, self.slot, results)
        await self.cog.send_view(interaction, content="Sélectionne un objet pour voir le nouveau total et les changements de panoplie.", view=view, ephemeral=True)


class ItemResults(OwnerView):
    def __init__(self, cog, actor, build, slot, items):
        super().__init__(cog, actor)
        select = discord.ui.Select(placeholder="Objet", options=[discord.SelectOption(label=i.name[:100], value=i.ref,
                                    description=f"Niveau {i.level} · {i.category}"[:100]) for i in items])
        async def chosen(interaction):
            await interaction.response.defer(ephemeral=True)
            preview = await cog.service.equipment(actor, build.id, build.revision, slot, select.values[0])
            await cog.show_preview(interaction, actor, preview)
        select.callback = chosen
        self.add_item(select)


class ProfileModal(GuardedModal):
    classe = discord.ui.TextInput(label="Classe", max_length=20)
    level = discord.ui.TextInput(label="Niveau", max_length=3)
    allocated = discord.ui.TextInput(label='Points DÉPENSÉS : {"cha": 300}', style=discord.TextStyle.paragraph, required=False, max_length=300)
    scrolled = discord.ui.TextInput(label='Parchottage : {"cha": 101}', style=discord.TextStyle.paragraph, required=False, max_length=300)
    naked = discord.ui.TextInput(label="Stats nues JSON (sinon laisser vide)", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, cog, actor, build):
        self.build = build
        super().__init__(cog, actor, "Profil du personnage")
        p = build.profile
        self.classe.default, self.level.default = p.classe, str(p.level)
        self.allocated.default = canonical({v.stat: v.value for v in p.allocated})
        self.scrolled.default = canonical({v.stat: v.value for v in p.scrolled})
        self.naked.default = canonical({v.stat: v.value for v in p.naked_stats}) if p.mode == "declared" else ""

    async def on_submit(self, interaction):
        from .conditions import norm
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        def stats(value):
            parsed = parse_json((value.strip() or "{}").encode())
            if not isinstance(parsed, dict):
                raise BuildError("Un objet JSON de caractéristiques est attendu.")
            return values(parsed)
        declared = bool(str(self.naked).strip())
        if declared and any(stats(str(field)) for field in (self.allocated, self.scrolled)):
            raise BuildError("Les stats nues incluent déjà les points et le parcho : vider ces deux champs.")
        profile = Profile(classe=norm(str(self.classe)), level=int(str(self.level)),
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
