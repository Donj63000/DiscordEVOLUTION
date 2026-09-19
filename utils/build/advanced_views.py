"""Parcours privés du simulateur, du carnet de prix et de l'optimisation."""
from __future__ import annotations

from io import BytesIO
import asyncio
import logging

import discord

from .combat import CombatScenario, ElementResistance, simulate_attack, compare_attacks
from .config import flag
from .editor import parse_stats, format_stats, integer, stat_name, SLOT_LABELS
from .embeds import safe
from .models import BuildError, SLOTS, canonical, revised, values
from .optimizer import Constraints
from .prices import PriceQuote, quote_for, latest_quotes
from .views import OwnerView, GuardedModal, ActionConfirm, OptimizationView

log = logging.getLogger(__name__)


def result_text(result):
    """Je garde les valeurs inconnues visibles au lieu de les remplacer par zéro."""
    lines = ["**Simulation d'une attaque** · formule expérimentale, non vérifiée en jeu."]
    for title, branch in (("Normal", result.normal), ("Critique", result.critical)):
        if branch is None:
            continue
        for label, value in (("dégâts", branch.damage), ("soins", branch.healing), ("vol de vie", branch.life_steal)):
            lines.append(f"{title}, {label} : " + (f"{value.minimum}–{value.maximum} (moyenne {value.average:.2f})" if value else "non calculable"))
    chance = result.critical_probability
    lines.append("Probabilité critique : " + (f"{chance:.2%}" if chance is not None else "inconnue"))
    for label, amount in (("Dégâts moyens", result.average_damage), ("Soins moyens", result.average_healing), ("Vol de vie moyen", result.average_life_steal)):
        lines.append(f"{label} : " + (f"{amount:.2f}" if amount is not None else "non calculable"))
    warnings = tuple(dict.fromkeys((*result.normal.warnings,
        *(result.critical.warnings if result.critical else ()), *result.warnings)))
    lines.extend(safe(w)[:200] for w in warnings[:5])
    lines.append("Source : [Xixou](https://xixou.io/les-outils/api/)")
    return "\n".join(lines)[:1900]


async def send_simulator(cog, interaction, actor, build_id):
    current, _, _ = await cog.service.inspect(actor, build_id)
    view = SimulatorView(cog, actor, current)
    await cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)


