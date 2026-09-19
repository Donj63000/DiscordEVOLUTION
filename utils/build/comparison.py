"""Deux rapports recalculés ; aucune somme parallèle ni faux vainqueur universel."""
from .models import STAT_LABELS


def equipment_content(instance):
    # Deux exemplaires copiés ont des UUID distincts sans changement d'équipement.
    return instance.model_dump(mode="json", exclude={"id"}) if instance is not None else None


def compare(first, report_a, second, report_b):
    return {
        "versions_comparables": (first.catalog_id, first.rules_id) == (second.catalog_id, second.rules_id),
        "profil_identique": first.profile == second.profile,
        "ecarts": [{"stat": stat, "libelle": STAT_LABELS[stat], "premier": report_a.totals[stat],
                     "second": report_b.totals[stat], "delta": report_b.totals[stat] - report_a.totals[stat],
                     "complet": report_a.known(stat) and report_b.known(stat)} for stat in STAT_LABELS
                    if report_a.totals[stat] != report_b.totals[stat]],
        "emplacements_changes": sorted(s for s in ({r.slot for r in first.slots} | {r.slot for r in second.slots}) if equipment_content(first.in_slot(s)) != equipment_content(second.in_slot(s))),
        "validation_avant": report_a.equipability, "validation_apres": report_b.equipability,
        "avertissements": [w.text for w in report_a.warnings + report_b.warnings],
    }


def comparison_text(first, report_a, second, report_b):
    """Version lisible, directement dérivée du contrat de comparaison commun."""
    from .editor import SLOT_LABELS
    from .renderer import EQUIPABILITY_LABELS
    result = compare(first, report_a, second, report_b)
    lines = [f"COMPARAISON — {first.name} -> {second.name}",
             "Les différences décrivent le deuxième build moins le premier."]
    if not result['versions_comparables']:
        lines.append("ATTENTION : versions de règles ou de catalogue différentes ; migrer explicitement avant une comparaison à version identique.")
    if not result['profil_identique']:
        lines.append("Les profils de personnages sont différents : ce n'est pas seulement un changement d'équipement.")
    lines += ["", f"Contrôles avant : {EQUIPABILITY_LABELS[result['validation_avant']]}",
              f"Contrôles après : {EQUIPABILITY_LABELS[result['validation_apres']]}", "", "CARACTÉRISTIQUES"]
    lines += [f"{row['libelle']} : {row['premier']} -> {row['second']} ({row['delta']:+d})"
              + (" * partiel" if not row['complet'] else "") for row in result['ecarts']]
    if not result['ecarts']:
        lines.append("Aucun écart parmi les sommes calculées. Cela ne certifie pas les valeurs inconnues.")
    lines += ["", "EMPLACEMENTS MODIFIÉS", ", ".join(SLOT_LABELS[s] for s in result['emplacements_changes']) or "Aucun."]
    lines += ["", "RÉSERVES", *dict.fromkeys(result['avertissements'])]
    return "\n".join(lines)
