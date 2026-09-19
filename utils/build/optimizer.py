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

CORE = ("coiffe", "cape", "amulette", "ceinture", "bottes", "anneau_1", "anneau_2", "arme")

class Price(Frozen):
    reference: str
    kamas: Annotated[StrictInt, Field(ge=0, le=10**12)]

class Constraints(Frozen):
    objective: Literal["fo", "ine", "cha", "age", "pp", "vi", "sa", "do", "so"] = "pp"
    minimums: Annotated[tuple[StatValue, ...], Field(max_length=12)] = ()
    locked: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    forbidden: Annotated[tuple[str, ...], Field(max_length=1000)] = ()
    allow_exos: StrictBool = False
    budget: Annotated[StrictInt, Field(ge=0, le=10**12)] | None = None
    prices: Annotated[tuple[Price, ...], Field(max_length=20000)] = ()
    price_context: Annotated[str, Field(max_length=200)] = ""  # serveur/date/jet de référence

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
            return v.get(constraints.objective, 0) + (v.get("cha", 0) / 10 if constraints.objective == "pp" else 0)
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
        choices = tuple(equip(i) for i in chosen)
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
    target = stats.get(constraints.objective, 0)
    if constraints.objective == "pp":
        target += max(0, stats.get("cha", 0)) / 10
    # Favorise les seuils sans éliminer prématurément un état partiel.
    progress = sum(min(stats.get(v.stat, 0), v.value) / max(v.value, 1) for v in constraints.minimums)
    return target + 1000 * progress


def optimize(build: Build, catalog: Catalog, rules: Rules, constraints: Constraints, limits: Limits = Limits()):
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
                signature = state.signature + ((instance.template_ref if instance else ""),)
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
    prices = {p.reference: p.kamas for p in constraints.prices}
    ranked = []
    for state in beam:
        candidate = revised(build, slots=[s.model_dump(mode="json") for s in state.slots])
        report = calculate(candidate, catalog, rules)
        if report.errors or any(report.totals[v.stat] < v.value for v in constraints.minimums):
            continue
        unknown_cost = [r.item.template_ref for r in candidate.slots if r.item.template_ref not in prices]
        cost = sum(prices.get(r.item.template_ref, 0) for r in candidate.slots) if not unknown_cost else None
        if constraints.budget is not None and cost is not None and cost > constraints.budget:
            continue
        complete = (report.equipability == "valid" and report.completeness == "complete"
                    and all(report.known(v.stat) for v in constraints.minimums)
                    and (constraints.budget is None or cost is not None))
        payload = {"build": candidate.model_dump(mode="json"), "report": report.model_dump(mode="json"),
                   "constraints_verified": complete, "cost": cost, "price_context": constraints.price_context,
                   "unknown_prices": unknown_cost if constraints.budget is not None else [],
                   "score": report.totals[constraints.objective]}
        ranked.append((complete, payload))
    ranked.sort(key=lambda row: (not row[0], -row[1]["score"], tuple((s["slot"], s["item"]["template_ref"], s["item"]["mode"]) for s in row[1]["build"]["slots"])))
    seen = set()
    for complete, row in ranked:
        signature = tuple((s["slot"], s["item"]["template_ref"]) for s in row["build"]["slots"])
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