class SimulatorView(OwnerView):
    def __init__(self, cog, actor, build, attack=None, scenario=None):
        self.build, self.attack = build, attack
        self.scenario = scenario or CombatScenario()
        super().__init__(cog, actor)

    def content(self):
        selected = safe(self.attack.name) + f" niveau {self.attack.level}" if self.attack else "À sélectionner"
        return (f"**Simulateur · {safe(self.build.name)}**\nAttaque : {selected}\n"
                f"Cible : {self.scenario.mode.upper()}, résistances et buffs configurables.\n"
                "Une seule attaque ; les effets spéciaux non interprétés restent non calculables.")

    async def redraw(self, interaction):
        await interaction.edit_original_response(content=self.content(), embed=None, view=self)

    @discord.ui.button(label="Choisir un sort", row=0)
    async def spell(self, interaction, button):
        await interaction.response.send_modal(SpellSearchModal(self))

    @discord.ui.button(label="Arme équipée", row=0)
    async def weapon(self, interaction, button):
        from .combat import weapon_attack
        build, _, scenario = self.snapshot()
        await interaction.response.defer(ephemeral=True)
        current, _, catalog = await self.cog.service.inspect(self.actor, build.id)
        instance = current.in_slot("arme")
        if instance is None:
            raise BuildError("Équipe une arme avant de simuler son attaque.")
        attack = weapon_attack(catalog.resolve(instance))
        await self.cog.repository.put_snapshot("attacks", attack.revision, canonical(attack))
        view = SimulatorView(self.cog, self.actor, current, attack, scenario)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cible / buffs / maîtrise", row=1)
    async def target(self, interaction, button):
        await interaction.response.send_modal(TargetModal(self))

    def snapshot(self):
        return self.build, self.attack, self.scenario

    async def evaluate(self, *, snapshot=None):
        build, attack, scenario = snapshot or self.snapshot()
        if attack is None:
            raise BuildError("Choisis un sort et son niveau, ou l'arme équipée.")
        current, report, _ = await self.cog.service.inspect(self.actor, build.id)
        if current.revision != build.revision:
            raise BuildError("Le build a changé. Rouvre le simulateur pour utiliser sa nouvelle révision.")
        if attack.required_level and current.profile.level < attack.required_level:
            raise BuildError("Le personnage n'a pas le niveau requis pour cette attaque.")
        if attack.classe and attack.classe != current.profile.classe:
            raise BuildError("Cette attaque appartient à une autre classe.")
        return await asyncio.to_thread(simulate_attack, report, attack, scenario)

    @discord.ui.button(label="Calculer", style=discord.ButtonStyle.primary, row=1)
    async def compute(self, interaction, button):
        snapshot = self.snapshot()
        build, attack, scenario = snapshot
        await interaction.response.defer(ephemeral=True)
        result = await self.evaluate(snapshot=snapshot)
        log.debug("build simulation attack=%s complete=%s", attack.id, result.complete)
        await interaction.followup.send(result_text(result), ephemeral=True,
            file=discord.File(BytesIO(canonical({"build_id": build.id, "build_revision": build.revision,
                "attack": attack, "scenario": scenario,
                "result": result}).encode()), filename="simulation-build.json"),
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Comparer deux builds", row=2)
    async def compare(self, interaction, button):
        snapshot = self.snapshot()
        build, attack, scenario = snapshot
        await interaction.response.defer(ephemeral=True)
        await self.evaluate(snapshot=snapshot)
        rows = [row for row in await self.cog.repository.list(self.actor) if row.id != build.id]
        if not rows:
            raise BuildError("Crée un deuxième build pour comparer la même attaque et la même cible.")
        view = OwnerView(self.cog, self.actor)
        select = discord.ui.Select(placeholder="Deuxième build", options=[discord.SelectOption(label=b.name[:100], value=b.id) for b in rows[:25]])
        async def chosen(i):
            selected_id = select.values[0]
            await i.response.defer(ephemeral=True)
            left_build, left, _ = await self.cog.service.inspect(self.actor, build.id)
            if left_build.revision != build.revision:
                raise BuildError("Le build a changé. Rouvre le simulateur avant de comparer.")
            right_build, right, _ = await self.cog.service.inspect(self.actor, selected_id)
            if attack.required_level and right_build.profile.level < attack.required_level:
                raise BuildError("Le deuxième personnage n'a pas le niveau de l'attaque.")
            if attack.classe and attack.classe != right_build.profile.classe:
                raise BuildError("Le deuxième personnage n'a pas la classe de l'attaque.")
            output = await asyncio.to_thread(compare_attacks, left, right, attack, scenario)
            delta = output["average_damage_delta"]
            output.update(attack=attack, scenario=scenario, left_build=left_build.id,
                          left_revision=left_build.revision, right_build=right_build.id,
                          right_revision=right_build.revision)
            summary = f"Écart de dégâts moyens : {delta:+.2f}." if delta is not None else "Écart non calculable : données incomplètes."
            await i.followup.send(f"Comparaison sur **la même attaque et la même cible**.\n{summary}", ephemeral=True,
                file=discord.File(BytesIO(canonical(output).encode()), filename="comparaison-attaque.json"))
        select.callback = chosen
        view.add_item(select)
        await self.cog.send_view(interaction, content="Choisis ton deuxième personnage.", view=view, ephemeral=True)

    @discord.ui.button(label="Optimiser cette attaque", row=2)
    async def optimize(self, interaction, button):
        snapshot = self.snapshot()
        build, attack, scenario = snapshot
        await interaction.response.defer(ephemeral=True)
        await self.evaluate(snapshot=snapshot)
        view = AdvancedOptimizerView(self.cog, self.actor, build,
            Constraints(objective="damage", attack=attack, scenario=scenario))
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)


