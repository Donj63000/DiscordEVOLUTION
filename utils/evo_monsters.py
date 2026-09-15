"""Je normalise les grades du wiki et calcule leurs plages sans compléter les inconnues."""
from __future__ import annotations

import logging


log = logging.getLogger(__name__)
RESISTANCES = {
    "neutral": "neutre", "earth": "terre", "fire": "feu", "water": "eau", "air": "air",
}
STAT_FIELDS = {"pv": "hp", "pa": "ap", "pm": "mp"}


def integer(value, minimum=0):
    return value if type(value) is int and minimum <= value <= 1_000_000_000 else None


def bounds(values):
    known = [value for value in values if value is not None]
    return [min(known), max(known)] if known else None


def monster_statistics(data):
    """Les zéros collectifs PV/PA/PM du wiki signalent une absence, pas des valeurs de jeu."""
    raw_grades = data.get("grades", [])
    raw_grades = raw_grades if isinstance(raw_grades, list) else []
    grades = []
    for row in raw_grades[:8]:
        if not isinstance(row, dict):
            continue
        statistics = {name: integer(row.get(source)) for name, source in STAT_FIELDS.items()}
        if all(value in (None, 0) for value in statistics.values()):
            statistics = dict.fromkeys(STAT_FIELDS)
        raw_resistances = row.get("resist")
        raw_resistances = raw_resistances if isinstance(raw_resistances, dict) else {}
        grades.append({
            "grade": integer(row.get("grade"), 1), "niveau": integer(row.get("level"), 1),
            **statistics,
            "resistances_pourcent": {
                label: integer(raw_resistances.get(source), -1_000_000_000)
                for source, label in RESISTANCES.items()
            },
        })
    levels = bounds([row["niveau"] for row in grades])
    stat_ranges = {name: bounds([row[name] for row in grades]) for name in STAT_FIELDS}
    resistance_ranges = {
        label: bounds([row["resistances_pourcent"][label] for row in grades])
        for label in RESISTANCES.values()
    }
    missing = [name.upper() for name, interval in stat_ranges.items() if interval is None]
    missing += [f"résistance {name}" for name, interval in resistance_ranges.items() if interval is None]
    partial = [name.upper() for name in STAT_FIELDS
               if stat_ranges[name] is not None and any(row[name] is None for row in grades)]
    partial += [f"résistance {name}" for name in RESISTANCES.values()
                if resistance_ranges[name] is not None and any(
                    row["resistances_pourcent"][name] is None for row in grades
                )]
    if levels is None:
        level_text = "Niveau non renseigné dans les grades disponibles."
    elif levels[0] == levels[1]:
        level_text = f"Niveau {levels[0]}."
    else:
        level_text = f"Niveau {levels[0]} à {levels[1]} selon le grade."
    incomplete = len(raw_grades) != len(grades) or any(row["niveau"] is None for row in grades)
    log.debug("evo monster statistics normalized grades=%s missing=%s partial=%s",
              len(grades), len(missing), len(partial))
    return {
        "grades": grades, "nombre_grades_renseignes": len(grades),
        "niveau_affiche": level_text, "plage_niveau": levels,
        "plages_pv_pa_pm": stat_ranges, "plages_resistances_pourcent": resistance_ranges,
        "donnees_non_renseignees": missing, "donnees_partielles": partial,
        "grades_incomplets": incomplete,
        "lecture_statistiques": (
            "Le niveau décrit le monstre, pas un nombre de niveaux possédés. Les plages sont calculées "
            "en Python sur les grades renseignés ; chaque ligne donne les valeurs du même grade. "
            "Les résistances sont en pourcentage, y compris négatif. PV/PA/PM absents restent inconnus."
        ),
    }
