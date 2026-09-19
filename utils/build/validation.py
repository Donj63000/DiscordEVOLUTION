"""Validité structurelle et état final : jamais une suppression récursive d'objets."""
from collections import Counter
from .models import BuildError, Diagnostic, CLASSES
from .conditions import parse, evaluate, Truth


def validate(build, catalog, rules, totals, unknown):
    errors, warnings = [], []
    resolved = [(row.slot, catalog.resolve(row.item)) for row in build.slots]
    counts = Counter(item.ref for _, item in resolved)
    context = {k: v for k, v in totals.items() if k not in unknown}
    context.update(level=build.profile.level, class_id=CLASSES.index(build.profile.classe) + 1,
                   grade=build.profile.grade, alignment=build.profile.alignment)
    if rules.stat_conditions != "final":
        context = {k: v for k, v in context.items() if k in {"level", "class_id", "grade", "alignment"}}
    for slot, item in resolved:
        if slot not in item.allowed_slots:
            raise BuildError("Cet objet ne correspond pas à l'emplacement choisi.")
        if item.level > build.profile.level:
            errors.append(Diagnostic(code="LEVEL", text=f"{item.name} exige le niveau {item.level}.", origin=slot))
        if counts[item.ref] > 1:
            if item.unique is True:
                errors.append(Diagnostic(code="DUPLICATE", text=f"{item.name} ne peut pas être équipé plusieurs fois.", origin=slot))
            elif item.unique is None:
                warnings.append(Diagnostic(code="DUPLICATE_UNKNOWN", text=f"Restriction de doublon non vérifiée : {item.name}.", origin=slot))
        if not item.conditions_known:
            warnings.append(Diagnostic(code="CONDITIONS_MISSING", text=f"Conditions non certifiées : {item.name}.", origin=slot))
        for condition in item.conditions:
            result = evaluate(parse(condition), context)
            if result is Truth.FALSE:
                errors.append(Diagnostic(code="CONDITION", text=condition, origin=slot))
            elif result is Truth.UNKNOWN:
                warnings.append(Diagnostic(code="CONDITION_UNKNOWN", text=condition, origin=slot))
    weapon = next((item for slot, item in resolved if slot == "arme"), None)
    if weapon and any(slot == "bouclier" for slot, _ in resolved):
        if weapon.two_handed is True:
            errors.append(Diagnostic(code="TWO_HANDED", text="Arme déclarée à deux mains incompatible avec le bouclier."))
        elif weapon.two_handed is None:
            warnings.append(Diagnostic(code="HANDS_UNKNOWN", text="Compatibilité arme/bouclier non renseignée."))
    if not rules.restrictions_verified:
        warnings.append(Diagnostic(code="RULE_COVERAGE", text="Règles de répétition, exos et séquence d'équipement non entièrement validées en jeu."))
    if any(row.item.mode == "declared_fm" for row in build.slots):
        warnings.append(Diagnostic(code="DECLARED_FM", text="FM déclarée : réalisation, coût et possession non certifiés."))
    return errors, warnings