class SpellSearchModal(GuardedModal):
    query = discord.ui.TextInput(label="Nom du sort", min_length=2, max_length=80)
    level = discord.ui.TextInput(label="Niveau du sort (1–6)", default="6", max_length=1)

    def __init__(self, parent):
        self.parent = parent
        self.snapshot = parent.snapshot()
        super().__init__(parent.cog, parent.actor, "Rechercher un sort")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        level = integer(str(self.level), "Niveau", 1, 6)
        catalog = await self.cog.spells.ensure()
        from utils.xixou_api import identity_key
        query = identity_key(str(self.query))
        rows = [a for a in catalog.attacks if a.level == level and query in identity_key(a.name)][:25]
        if not rows:
            raise BuildError("Aucun sort correspondant à ce niveau dans le catalogue documenté.")
        view = OwnerView(self.cog, self.actor)
        select = discord.ui.Select(placeholder="Choisir le sort", options=[discord.SelectOption(label=a.name[:100], value=a.id) for a in rows])
        async def chosen(i):
            selected_id = select.values[0]
            await i.response.defer(ephemeral=True)
            attack = await self.cog.spells.attack(catalog.id, selected_id, level)
            build, _, scenario = self.snapshot
            replacement = SimulatorView(self.cog, self.actor, build, attack, scenario)
            await self.cog.send_view(i, content=replacement.content(), view=replacement, ephemeral=True)
            self.parent.stop()
            view.stop()
        select.callback = chosen
        view.add_item(select)
        await self.cog.send_view(interaction, content="Choisis le sort. Sa classe n'est pas devinée si la source ne l'indique pas.", view=view, ephemeral=True)


def parse_resistances(text):
    """Chaque ligne porte élément, fixe, pourcentage et éventuellement les bonus PvP."""
    rows = []
    names = {"neutre": "ne", "terre": "te", "feu": "fe", "eau": "ea", "air": "ai"}
    for line in text.strip().splitlines():
        parts = line.replace(":", " ").replace(",", " ").split()
        if len(parts) not in {3, 5}:
            raise BuildError("Résistances : une ligne 'terre 10 20' ou 'terre 10 20 5 10' (bonus PvP).")
        element = names.get(parts[0].casefold(), parts[0].casefold())
        rows.append(ElementResistance(element=element, flat=int(parts[1]), percent=int(parts[2]),
            pvp_flat=int(parts[3]) if len(parts) == 5 else 0, pvp_percent=int(parts[4]) if len(parts) == 5 else 0))
    return tuple(rows)


class TargetModal(GuardedModal):
    resistances = discord.ui.TextInput(label="Résistances : terre 10 20 [fixe PvP % PvP]", style=discord.TextStyle.paragraph, required=False, max_length=500)
    buffs = discord.ui.TextInput(label="Buffs : Force: 100, Dommages: 10", required=False, max_length=500)
    context = discord.ui.TextInput(label="Contexte : pvm ou pvp", default="pvm", max_length=3)
    mastery = discord.ui.TextInput(label="Maîtrise % / coefficient arme %", default="0/100", max_length=12)
    health = discord.ui.TextInput(label="PV cible / PV manquants lanceur / cible", required=False, placeholder="Exemple : 500 / 200 / 100", max_length=40)

    def __init__(self, parent):
        self.parent = parent
        self.snapshot = parent.snapshot()
        super().__init__(parent.cog, parent.actor, "Cible et contexte de l'attaque")
        _, _, scenario = self.snapshot
        self.resistances.default = "\n".join(f"{r.element} {r.flat} {r.percent} {r.pvp_flat} {r.pvp_percent}" for r in scenario.resistances)
        self.buffs.default = format_stats(scenario.buffs)
        self.context.default = scenario.mode
        self.mastery.default = f"{scenario.mastery_percent}/{scenario.weapon_skill_percent}"
        if any(v is not None for v in (scenario.target_hp, scenario.source_missing_hp, scenario.target_missing_hp)):
            self.health.default = "/".join(str(v) if v is not None else "?" for v in (scenario.target_hp, scenario.source_missing_hp, scenario.target_missing_hp))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        mastery = str(self.mastery).split("/")
        health = str(self.health).split("/") if str(self.health).strip() else ["?", "?", "?"]
        if len(mastery) != 2 or len(health) != 3:
            raise BuildError("Sépare les valeurs par / ; ? signifie PV inconnus.")
        hp = [None if s.strip() == "?" else int(s.strip()) for s in health]
        scenario = CombatScenario(mode=str(self.context).strip().lower(),
            resistances=parse_resistances(str(self.resistances)), buffs=parse_stats(str(self.buffs)),
            mastery_percent=int(mastery[0]), weapon_skill_percent=int(mastery[1]),
            target_hp=hp[0], source_missing_hp=hp[1], target_missing_hp=hp[2])
        build, attack, _ = self.snapshot
        view = SimulatorView(self.cog, self.actor, build, attack, scenario)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)
        self.parent.stop()


