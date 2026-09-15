"""Propositions de huit emplacements, explicables et calculées sans modèle.

Pas de prix HDV, solveur de panoplies, exos ou validation complète des conditions.
Les données proviennent exclusivement de l'index d'identités rapprochées.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction

from utils.dofus_wiki import search_key
from utils.evo_config import EvoError
from utils.evo_equipment import STAT_NAMES, STAT_LABELS

SLOTS = ("coiffe", "cape", "amulette", "ceinture", "bottes", "anneau", "anneau", "arme")
_SLOT = {
    "chapeau": "coiffe", "cape": "cape", "sac a dos": "cape",
    "amulette": "amulette", "ceinture": "ceinture", "botte": "bottes",
    "bottes": "bottes", "anneau": "anneau", "bouclier": "bouclier",
    "familier": "familier", "dofus": "dofus",
}
for _weapon in ("arc", "arbalete", "arme magique", "baguette", "baton", "dague",
                "epee", "faux", "hache", "marteau", "outil", "pelle", "pioche"):
    _SLOT[_weapon] = "arme"

LIMITS = (
    "Sommes des seuls jets naturels interprétés, pas statistiques finales du personnage. "
    "Hors bonus de panoplie, caractéristiques de base, parchottage, familiers non fournis et exos. "
    "Conditions, restrictions de classe, armes à deux mains et effets inconnus non validés. "
    "Prix, disponibilité HDV et optimalité globale inconnus."
)


def slot_of(row):
    return _SLOT.get(search_key(row.entry.category))


def summarize(rows, *, level):
    rows = tuple(rows)
    if not rows:
        raise EvoError("Il faut au moins un équipement vérifié.")
    if len({row.entry.token for row in rows}) != len(rows):
        raise EvoError("Liste avec doublon : fournis chaque référence une seule fois.")
    if any(type(row.entry.level) is not int or not 1 <= row.entry.level <= level for row in rows):
        raise EvoError("Un objet dépasse le niveau indiqué ou son niveau n'est pas vérifié.")
    counts = Counter(slot_of(row) for row in rows)
    if None in counts:
        raise EvoError("La liste contient une catégorie non prise en charge comme équipement.")
    if any(count > (2 if slot == "anneau" else 6 if slot == "dofus" else 1)
           for slot, count in counts.items()):
        raise EvoError("Trop d'objets pour un même emplacement dans cette analyse.")
    totals = {}
    pieces = []
    partial = False
    for row in rows:
        partial |= bool(row.unsupported)
        for key, bounds in row.bounds.items():
            total = totals.setdefault(STAT_LABELS.get(key, key), [0, 0])
            total[0] += bounds[0]
            total[1] += bounds[1]
        pieces.append({
            "emplacement": slot_of(row), "objet": row.entry.name,
            "reference": row.entry.token, "niveau": row.entry.level, "source": row.entry.url,
            "conditions_a_verifier": bool(row.conditions),
            "panoplie_non_incluse": bool(row.panoplie),
            "effets_non_interpretes": bool(row.unsupported),
        })
    missing = Counter(SLOTS) - counts
    return {
        "pieces": pieces, "niveau_personnage": level, "nombre_pieces": len(pieces),
        "somme_jets_interpretes": totals, "somme_partielle": partial,
        "emplacements_manquants": dict(missing),
        "equipabilite_confirmee": False, "limites": LIMITS,
    }


def suggest(rows, *, level, priorities, no_malus):
    """Greedy per-slot ranking, normalized per stat; never claims global optimality."""
    if not priorities or len(set(priorities)) != len(priorities):
        raise EvoError("Indique une à trois priorités distinctes, dans l'ordre.")
    keys = [STAT_NAMES[name] for name in priorities]
    forbidden = [STAT_NAMES[name] for name in no_malus]
    eligible = [
        row for row in rows if type(row.entry.level) is int and 1 <= row.entry.level <= level
        and slot_of(row) in SLOTS and not row.unsupported
        and all(row.bounds.get(key, (0, 0))[0] >= 0 for key in forbidden)
    ]
    selected, used, diagnostics = [], set(), {}
    for slot in SLOTS:
        pool = [row for row in eligible if slot_of(row) == slot and row.entry.token not in used]
        diagnostics[slot] = len(pool)
        if not pool:
            continue
        # Secondary priorities are preferences, not a requirement on every piece.
        scales = {key: max(1, *(abs(row.bounds.get(key, (0, 0))[1]) for row in pool))
                  for key in keys}

        def score(row):
            return sum(
                (len(keys) - index) * Fraction(row.bounds.get(key, (0, 0))[1], scales[key])
                for index, key in enumerate(keys)
            )

        ranked = sorted(pool, key=lambda row: (
            -score(row), bool(row.conditions), -row.entry.level, row.entry.token,
        ))
        # Do not pad a build with irrelevant zero-score pieces.
        if score(ranked[0]) <= 0:
            continue
        selected.append(ranked[0])
        used.add(ranked[0].entry.token)
    if not selected:
        return {"pieces": [], "message": "Aucune base vérifiée ne correspond à ces priorités.",
                "limites": LIMITS}
    return {
        **summarize(selected, level=level),
        "priorites": priorities, "sans_malus": no_malus,
        "methode": (
            "Proposition heuristique de 8 emplacements, deux anneaux distincts. "
            "Par emplacement, somme pondérée des jets maximums normalisés par leur amplitude ; "
            "poids décroissants dans l'ordre des priorités. Pas d'optimisation de panoplie. "
            "Les pièces avec effets non interprétés sont exclues ; conditions à vérifier sur les fiches."
        ),
        "candidats_par_emplacement": diagnostics,
    }
