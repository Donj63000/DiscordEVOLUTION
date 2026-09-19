"""Fonction pure commune aux vues, images, sauvegardes, comparaisons et solveur."""
from collections import defaultdict
from . import ENGINE_VERSION
from .models import (Build, Catalog, Report, Metric, Contribution, Diagnostic, STAT_LABELS,
                     BuildError)
from .rules import character, derive, Rules
from .effects import resolve_values, affected_stats
from .validation import validate


def calculate(build: Build, catalog: Catalog, rules: Rules) -> Report:
    if (build.catalog_id, build.rules_id) != (catalog.id, rules.id):
        raise BuildError("Versions de catalogue/règles incompatibles avec le build.")
    ledger, unknown, warnings = character(build.profile, rules)
    base = {c.stat: c.value for c in ledger}
    set_members = defaultdict(set)
    for row in sorted(build.slots, key=lambda r: r.slot):
        item = catalog.resolve(row.item)
        contributions, unsupported = resolve_values(item, row.item, row.slot)
        ledger.extend(contributions)
        for effect in unsupported:
            # Impact non classé : impossible de certifier quelles statistiques seraient épargnées.
            unknown.update(affected_stats(effect))
            warnings.append(Diagnostic(code="EFFECT_UNKNOWN", text=effect.text, origin=row.slot))
        pet_stats = {effect.stat for effect in item.effects
                     if effect.kind == "stat" and effect.low != effect.high}
        if item.category == "familier" and len(pet_stats) > 1:
            warnings.append(Diagnostic(code="PET_BUDGET", origin=row.slot,
                                       text="Bonus de familier liés au nourrissage : renseigner les jets réels ; maxima simultanés non certifiés."))
            if row.item.mode == "natural_best":
                unknown.update(pet_stats)
        warnings.extend(Diagnostic(code="ITEM_COVERAGE", text=w, origin=row.slot) for w in item.warnings)
        if item.warnings:
            unknown.update(STAT_LABELS)
        if item.set_ref:
            set_members[item.set_ref].add(item.ref)
    for ref, members in sorted(set_members.items()):
        definition = catalog.by_set.get(ref)
        tier = next((t for t in definition.tiers if t.pieces == len(members)), None) if definition else None
        if tier is None:
            unknown.update(STAT_LABELS)
            warnings.append(Diagnostic(code="SET_MISSING", text=f"Bonus TOTAL de panoplie absent : {ref}, {len(members)} pièce(s)."))
            continue
        for effect in tier.effects:
            if effect.kind == "stat":
                ledger.append(Contribution(origin=f"panoplie:{ref}:{len(members)}", stat=effect.stat, value=effect.high))
            elif effect.kind != "cosmetic":
                unknown.update(STAT_LABELS)
                warnings.append(Diagnostic(code="SET_EFFECT_UNKNOWN", text=effect.text, origin=ref))
    totals = {s: 0 for s in STAT_LABELS}
    for c in ledger:
        totals[c.stat] = totals.get(c.stat, 0) + c.value
    ledger.extend(derive(totals, base, build.profile, rules, unknown))
    errors, validity_warnings = validate(build, catalog, rules, totals, unknown)
    warnings.extend(validity_warnings)
    # Ne pas confondre somme partielle et absence d'équipement (un brouillon vide reste autorisé).
    blocking_unknown = unknown - {"ini", "pod"}
    equipability = "invalid" if errors else "unknown" if validity_warnings or blocking_unknown else "valid"
    metrics = tuple(Metric(stat=s, value=totals[s], status="partial" if s in unknown else "known") for s in STAT_LABELS)
    return Report(metrics=metrics, contributions=tuple(ledger), errors=tuple(errors), warnings=tuple(warnings),
                  equipability=equipability, completeness="partial" if unknown else "complete",
                  nature="declared" if build.profile.mode == "declared" or any(r.item.mode == "declared_fm" for r in build.slots) else "natural",
                  catalog_id=catalog.id, rules_id=rules.id, engine=ENGINE_VERSION)