class AdvancedOptimizerView(OwnerView):
    def __init__(self, cog, actor, build, constraints=None):
        self.build, self.constraints = build, constraints or Constraints()
        super().__init__(cog, actor)

    def content(self):
        c = self.constraints
        return (f"**Optimiser {safe(self.build.name)}** · objectif {c.objective}\n"
                f"Minimums : {format_stats(c.minimums) or 'aucun'}\n"
                f"Verrouillés : {', '.join(SLOT_LABELS[s] for s in c.locked) or 'aucun'} · possédés : {', '.join(SLOT_LABELS[s] for s in c.owned_slots) or 'aucun'}\n"
                f"FM déclarée autorisée : {c.allow_exos}\n"
                f"Budget : {c.budget if c.budget is not None else 'aucun'} · serveur : {safe(c.price_context) or 'non choisi'}\n"
                "Seuls les objets déclarés possédés sont gratuits. Un prix manquant ou des jets différents empêchent de confirmer le budget. Recherche limitée, sans garantie d'optimum global.")

    @discord.ui.button(label="Objectif / contraintes", row=0)
    async def settings(self, interaction, button):
        await interaction.response.send_modal(OptimizerModal(self))

    @discord.ui.button(label="Budget / serveur", row=0)
    async def budget(self, interaction, button):
        await interaction.response.send_modal(BudgetModal(self))

    @discord.ui.button(label="Autoriser / interdire FM", row=0)
    async def exos(self, interaction, button):
        self.constraints = revised(self.constraints, allow_exos=not self.constraints.allow_exos)
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Rechercher", style=discord.ButtonStyle.primary, row=1)
    async def run(self, interaction, button):
        build, constraints = self.build, self.constraints
        await interaction.response.defer(ephemeral=True)
        if not flag("BUILD_OPTIMIZER_ENABLED"):
            raise BuildError("Optimiseur désactivé par le Staff.")
        current, _, catalog = await self.cog.service.inspect(self.actor, build.id)
        if current.revision != build.revision:
            raise BuildError("Le build a changé. Rouvre l'optimiseur pour confirmer les objets possédés.")
        _, rules = await self.cog.service.dependencies(current)
        quotes = latest_quotes(await self.cog.repository.prices(self.actor), constraints.price_context)
        request = revised(constraints, quotes=quotes)
        result = await self.cog.worker.run(current, catalog, rules, request)
        choices = result.get("solutions", []) + result.get("tentative", [])
        view = OptimizationView(self.cog, self.actor, result, current) if choices else None
        await self.cog.send_view(interaction, content=f"**{result['status']}** · {len(choices)} propositions.\n{result.get('message', '')}", view=view, ephemeral=True)


def parse_slots(text):
    from .conditions import norm
    aliases = {norm(key): key for key in SLOTS}
    aliases.update({norm(label): key for key, label in SLOT_LABELS.items()})
    result = []
    for value in text.split(","):
        if value.strip():
            key = aliases.get(norm(value.strip()))
            if key is None:
                raise BuildError("Emplacement inconnu. Exemples : Coiffe, Cape, Anneau 1, Dofus 1.")
            result.append(key)
    return tuple(result)


