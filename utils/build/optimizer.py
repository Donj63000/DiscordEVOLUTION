"""Recherche bornée et déterministe ; les finalistes passent le calculateur commun.

Les contraintes numériques ne sont JAMAIS relâchées. Les candidats avec données
partielles peuvent être retournés séparément, sans certificat de conformité.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Annotated, Literal
from collections import Counter, defaultdict
import time
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import (Frozen, Build, Catalog, StatValue, SLOTS, STAT_LABELS, BuildError,
                     equip, SlotItem, revised, as_stats, digest)
from .rules import Rules
from .calculator import calculate
from .effects import resolve_values
from .prices import PriceQuote, jet_signature
from .combat import AttackDefinition, CombatScenario, simulate_attack

CORE = ("coiffe", "cape", "amulette", "ceinture", "bottes", "anneau_1", "anneau_2", "arme")

class Price(Frozen):
    reference: str
    kamas: Annotated[StrictInt, Field(ge=0, le=10**12)]

class Constraints(Frozen):
    objective: Literal["fo", "ine", "cha", "age", "pp", "vi", "sa", "do", "so", "damage"] = "pp"
    minimums: Annotated[tuple[StatValue, ...], Field(max_length=12)] = ()
    locked: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    forbidden: Annotated[tuple[str, ...], Field(max_length=1000)] = ()
    allow_exos: StrictBool = False
    budget: Annotated[StrictInt, Field(ge=0, le=10**12)] | None = None
    prices: Annotated[tuple[Price, ...], Field(max_length=20000)] = ()
    price_context: Annotated[str, Field(max_length=200)] = ""  # serveur/date/jet de référence
    quotes: Annotated[tuple[PriceQuote, ...], Field(max_length=20000)] = ()
    owned_slots: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    attack: AttackDefinition | None = None
    scenario: CombatScenario = CombatScenario()

    @model_validator(mode="after")
    def consistent(self):
        if any(slot not in SLOTS for slot in self.locked) or len(set(self.locked)) != len(self.locked):
            raise ValueError("Emplacements verrouillés invalides.")
        if len(set(self.forbidden)) != len(self.forbidden) or len({p.reference for p in self.prices}) != len(self.prices):
            raise ValueError("Références dupliquées.")
        if any(v < 0 for v in as_stats(self.minimums).values()):
            raise ValueError("Les seuils minimaux ne peuvent être négatifs.")
        if self.prices and not self.price_context:
            raise ValueError("Les prix nécessitent serveur, date et type de jet.")
        if len(set(self.owned_slots)) != len(self.owned_slots) or any(s not in SLOTS for s in self.owned_slots):
            raise ValueError("Emplacements possédés invalides.")
        if self.quotes and (not self.price_context or any(q.server.casefold() != self.price_context.casefold() for q in self.quotes)):
            raise ValueError("Les prix doivent provenir du serveur sélectionné.")
        if self.objective == "damage" and self.attack is None:
            raise ValueError("L'objectif dégâts nécessite une attaque et sa cible.")
        return self

class Limits(Frozen):
    beam: Annotated[StrictInt, Field(ge=1, le=128)] = 32
    candidates: Annotated[StrictInt, Field(ge=1, le=64)] = 24
    expansions: Annotated[StrictInt, Field(ge=1, le=100000)] = 30000
    seconds: Annotated[StrictInt, Field(ge=1, le=20)] = 8

@dataclass
class State:
    slots: tuple
    totals: dict
    sets: dict
    used: dict
    signature: tuple


def item_stats(template):
    totals = defaultdict(int)
    for e in template.effects:
        if e.kind == "stat":
            totals[e.stat] += e.high
    return dict(totals)


def objective_hint(stats, constraints):
    """Je classe les états partiels ; seul le simulateur départage les attaques finales."""
    if constraints.objective != "damage":
        return stats.get(constraints.objective, 0) + (stats.get("cha", 0) / 10 if constraints.objective == "pp" else 0)
    from .damage import ELEMENT_STAT
    lines = constraints.attack.normal_lines
    return sum(((line.minimum + line.maximum) / 200 *
                (stats.get(ELEMENT_STAT.get(line.element, "ine"), 0) + stats.get("pui", 0)) +
                stats.get("so" if line.kind == "heal" else "do", 0)) for line in lines) + stats.get("cc", 0)


def purchase_cost(original, candidate, catalog, constraints):
    """Je consomme chaque objet déclaré possédé une seule fois, à jets identiques."""
    inventory = Counter()
    for slot in constraints.owned_slots:
        instance = original.in_slot(slot)
        if instance:
            template = catalog.resolve(instance)
            inventory[(instance.template_ref, jet_signature(instance, template))] += 1
    quotes = {}
    for quote in constraints.quotes:
        key = (quote.template_ref, quote.jet_signature)
        previous = quotes.get(key)
        if previous is None or quote.observed_at > previous.observed_at:
            quotes[key] = quote
    legacy = {p.reference: p.kamas for p in constraints.prices}
    cost, missing = 0, []
    for row in candidate.slots:
        instance, template = row.item, catalog.resolve(row.item)
        key = (instance.template_ref, jet_signature(instance, template))
        if inventory[key]:
            inventory[key] -= 1
            continue
        quote = quotes.get(key)
        if quote is not None and quote.template_revision == template.revision:
            cost += quote.kamas
        elif instance.mode == "natural_best" and instance.template_ref in legacy:
            cost += legacy[instance.template_ref]
        else:
            missing.append(f"{row.slot}:{instance.template_ref}")
    return (None if missing else cost), missing


def pools_for(build, catalog, constraints, limits):
    pools, truncated = {}, False
    for slot in SLOTS:
        if slot in constraints.locked:
            instance = build.in_slot(slot)
            if instance:
                if instance.template_ref in constraints.forbidden or (not constraints.allow_exos and instance.mode == "declared_fm"):
                    raise BuildError("Un emplacement verrouillé contredit les interdictions/exos demandés.")
                if catalog.resolve(instance).level > build.profile.level:
                    raise BuildError("Un emplacement verrouillé dépasse le niveau du personnage.")
            pools[slot] = (instance,)
            continue
        eligible = [i for i in catalog.items if slot in i.allowed_slots and i.level <= build.profile.level and i.ref not in constraints.forbidden]
        stats = {i.ref: item_stats(i) for i in eligible}
        def score(i):
            v = stats[i.ref]
            # Le score guide la recherche. Il n'est pas une preuve de dégâts/optimalité.
            return objective_hint(v, constraints)
        ranked = sorted(eligible, key=lambda i: (-score(i), i.ref))
        selected = {}
        # Conserver les seuils PA/PM/PO et différents groupes de panoplie.
        for stat in ("pa", "pm", "po"):
            for item in sorted(eligible, key=lambda i: (-stats[i.ref].get(stat, 0), -score(i), i.ref))[:2]:
                if stats[item.ref].get(stat, 0) > 0:
                    selected[item.ref] = item
        by_set = {}
        for item in ranked:
            if item.set_ref and item.set_ref not in by_set:
                by_set[item.set_ref] = item
        for item in list(by_set.values())[:max(1, limits.candidates // 3)]:
            selected[item.ref] = item
        for item in ranked:
            if len(selected) >= limits.candidates:
                break
            selected[item.ref] = item
        chosen = list(selected.values())[:limits.candidates]
        truncated |= len(chosen) < len(eligible)
        variants = {}
        for owned_slot in constraints.owned_slots:
            instance = build.in_slot(owned_slot)
            if instance is None:
                continue
            template = catalog.resolve(instance)
            if (slot not in template.allowed_slots or template.level > build.profile.level
                    or template.ref in constraints.forbidden
                    or (instance.mode == "declared_fm" and not constraints.allow_exos)):
                continue
            key = (template.ref, jet_signature(instance, template))
            variants[key] = revised(instance, id=equip(template).id)
        for template in chosen:
            instance = equip(template)
            variants.setdefault((template.ref, jet_signature(instance, template)), instance)
        truncated |= len(variants) > limits.candidates
        choices = tuple(variants.values())[:limits.candidates]
        if slot not in CORE:
            choices += (None,)
        if not choices:
            return {}, True
        pools[slot] = choices
    return pools, truncated


def state_score(state, catalog, constraints):
    stats = dict(state.totals)
    for ref, refs in state.sets.items():
        definition = catalog.by_set.get(ref)
        tier = next((t for t in definition.tiers if t.pieces == len(refs)), None) if definition else None
        if tier:
            for e in tier.effects:
                if e.kind == "stat":
                    stats[e.stat] = stats.get(e.stat, 0) + e.high
    target = objective_hint(stats, constraints)
    # Favorise les seuils sans éliminer prématurément un état partiel.
    progress = sum(min(stats.get(v.stat, 0), v.value) / max(v.value, 1) for v in constraints.minimums)
    return target + 1000 * progress


def optimize(build: Build, catalog: Catalog, rules: Rules, constraints: Constraints, limits: Limits = Limits()):
    if constraints.objective == "damage" and constraints.attack.required_level and constraints.attack.required_level > build.profile.level:
        raise BuildError("Le personnage n'a pas le niveau requis pour cette attaque.")
    if constraints.objective == "damage" and constraints.attack.classe and constraints.attack.classe != build.profile.classe:
        raise BuildError("Cette attaque appartient à une autre classe.")
    started = time.monotonic()
    deadline = started + limits.seconds
    pools, truncated = pools_for(build, catalog, constraints, limits)
    if not pools:
        return {"status": "DATA_INCOMPLETE", "solutions": [], "tentative": [], "message": "Un emplacement obligatoire n'a aucun candidat autorisé.", "global_optimum": False}
    empty = revised(build, slots=[])
    base = calculate(empty, catalog, rules)
    # PP sera dérivée par le calculateur final. Le score partiel n'est qu'un classement.
    beam = [State((), dict(base.totals), {}, {}, ())]
    count = 0
    for slot in SLOTS:
        expanded = []
        for state in beam:
            for instance in pools[slot]:
                count += 1
                if count > limits.expansions or time.monotonic() >= deadline:
                    return {"status": "RESOURCE_LIMIT", "solutions": [], "tentative": [], "expansions": count - 1,
                            "message": "Recherche interrompue avant un état complet. Aucune preuve d'impossibilité.", "global_optimum": False}
                used, sets, totals = dict(state.used), {k: set(v) for k, v in state.sets.items()}, dict(state.totals)
                slots = state.slots
                if instance:
                    template = catalog.resolve(instance)
                    if template.unique is True and used.get(template.ref, 0):
                        continue
                    used[template.ref] = used.get(template.ref, 0) + 1
                    contributions, _ = resolve_values(template, instance, slot)
                    for c in contributions:
                        totals[c.stat] = totals.get(c.stat, 0) + c.value
                    if template.set_ref:
                        sets.setdefault(template.set_ref, set()).add(template.ref)
                    slots += (SlotItem(slot=slot, item=instance),)
                signature = state.signature + ((instance.template_ref,
                    jet_signature(instance, catalog.resolve(instance))) if instance else ("", ""),)
                expanded.append(State(slots, totals, sets, used, signature))
        if not expanded:
            return {"status": "NO_SOLUTION_FOUND", "solutions": [], "tentative": [], "global_optimum": False}
        expanded.sort(key=lambda s: (-state_score(s, catalog, constraints), s.signature))
        # Réserver une place à quelques signatures de panoplie différentes.
        diverse, signatures = [], set()
        for state in expanded:
            set_sig = tuple(sorted((ref, len(refs)) for ref, refs in state.sets.items()))
            if set_sig not in signatures and len(diverse) < max(1, limits.beam // 4):
                diverse.append(state)
                signatures.add(set_sig)
        taken = {s.signature for s in diverse}
        beam = diverse + [s for s in expanded if s.signature not in taken][:max(0, limits.beam - len(diverse))]
        truncated |= len(expanded) > len(beam)
    solutions, tentative = [], []
    ranked = []
    for state in beam:
        if time.monotonic() >= deadline:
            return {"status": "RESOURCE_LIMIT", "solutions": [], "tentative": [], "expansions": count,
                    "message": "Temps de calcul des finalistes dépassé. Aucune preuve d'impossibilité.", "global_optimum": False}
        candidate = revised(build, slots=[s.model_dump(mode="json") for s in state.slots])
        report = calculate(candidate, catalog, rules)
        if report.errors or any(report.totals[v.stat] < v.value for v in constraints.minimums):
            continue
        cost, unknown_cost = purchase_cost(build, candidate, catalog, constraints)
        if constraints.budget is not None and cost is not None and cost > constraints.budget:
            continue
        attack_result = None
        score = report.totals.get(constraints.objective, 0)
        objective_known = report.known(constraints.objective) if constraints.objective != "damage" else False
        if constraints.objective == "damage":
            attack = constraints.attack
            if attack.kind == "weapon":
                from .combat import weapon_attack
                weapon = candidate.in_slot("arme")
                if weapon is None:
                    continue
                attack = weapon_attack(catalog.resolve(weapon))
            attack_result = simulate_attack(report, attack, constraints.scenario)
            score = attack_result.average_damage
            objective_known = attack_result.complete and score is not None
        complete = (report.equipability == "valid" and objective_known
                    and all(report.known(v.stat) for v in constraints.minimums)
                    and (constraints.budget is None or cost is not None))
        payload = {"build": candidate.model_dump(mode="json"), "report": report.model_dump(mode="json"),
                   "constraints_verified": complete, "cost": cost, "price_context": constraints.price_context,
                   "unknown_prices": unknown_cost if constraints.budget is not None else [],
                   "score": score, "attack_result": attack_result.model_dump(mode="json") if attack_result else None}
        ranked.append((complete, payload))
    ranked.sort(key=lambda row: (not row[0], -(row[1]["score"] if row[1]["score"] is not None else float("-inf")), tuple((s["slot"], s["item"]["template_ref"], s["item"]["mode"]) for s in row[1]["build"]["slots"])))
    seen = set()
    for complete, row in ranked:
        candidate = Build.model_validate(row["build"])
        signature = tuple((s.slot, s.item.template_ref,
                           jet_signature(s.item, catalog.resolve(s.item))) for s in candidate.slots)
        if signature in seen:
            continue
        seen.add(signature)
        destination = solutions if complete else tentative
        if len(destination) < 3:
            destination.append(row)
    return {"status": "FOUND" if solutions else "DATA_INCOMPLETE" if tentative else "NO_SOLUTION_FOUND",
            "solutions": solutions, "tentative": tentative, "method": "bounded_beam_search",
            "global_optimum": False, "search_exhaustive": not truncated, "expansions": count,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "message": "Les seuils ne sont pas relâchés. Les pistes partielles ne prouvent pas la conformité en jeu. Aucune absence de résultat ne prouve l'impossibilité."}
