"""Index d'effets compact et classements explicables, sans IA ni prix inventés."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import combinations
import logging
import time

from utils.dofus_wiki import EQUIPMENT_ALIASES, EQUIPMENT_TYPES, search_key
from utils.evo_config import EvoError
from utils.exo_data import is_mageable, parse_effects
from utils.exo_engine import STATS
from utils.xixou_api import equipment_category, equipment_records, identity_key

STAT_NAMES = {
    "force": "fo", "vitalite": "vi", "sagesse": "sa", "intelligence": "ine",
    "agilite": "age", "chance": "cha", "prospection": "pp", "portee": "po",
    "pa": "pa", "pm": "pm", "dommages": "do", "critiques": "cc",
}
STAT_LABELS = {key: stat.name for key, stat in STATS.items()}
log = logging.getLogger(__name__)


def category_name(value: str | None) -> str | None:
    if not value:
        return None
    key = search_key(value)
    if not key:
        return None
    alias = EQUIPMENT_ALIASES.get(key)
    normalized = equipment_category(key)
    for category in EQUIPMENT_TYPES:
        if alias == category or equipment_category(category) == normalized:
            return category
    raise EvoError("Type d'équipement inconnu. Exemple : coiffe, cape, anneau, bottes.")


@dataclass(frozen=True)
class Equipment:
    entry: object
    bounds: dict
    unsupported: tuple
    conditions: tuple
    panoplie: tuple
    effects: tuple

    def payload(self) -> dict:
        return {
            "objet": self.entry.name, "reference": self.entry.token,
            "type": self.entry.category, "niveau": self.entry.level,
            "jets_naturels": {STAT_LABELS.get(k, k): list(v) for k, v in self.bounds.items()},
            "effets_non_interpretes": list(self.unsupported),
            "conditions": list(self.conditions), "panoplie": list(self.panoplie),
            "source": self.entry.url,
        }


def build_index(entries, catalog):
    by_id = {entry.identifier: entry for entry in entries if entry.kind == "item"}
    by_identity = {}
    for entry in entries:
        identity = (identity_key(entry.name), entry.level, equipment_category(entry.category))
        by_identity.setdefault(identity, []).append(entry)
    selected, conflicting = {}, set()
    count = unmatched = 0
    for row in equipment_records(catalog):
        count += 1
        identity = (identity_key(row["name"]), row["level"], row["category"])
        matches = ([by_id[str(row["id"])]] if str(row["id"]) in by_id
                   else by_identity.get(identity, []) if row["id"] is None else [])
        matches = [entry for entry in matches if (
            identity_key(entry.name), entry.level, equipment_category(entry.category)
        ) == identity]
        if len(matches) != 1:
            unmatched += 1
            continue
        entry = matches[0]
        bounds, unsupported, _ = parse_effects(row["effects"])
        if not bounds:
            unmatched += 1
            continue
        equipment = Equipment(entry, bounds, unsupported, row["conditions"], row["panoplie"], row["effects"])
        previous = selected.get(entry.token)
        if previous is not None and previous != equipment:
            conflicting.add(entry.token)
        selected[entry.token] = equipment
    for token in conflicting:
        selected.pop(token, None)
    return tuple(selected.values()), {
        "lignes_catalogue": count, "objets_indexes": len(selected),
        "non_rapproches_ou_sans_stats": unmatched, "identites_ambigues": len(conflicting),
        "catalogue_genere_le": catalog.get("genere_le", ""),
        "portee": "Catalogue Xixou rapproché du wiki, pas garantie d'exhaustivité du jeu.",
    }


class EquipmentIndex:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.rows = ()
        self.info = {}
        self.expires = 0.0

    async def get(self, wiki):
        if time.monotonic() < self.expires and self.rows:
            return self.rows, self.info
        async with self.lock:
            if time.monotonic() < self.expires and self.rows:
                return self.rows, self.info
            client = wiki.enrichment_client
            if client is None or not client.enabled:
                raise EvoError(
                    "Le classement par caractéristiques nécessite l'enrichissement Xixou. "
                    "Les fiches d'objets nommés restent consultables ; le Staff peut vérifier XIXOU_API_KEY."
                )
            entries, catalog = await asyncio.gather(wiki.client.items(), client.catalog("equipements"))
            if not catalog:
                raise EvoError("Le catalogue d'effets est momentanément indisponible.")
            rows, info = await asyncio.to_thread(build_index, entries, catalog)
            if not rows:
                raise EvoError("Aucun équipement rapproché avec certitude ; je ne vais pas inventer un classement.")
            self.rows, self.info = rows, info
            self.expires = time.monotonic() + 900
            return rows, info


def equipment_search(rows, *, category, min_level, max_level, priorities, no_malus, query):
    category = category_name(category)
    if min_level > max_level:
        raise EvoError("Le niveau minimum doit être inférieur au niveau maximum.")
    keys = [STAT_NAMES[k] for k in priorities]
    forbidden = [STAT_NAMES[k] for k in no_malus]
    category_key = equipment_category(category) if category else None
    words = search_key(query).split()
    rows = tuple(rows)
    typed = [row for row in rows if category_key is None
             or equipment_category(row.entry.category) == category_key]
    leveled = [row for row in typed if type(row.entry.level) is int
               and min_level <= row.entry.level <= max_level]
    named = [row for row in leveled if all(word in search_key(row.entry.name) for word in words)]
    prioritized = [row for row in named if all(row.bounds.get(key, (0, 0))[1] > 0 for key in keys)]
    selected = [row for row in prioritized if all(row.bounds.get(key, (0, 0))[0] >= 0 for key in forbidden)]
    selected.sort(key=lambda row: (
        tuple(-row.bounds.get(key, (0, 0))[1] for key in keys),
        row.entry.level, row.entry.name,
    ))
    counts = {
        "objets_indexes": len(rows), "type": len(typed), "niveau": len(leveled),
        "nom": len(named), "priorites": len(prioritized), "sans_malus": len(selected),
    }
    blocked_filter = next((name for name, count in counts.items() if count == 0), None)
    log.debug("evo equipment search counts=%s blocked_filter=%s", counts, blocked_filter)
    result = {
        "resultats": [row.payload() for row in selected[:5]],
        "correspondances": len(selected),
        "classement": "Tri lexicographique sur les jets maximums, dans l'ordre des priorités demandées.",
        "priorites": priorities,
        "filtres_appliques": {
            "type_objet": category, "niveau_min": min_level, "niveau_max": max_level,
            "nom_contient": query, "sans_malus": no_malus,
        },
        "diagnostic_filtres": counts, "premier_filtre_sans_resultat": blocked_filter,
        "limites": "Jets naturels seuls ; conditions et panoplies à vérifier. Aucun prix ni optimisation globale de stuff.",
    }
    if not selected and min_level == max_level:
        result["precision_niveau"] = (
            "Cette recherche impose un niveau d'objet exact. Le niveau d'un personnage est "
            "normalement un plafond ; une tranche explicitement demandée doit rester respectée."
        )
    return result


def compare_equipment(payloads):
    """Je calcule les écarts de jets comparables sans assimiler une donnée absente à zéro."""
    comparisons = []
    for first, second in combinations(payloads, 2):
        first_stats, second_stats = first.get("jets_naturels", {}), second.get("jets_naturels", {})
        differences = {}
        complete = bool(first_stats and second_stats) and not (
            first.get("effets_non_interpretes") or second.get("effets_non_interpretes")
        )
        for name in sorted(first_stats.keys() | second_stats.keys()):
            left, right = first_stats.get(name), second_stats.get(name)
            if not complete and (left is None or right is None):
                continue
            left, right = left or [0, 0], right or [0, 0]
            differences[name] = {
                "ecart_min": left[0] - right[0], "ecart_max": left[1] - right[1],
                "avantage_jet_max": (
                    first["reference"] if left[1] > right[1] else
                    second["reference"] if right[1] > left[1] else "egalite"
                ),
            }
        comparisons.append({
            "premier": first["reference"], "second": second["reference"],
            "ecarts_premier_moins_second": differences,
            "comparaison_complete": complete,
        })
    log.debug("evo equipment compared pairs=%s", len(comparisons))
    return comparisons


def exo_candidates(rows, *, target, category, min_level, max_level, references=()):
    category = category_name(category)
    category_key = equipment_category(category) if category else None
    if min_level > max_level:
        raise EvoError("Tranche de niveaux invalide.")
    requested = set(references)
    selected = [row for row in rows if not requested or row.entry.token in requested]
    excluded = []
    if requested:
        for row in selected:
            reasons = []
            if not is_mageable(row.entry):
                reasons.append("Objet non forgeable dans le moteur.")
            if target in row.bounds:
                reasons.append("Ce bonus est déjà natif : ce ne serait pas un exo.")
            if row.unsupported:
                reasons.append("Des effets non interprétés empêchent un classement fiable.")
            if not min_level <= row.entry.level <= max_level or (
                category_key and equipment_category(row.entry.category) != category_key
            ):
                reasons.append("Objet hors des filtres demandés.")
            if reasons:
                excluded.append({"objet": row.entry.name, "reference": row.entry.token, "raisons": reasons})
        for reference in sorted(requested - {row.entry.token for row in selected}):
            excluded.append({"reference": reference, "raisons": ["Effets vérifiés indisponibles dans l'index."]})
    candidates = [
        row for row in selected if is_mageable(row.entry)
        and min_level <= row.entry.level <= max_level
        and (category_key is None or equipment_category(row.entry.category) == category_key)
        and target not in row.bounds
        and not row.unsupported
    ]
    def complexity(row):
        return (
            len(row.bounds),
            sum(1 for key in row.bounds if STATS[key].weight >= 10),
            row.entry.level, row.entry.name,
        )
    candidates.sort(key=complexity)
    results = []
    for row in candidates[:5]:
        value = row.payload()
        value["lignes_a_entretenir"] = len(row.bounds)
        value["lignes_lourdes"] = [STATS[key].name for key in row.bounds if STATS[key].weight >= 10]
        results.append(value)
    return {
        "resultats": results, "candidats": len(candidates), "objectif": target.upper(),
        "objets_demandes": list(references), "exclus": excluded,
        "methode": "Heuristique : moins de lignes, puis moins de lignes de poids nominal ≥ 10.",
        "limites": (
            "Un bonus natif n'est pas un exo. Un remontage plus simple n'augmente pas le taux de passage. "
            "Ce n'est PAS un classement de coût ni de probabilité ; aucun prix des runes connu. "
            "Aucun score pseudo-précis /10. Le jet cible réel peut inverser ce classement."
        ),
    }