class OptimizerModal(GuardedModal):
    objective = discord.ui.TextInput(label="Caractéristique à optimiser (ou dégâts)", max_length=40)
    minimums = discord.ui.TextInput(label="Minimums : PA: 10, PM: 5", required=False, max_length=300)
    locked = discord.ui.TextInput(label="Emplacements verrouillés (virgules)", placeholder="Coiffe, Cape, Anneau 1", required=False, max_length=200)
    owned = discord.ui.TextInput(label="Emplacements possédés (virgules)", placeholder="Coiffe, Cape, Anneau 1", required=False, max_length=200)

    def __init__(self, parent):
        self.parent = parent
        self.build, self.constraints = parent.build, parent.constraints
        super().__init__(parent.cog, parent.actor, "Contraintes de recherche")
        c = self.constraints
        self.objective.default, self.minimums.default = c.objective, format_stats(c.minimums)
        self.locked.default = ", ".join(SLOT_LABELS[s] for s in c.locked)
        self.owned.default = ", ".join(SLOT_LABELS[s] for s in c.owned_slots)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        from .conditions import norm
        raw_objective = str(self.objective).strip()
        objective = "damage" if norm(raw_objective) in {"damage", "degats"} else stat_name(raw_objective)
        constraints = revised(self.constraints, objective=objective,
            minimums=parse_stats(str(self.minimums), low=0),
            locked=parse_slots(str(self.locked)), owned_slots=parse_slots(str(self.owned)))
        view = AdvancedOptimizerView(self.cog, self.actor, self.build, constraints)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)
        self.parent.stop()


class BudgetModal(GuardedModal):
    server = discord.ui.TextInput(label="Serveur Dofus", max_length=80)
    amount = discord.ui.TextInput(label="Budget en kamas (vide = sans plafond)", required=False, max_length=13)

    def __init__(self, parent):
        self.parent = parent
        self.build, self.constraints = parent.build, parent.constraints
        super().__init__(parent.cog, parent.actor, "Budget des achats nécessaires")
        self.server.default = self.constraints.price_context
        self.amount.default = str(self.constraints.budget) if self.constraints.budget is not None else ""

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        amount = str(self.amount).strip()
        constraints = revised(self.constraints, price_context=str(self.server).strip(),
            budget=int(amount) if amount else None, quotes=())
        view = AdvancedOptimizerView(self.cog, self.actor, self.build, constraints)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)
        self.parent.stop()


class PriceBookView(OwnerView):
    def __init__(self, cog, actor, build, catalog):
        self.build, self.catalog = build, catalog
        super().__init__(cog, actor)
        if build.slots:
            select = discord.ui.Select(placeholder="Saisir le prix d'un équipement et de ses jets actuels", row=0,
                options=[discord.SelectOption(label=f"{SLOT_LABELS[r.slot]} · {catalog.resolve(r.item).name}"[:100], value=r.slot) for r in build.slots])
            async def chosen(i):
                slot = select.values[0]
                await i.response.send_modal(PriceModal(self, slot))
            select.callback = chosen
            self.add_item(select)

    @discord.ui.button(label="Voir / supprimer mes prix", row=1)
    async def listing(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        rows = [PriceQuote.model_validate(row) for row in await self.cog.repository.prices(self.actor)]
        view = PriceListView(self.cog, self.actor, rows)
        await self.cog.send_view(interaction, content=view.content(), view=view, ephemeral=True)


class PriceModal(GuardedModal):
    server = discord.ui.TextInput(label="Serveur Dofus", max_length=80)
    amount = discord.ui.TextInput(label="Prix en kamas", max_length=13)
    date = discord.ui.TextInput(label="Date ISO avec fuseau (vide = maintenant)", required=False, max_length=40)

    def __init__(self, parent, slot):
        self.parent, self.slot = parent, slot
        super().__init__(parent.cog, parent.actor, "Prix personnel pour ces jets")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        instance = self.parent.build.in_slot(self.slot)
        quote = quote_for(instance, self.parent.catalog.resolve(instance), str(self.server).strip(),
            int(str(self.amount).strip()), str(self.date).strip() or None)
        async def commit(i, operation):
            await self.cog.repository.set_price(self.actor, quote.model_dump(mode="json"))
            await i.edit_original_response(content="Prix personnel enregistré dans #console pour ces jets.", view=None)
        view = ActionConfirm(self.cog, self.actor, commit, "Enregistrer ce prix")
        jets = format_stats(getattr(quote, "jets", ())) or "Jets détaillés indisponibles pour cet ancien prix."
        await self.cog.send_view(interaction, content=(f"**{safe(quote.label)}** · {quote.kamas} kamas · {safe(quote.server)}\n"
            f"{quote.observed_at}\nJets concernés : {jets}\nPrix privé associé à ces jets.")[:1900], view=view, ephemeral=True)


class PriceListView(OwnerView):
    def __init__(self, cog, actor, rows, page=0):
        self.rows, self.page = sorted(rows, key=lambda q: q.observed_at, reverse=True), page
        super().__init__(cog, actor)
        selected = self.rows[page * 25:(page + 1) * 25]
        if selected:
            select = discord.ui.Select(placeholder="Choisir un prix à supprimer", row=0, options=[discord.SelectOption(
                label=f"{q.label} · {q.kamas} k"[:100], description=f"{q.server} · {q.observed_at}"[:100], value=q.id) for q in selected])
            async def chosen(i):
                quote_id = select.values[0]
                quote = next(row for row in selected if row.id == quote_id)
                await i.response.defer(ephemeral=True)
                async def remove(j, operation):
                    await cog.repository.delete_price(actor, quote_id)
                    await j.edit_original_response(content="Prix supprimé du carnet privé.", view=None)
                view = ActionConfirm(cog, actor, remove, "Supprimer ce prix")
                jets = format_stats(getattr(quote, "jets", ())) or "Jets détaillés indisponibles pour cet ancien prix."
                await cog.send_view(i, content=(f"**{safe(quote.label)}** · {quote.kamas} kamas · {safe(quote.server)}\n"
                    f"{quote.observed_at}\nJets concernés : {jets}\nSupprimer ce prix personnel ?")[:1900], view=view, ephemeral=True)
            select.callback = chosen
            self.add_item(select)
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= len(rows)

    def content(self):
        return f"**Carnet privé : {len(self.rows)} prix** · page {self.page + 1}. Les prix restent associés à leurs jets exacts."

    async def move(self, interaction, page):
        view = PriceListView(self.cog, self.actor, self.rows, page)
        await interaction.response.edit_message(content=view.content(), view=view)
        view.message = self.message
        self.stop()

    @discord.ui.button(label="Précédent", row=1)
    async def previous(self, interaction, button):
        await self.move(interaction, self.page - 1)

    @discord.ui.button(label="Suivant", row=1)
    async def next(self, interaction, button):
        await self.move(interaction, self.page + 1)


class ExoSinkModal(GuardedModal):
    sink = discord.ui.TextInput(label="Puits déclaré (0 si tu sais qu'il est nul)", max_length=20)

    def __init__(self, cog, actor, output):
        self.output = output
        super().__init__(cog, actor, "Ouvrir une session native /exo")

    async def on_submit(self, interaction):
        from utils.exo_engine import decimal_value
        await interaction.response.defer(ephemeral=True)
        await self.guard(interaction)
        sink = decimal_value(str(self.sink), "1000000")
        async def open_session(i, operation):
            cog = self.cog.bot.get_cog("ExoCog")
            if cog is None:
                raise BuildError("L'atelier /exo est indisponible.")
            await cog.open_build_session(i, self.output["reference"], self.output["jets_declares"], sink, guild_id=self.actor.guild_id)
        view = ActionConfirm(self.cog, self.actor, open_session, "Ouvrir /exo avec ces jets")
        jets = format_stats(values(self.output["jets_declares"])) or "Aucun bonus de caractéristique."
        await self.cog.send_view(interaction, content=(f"Objet : **{safe(self.output['reference'])}**\n"
            f"Jets déclarés : {jets}\nOuvrir avec un puits de **{sink}** ?\n"
            "L'atelier précédent sera remplacé après ouverture réussie. Le puits n'est jamais déduit des jets.")[:1900], view=view, ephemeral=True)


class ExoTransferView(OwnerView):
    def __init__(self, cog, actor, output):
        self.output = output
        super().__init__(cog, actor)

    @discord.ui.button(label="Renseigner le puits", style=discord.ButtonStyle.primary)
    async def sink(self, interaction, button):
        await interaction.response.send_modal(ExoSinkModal(self.cog, self.actor, self.output))
